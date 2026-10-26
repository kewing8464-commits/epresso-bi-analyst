"""Assistant: a dispatcher plus two routers.
The router (a language model, or keyword rules as the fallback) only CHOOSES a tool
and its arguments. Our code RUNS the tool. Nothing here executes model-written code,
and no tool can change the data."""
from __future__ import annotations

import json
import re

import analytics
from analytics import Filters, ToolResult

DEFAULT_MODEL = 'gemini-2.5-flash'
MAX_TOOL_ROUNDS = 5

# ------------------------------------------------------------- tool registry
FILTER_SCHEMA = {
    'type': 'object',
    'description': 'Optional subset. Leave a field out to include everything for that field.',
    'properties': {
        'years': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'e.g. [2028]'},
        'channels': {'type': 'array', 'items': {'type': 'string', 'enum': [analytics.WEBSITE,
                                                                         analytics.BRICK]}},
        'regions': {'type': 'array', 'items': {'type': 'string'},
                    'description': 'Store regions, e.g. West, Midwest'},
        'stores': {'type': 'array', 'items': {'type': 'string'},
                   'description': 'Store IDs (SH00-SH20) or a city that has only one store'},
        'categories': {'type': 'array', 'items': {'type': 'string'},
                       'description': 'Category names, e.g. Beans, Syrups'},
        'products': {'type': 'array', 'items': {'type': 'string'}},
    },
}

TOOLS = {
    'get_revenue_total': {
        'fn': analytics.get_revenue_total,
        'params': {'filters'},
        'uses_dashboard_filters': True,
        'description': 'Total revenue for all data or a subset (year, channel, store, region, '
                       'category, product). Also returns the period and average monthly revenue.',
        'schema': {'type': 'object', 'properties': {'filters': FILTER_SCHEMA}},
    },
    'get_revenue_breakdown': {
        'fn': analytics.get_revenue_breakdown,
        'params': {'dimension', 'filters', 'top_n'},
        'uses_dashboard_filters': True,
        'description': 'Revenue split by one dimension (channel, year, month, year_month, store, '
                       'category, product, region) with each group\'s share of the subset total '
                       'and its rank. Use for comparisons, rankings and "which is highest".',
        'schema': {'type': 'object', 'required': ['dimension'], 'properties': {
            'dimension': {'type': 'string', 'enum': list(analytics.BREAKDOWN_DIMS)},
            'filters': FILTER_SCHEMA,
            'top_n': {'type': 'integer', 'description': 'Only list the top N groups'}}},
    },
    'get_revenue_growth': {
        'fn': analytics.get_revenue_growth,
        'params': {'dimension', 'start_year', 'end_year', 'filters'},
        'uses_dashboard_filters': True,
        'description': 'Revenue change between two years (defaults: first and last year in the data) '
                       'for the total or for each channel, store, category, product or region, '
                       'ranked by % change. Use for growth, decline and trend questions.',
        'schema': {'type': 'object', 'properties': {
            'dimension': {'type': 'string', 'enum': list(analytics.GROWTH_DIMS)},
            'start_year': {'type': 'integer'}, 'end_year': {'type': 'integer'},
            'filters': FILTER_SCHEMA}},
    },
    'get_monthly_revenue_share': {
        'fn': analytics.get_monthly_revenue_share,
        'params': {'filters'},
        'uses_dashboard_filters': True,
        'description': 'Each month\'s revenue as a percent of the same year\'s total, plus the '
                       'average share per calendar month. Use for seasonality questions.',
        'schema': {'type': 'object', 'properties': {'filters': FILTER_SCHEMA}},
    },
    'get_satisfaction_vs_revenue': {
        'fn': analytics.get_satisfaction_vs_revenue,
        'params': {'filters'},
        'uses_dashboard_filters': True,
        'description': 'Correlation between annual survey satisfaction / interest and annual store '
                       'revenue (store-year level), with highest and lowest scoring stores.',
        'schema': {'type': 'object', 'properties': {'filters': FILTER_SCHEMA}},
    },
    'get_data_quality_report': {
        'fn': analytics.get_data_quality_report,
        'params': set(),
        'uses_dashboard_filters': False,
        'description': 'Missing values, duplicates, unmatched keys and join checks for the loaded files.',
        'schema': {'type': 'object', 'properties': {}},
    },
    'get_data_coverage': {
        'fn': analytics.get_data_coverage,
        'params': set(),
        'uses_dashboard_filters': False,
        'description': 'What the loaded data contains (years, stores with IDs and cities, '
                       'categories, products) and what it does NOT contain (costs, profit, '
                       'customers...). Call this when unsure whether a question can be answered.',
        'schema': {'type': 'object', 'properties': {}},
    },
}

UNSUPPORTED = ('I can only answer questions that my approved analytical functions can '
               'calculate from the loaded data: ' + ', '.join(TOOLS) + '. '
               'Try asking about revenue totals, channels, stores, categories, growth between '
               'years, monthly patterns, survey satisfaction, or data quality.')

SYSTEM_PROMPT = """You are an analytical assistant for E-Presso managers, inside a BI app.
E-Presso sells espresso products through a website (store SH00) and physical stores (SH01-SH20).

Rules:
- Only state numbers that appear in a tool result in THIS turn. Never estimate, recall, or
  compute new figures yourself (no adding, subtracting or dividing tool numbers). If you need a
  number you do not have, call another tool.
- Always call at least one tool before answering a question about the data.
- If a question needs data that is not available (profit, costs, customers, prices...), call
  get_data_coverage, say clearly what is missing, and only then offer revenue facts as context.
- Revenue is not profit. Correlation is not causation. Say so when relevant.
- Do not recommend closing, opening, or investing in anything as if revenue alone decides it.
  Name the extra information management would need.
- Never ask for, write, or run code. Never claim to modify the data.
- If a tool returns an error, explain it plainly and suggest a valid alternative.

Answer in this exact format (Markdown), starting with the scope:
**Scope:** which data the numbers cover, e.g. "all stores, all years" or "Chicago (SH11), 2027".
  Add "(from the sidebar filter)" when the app note says sidebar filters are applied.
**Findings:** facts taken directly from tool results, with the numbers.
**Interpretation:** what the findings might mean, clearly an interpretation, not a fact.
**Limitations:** the relevant caveats from the tool results.
**Next steps:** a cautious next step, or exactly "No recommendation is supported by this data."
  when the data cannot support one (always for profit, closing/opening, and causation questions,
  followed by the information management would need).
Keep it under 220 words. Format money like $1,234,567 and percents like 12.3%."""


# ---------------------------------------------------------------- dispatcher
def run_tool(name: str, args: dict, model, log: list, base: Filters | None = None) -> ToolResult:
    """The dispatcher: validate the request, run the function, log the call."""
    args = dict(args or {})
    if name not in TOOLS:
        result = analytics._error(name, f'Unknown tool: {name}')
    else:
        unexpected = set(args) - TOOLS[name]['params']
        if unexpected:
            result = analytics._error(name, f'Unexpected argument(s): {sorted(unexpected)}')
        else:
            kwargs = dict(args)
            if TOOLS[name]['uses_dashboard_filters']:
                kwargs['base'] = base
            try:
                result = TOOLS[name]['fn'](model, **kwargs)
            except (TypeError, ValueError) as exc:   # bad argument types from a router
                result = analytics._error(name, f'Invalid arguments: {exc}')
    entry = {'tool': name, 'arguments': args, 'result': result.to_dict()}
    if base is not None and not base.is_empty() and name in TOOLS and TOOLS[name]['uses_dashboard_filters']:
        entry['sidebar_filters_applied'] = base.to_dict()    # shown in the log, never hidden
    log.append(entry)
    return result


# ------------------------------------------------------- number verification
_NUM = re.compile(r'(?<![A-Za-z_\d.])(\$)?(-?\d[\d,]*(?:\.\d+)?)\s*(%|percent|million|billion|'
                  r'thousand|[mMkK]\b|bn\b)?')
_SCALE = {'million': 1e6, 'm': 1e6, 'billion': 1e9, 'bn': 1e9, 'thousand': 1e3, 'k': 1e3}


def _collect_numbers(obj, out: set):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.add(abs(float(obj)))
    elif isinstance(obj, str):
        for m in re.finditer(r'\d[\d,]*(?:\.\d+)?', obj):
            out.add(float(m.group().replace(',', '')))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _collect_numbers(k, out)
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def verify_numbers(text: str, tool_log: list, question: str = '') -> dict:
    """Check that every number in an answer appears in a tool result from this turn
    (allowing for rounding, e.g. $42,174,440 -> $42.2 million). Numbers that cannot be
    traced are reported to the user as unverified."""
    known: set = {0.0, 5.0, 100.0}
    for entry in tool_log:
        _collect_numbers(entry['result'].get('value'), known)
        _collect_numbers(entry['result'].get('scope'), known)
        _collect_numbers(entry['result'].get('caveats'), known)
        _collect_numbers(entry['result'].get('rows_used'), known)
    _collect_numbers(question, known)
    checked, unverified = 0, []
    for m in _NUM.finditer(text):
        dollar, raw, unit = m.group(1), m.group(2), (m.group(3) or '').lower()
        digits = raw.replace(',', '').lstrip('-')
        try:
            v = float(digits)
        except ValueError:
            continue
        if not dollar and not unit and '.' not in digits and v <= 12:
            continue          # small counts like "3 stores" or "top 5"
        decimals = len(digits.split('.')[1]) if '.' in digits else 0
        scale = _SCALE.get(unit, 1.0)
        value = v * scale
        tol = 0.5 * 10 ** (-decimals) * scale + 1e-9
        checked += 1
        if not any(abs(value - k) <= tol for k in known):
            unverified.append(m.group(0).strip())
    return {'numbers_checked': checked, 'unverified': unverified}


# ------------------------------------------------------------- rules router
NOT_AVAILABLE_WORDS = ('profit', 'margin', 'cost', 'expense', 'wage', 'salary', 'rent', 'lease',
                       'payroll', 'customer count', 'how many customers', 'foot traffic', 'price',
                       'units sold', 'quantity', 'employees', 'marketing spend', 'roi')


def _extract_filters(q: str, model) -> dict:
    f: dict = {}
    years = sorted({int(y) for y in re.findall(r'\b(20\d{2})\b', q)})
    if years:
        f['years'] = years
    if re.search(r'\b(website|online|web|e-?commerce)\b', q):
        f.setdefault('channels', []).append(analytics.WEBSITE)
    if re.search(r'brick|physical|in-store|retail store', q):
        f.setdefault('channels', []).append(analytics.BRICK)
    for sid in re.findall(r'\bsh\d{2}\b', q):
        f.setdefault('stores', []).append(sid.upper())
    if model.stores is not None and 'City' in model.stores.columns:
        for city in model.stores['City'].dropna().unique():
            if re.search(r'\b' + re.escape(city.lower()) + r'\b', q):
                f.setdefault('stores', []).append(city)
    for name, col in [('categories', 'Category'), ('products', 'Product'), ('regions', 'Location')]:
        for val in model.sales[col].dropna().unique():
            v = str(val).lower()
            if v not in ('website', 'unknown') and re.search(r'\b' + re.escape(v) + r'\b', q):
                f.setdefault(name, []).append(str(val))
    return f


def _dimension(q: str):
    for word, dim in [('channel', 'channel'), ('store', 'store'), ('location', 'store'),
                      ('categor', 'category'), ('product', 'product'), ('region', 'region'),
                      ('month', 'month'), ('year', 'year')]:
        if word in q:
            return dim
    return None


def rules_router(question: str, model) -> list:
    """Keyword rules. Returns a list of (tool_name, arguments); empty if unsupported."""
    q = question.lower()
    f = _extract_filters(q, model)
    years = f.get('years', [])
    growth_years = {}
    if len(years) >= 2:
        growth_years = {'start_year': years[0], 'end_year': years[-1]}
    no_years = {k: v for k, v in f.items() if k != 'years'}

    if any(re.search(r'\b' + re.escape(w), q) for w in NOT_AVAILABLE_WORDS):
        calls = [('get_data_coverage', {})]
        if 'store' in q or f.get('stores'):
            calls.append(('get_revenue_breakdown', {'dimension': 'store', 'filters': f}))
        return calls
    if any(w in q for w in ('missing', 'duplicate', 'quality', 'blank', 'data problem', 'errors in')):
        return [('get_data_quality_report', {})]
    if re.search(r'satisf|survey|\binterest\b|happy customer|\bcaus', q):
        return [('get_satisfaction_vs_revenue', {'filters': f})]
    if any(w in q for w in ('season', 'busiest month', 'slowest month', 'which month',
                            'monthly pattern', 'monthly share', 'share of annual', 'percent of annual')):
        return [('get_monthly_revenue_share', {'filters': f})]
    if any(w in q for w in ('should', 'close', 'recommend', 'shut down', 'invest')):
        # Compare the named store with ALL stores, not just with itself.
        peers = {k: v for k, v in no_years.items() if k != 'stores'}
        return [('get_revenue_growth', {'dimension': 'store', 'filters': peers, **growth_years}),
                ('get_data_coverage', {})]
    if any(w in q for w in ('growth', 'grow', 'declin', 'decrease', 'increase', 'trend',
                            'change', 'drop', 'fell', 'fall', 'improv')):
        dim = _dimension(q)
        if dim in (None, 'year', 'month'):
            dim = 'total'
        if dim == 'store' and f.get('stores') and 'which' not in q:
            return [('get_revenue_growth', {'dimension': 'store', 'filters': no_years, **growth_years})]
        return [('get_revenue_growth', {'dimension': dim, 'filters': no_years, **growth_years})]
    if re.search(r'\b(by|per|each|every) (year|month)\b|annual|yearly', q):
        return [('get_revenue_breakdown', {'dimension': 'month' if 'month' in q else 'year',
                                           'filters': f})]
    if any(w in q for w in ('compare', ' vs', 'versus', 'split', 'breakdown', 'which', 'top',
                            'best', 'worst', 'highest', 'lowest', 'most', 'least', 'rank')):
        dim = _dimension(q) or ('channel' if f.get('channels') else None)
        if dim:
            if dim == 'channel':
                f.pop('channels', None)
            return [('get_revenue_breakdown', {'dimension': dim, 'filters': f})]
    if any(w in q for w in ('revenue', 'sales', 'how much', 'total', 'earn', 'generate', 'make')) or f:
        return [('get_revenue_total', {'filters': f})]
    return []


def _money(x):
    return f'${x:,.0f}'


def _rules_text(question: str, results: list[ToolResult]) -> str:
    """Write the answer from tool results with fixed templates (no language model)."""
    findings, interp, limits, nexts = [], [], [], []
    scope = None
    q = question.lower()
    for r in results:
        if r.error:
            findings.append(f'`{r.tool}` could not answer: {r.error}')
            continue
        if r.scope.get('filters') and (scope is None or scope.startswith('none')):
            scope = r.scope['filters']
            if r.scope.get('dashboard_filters_applied'):
                scope += ' (from the sidebar filter)'
        v = r.value
        if r.tool == 'get_revenue_total':
            findings.append(f'Total revenue: **{_money(v["total_revenue"])}** ({v["period"]}, '
                            f'{v["stores_covered"]} store(s), {r.rows_used:,} sales rows). '
                            f'Average per month: {_money(v["average_monthly_revenue"])}.')
        elif r.tool == 'get_revenue_breakdown':
            lines = [f'{g["group"]}: {_money(g["revenue"])} ({g["share_pct"]}%)' for g in v['groups'][:12]]
            findings.append(f'Revenue by {r.definition.split("by ")[-1]} (subset total '
                            f'{_money(v["subset_total"])}):\n- ' + '\n- '.join(lines))
            if len(v['groups']) > 1:
                top = max(v['groups'], key=lambda g: g['revenue'])
                interp.append(f'{top["group"]} has the most revenue in this scope.')
        elif r.tool == 'get_revenue_growth':
            s, e = v['start_year'], v['end_year']
            lines = [f'{g["group"]}: {_money(g[f"revenue_{s}"])} -> {_money(g[f"revenue_{e}"])} '
                     f'({g["pct_change"]}%)' for g in v['groups'][:21]]
            sm = v['summary']
            ranked = sorted((g for g in v['groups'] if g['pct_change'] is not None),
                            key=lambda g: g['pct_change'])
            for i, g in enumerate(ranked, 1):
                sid = g['group'].split(' ')[0].lower()
                city = g['group'].split(' ', 1)[-1].split(',')[0].lower()
                if len(ranked) > 1 and (re.search(r'\b' + sid + r'\b', q) or
                                        (city != 'website' and re.search(r'\b' + re.escape(city) + r'\b', q))):
                    findings.append(f'**{g["group"]}**: {_money(g[f"revenue_{s}"])} in {s} -> '
                                    f'{_money(g[f"revenue_{e}"])} in {e} ({g["pct_change"]}%). '
                                    f'It ranks {i} of {len(ranked)} from largest decline to largest growth.')
            if sm['groups_compared'] > 1:
                findings.append(f'Largest % increase: **{sm["largest_pct_increase"] or "none (nothing grew)"}**. '
                                f'Largest % decline: **{sm["largest_pct_decline"] or "none (nothing declined)"}**. '
                                f'{sm["groups_that_declined"]} of {sm["groups_compared"]} declined.')
            findings.append(f'Revenue change {s} to {e}:\n- ' + '\n- '.join(lines))
        elif r.tool == 'get_monthly_revenue_share':
            avg = v['average_share_pct_by_month']
            findings.append('Average share of annual revenue by month: ' +
                            ', '.join(f'{m} {p}%' for m, p in avg.items()) + '.')
            interp.append(f'{v["peak_month"]} is the peak month and {v["lowest_month"]} the lowest, '
                          'which suggests a strong holiday season.')
        elif r.tool == 'get_satisfaction_vs_revenue':
            findings.append(f'Across {v["store_years_with_scores"]} store-years, the correlation '
                            f'between satisfaction and revenue is r = {v["pearson_r_satisfaction_vs_revenue"]} '
                            f'({v["strength"]}). Interest vs revenue: r = {v["pearson_r_interest_vs_revenue"]}.')
            interp.append('Stores with higher satisfaction tend to have somewhat higher revenue, but '
                          'this is an association, not proof that satisfaction causes revenue.')
        elif r.tool == 'get_data_quality_report':
            issues = v['issues']
            findings.append(f'{v["sales_rows_used"]:,} of {v["sales_rows_in_file"]:,} sales rows are usable. '
                            f'{len(issues)} issue(s) found' + (':\n- ' + '\n- '.join(
                                f'[{i["severity"]}] {i["check"]}: {i["detail"]}' for i in issues) if issues else '.'))
        elif r.tool == 'get_data_coverage':
            findings.append(f'The data covers {v["period"]} with revenue by store, product and month, '
                            'plus annual survey scores.')
            limits.append('This data does NOT include: ' + '; '.join(v['not_in_the_data']) +
                          '. Questions about profit, costs or customers cannot be answered from it.')
        limits.extend(c for c in r.caveats if c not in limits)
    missing = [w for w in NOT_AVAILABLE_WORDS if re.search(r'\b' + re.escape(w), q)]
    if missing:
        findings.insert(0, f'**This cannot be answered from the data.** The question needs '
                           f'{", ".join(missing)} information, and the data contains only revenue '
                           '(plus annual survey scores). Any revenue figures below are context, not an answer.')
        interp.append('The highest-revenue store is not necessarily the most profitable one; costs '
                      'could differ a lot between stores.')
        nexts.append('To answer it, management would need cost and expense data by store '
                     '(cost of goods, wages, rent), which this data does not contain.')
    tools = {r.tool for r in results if not r.error}
    if any(w in q for w in ('should', 'close', 'recommend', 'shut down', 'invest')):
        interp.append('Revenue trends alone cannot justify closing or keeping a store.')
        nexts.append('Before deciding, management would need operating costs and profitability by '
                     'store, lease obligations, local market conditions and customer behaviour.')
    elif 'get_satisfaction_vs_revenue' in tools:
        nexts.append('Testing whether satisfaction drives revenue would need a controlled comparison '
                     '(for example, stores of similar size and market) or an experiment.')
    elif 'get_revenue_growth' in tools and any(
            r.value and r.value['summary']['groups_that_declined'] for r in results
            if r.tool == 'get_revenue_growth' and not r.error):
        nexts.append('Investigate why the declining groups fell (local competition, staffing, '
                     'store traffic); this data shows only that revenue fell, not why.')
    elif 'get_data_quality_report' in tools:
        nexts.append('Correct any error-level issues in the source files and upload them again.')
    next_text = ('No recommendation is supported by this data. ' + ' '.join(nexts)).strip() \
        if (missing or 'should' in q or 'close' in q or 'recommend' in q or not nexts
            or 'get_satisfaction_vs_revenue' in tools) else ' '.join(nexts)
    return '\n\n'.join([
        f'**Scope:** {scope or "n/a"}',
        '**Findings:** ' + '\n\n'.join(findings),
        '**Interpretation:** ' + (' '.join(interp) or 'None beyond the findings above.'),
        '**Limitations:** ' + (' '.join(limits) or 'None reported by the tools.'),
        '**Next steps:** ' + next_text,
    ])


def answer_with_rules(question: str, model, base: Filters | None = None, notice: str = '') -> dict:
    log: list = []
    calls = rules_router(question, model)
    if not calls:
        return {'mode': 'rules', 'text': UNSUPPORTED, 'tool_log': log, 'notice': notice,
                'verification': {'numbers_checked': 0, 'unverified': []}}
    results = [run_tool(name, args, model, log, base) for name, args in calls]
    text = _rules_text(question, results)
    return {'mode': 'rules', 'text': text, 'tool_log': log, 'notice': notice,
            'verification': verify_numbers(text, log, question)}


# --------------------------------------------------------------- LLM router
class ModelUnavailable(Exception):
    """The language model could not be used (no key, quota, network...)."""


def _gemini_tools():
    from google.genai import types
    decls = [types.FunctionDeclaration(name=n, description=t['description'],
                                       parameters_json_schema=t['schema'])
             for n, t in TOOLS.items()]
    return [types.Tool(function_declarations=decls)]


def answer_with_llm(question: str, model, api_key: str, model_name: str = DEFAULT_MODEL,
                    base: Filters | None = None, history: list | None = None, client=None) -> dict:
    """Function-calling loop: the model picks tools, we run them, the model writes the answer."""
    try:
        from google import genai
        from google.genai import errors, types
    except ImportError as exc:
        raise ModelUnavailable(f'google-genai is not installed ({exc})')

    client = client or genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT, tools=_gemini_tools(), temperature=0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    contents = []
    for turn in (history or [])[-3:]:          # short memory for follow-up questions
        contents.append(types.Content(role='user', parts=[types.Part.from_text(text=turn['question'])]))
        contents.append(types.Content(role='model', parts=[types.Part.from_text(text=turn['text'])]))
    scope_note = (f'Dashboard filters currently applied to every tool call: {base.describe()}.'
                  if base is not None and not base.is_empty() else
                  'No dashboard filters are applied; tools use the full dataset unless you pass filters.')
    contents.append(types.Content(role='user', parts=[types.Part.from_text(
        text=f'{question}\n\n[App note: {scope_note}]')]))

    log: list = []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            resp = client.models.generate_content(model=model_name, contents=contents, config=config)
            calls = resp.function_calls or []
            if not calls:
                break
            contents.append(resp.candidates[0].content)
            parts = []
            for call in calls:
                result = run_tool(call.name, dict(call.args or {}), model, log, base)
                parts.append(types.Part.from_function_response(
                    name=call.name, response={'result': json.loads(json.dumps(result.to_dict(), default=str))}))
            contents.append(types.Content(role='user', parts=parts))
        else:
            resp = client.models.generate_content(
                model=model_name, contents=contents + [types.Content(role='user', parts=[
                    types.Part.from_text(text='Write the final answer now using the tool results.')])],
                config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=0))
        text = (resp.text or '').strip()
    except errors.APIError as exc:
        code = getattr(exc, 'code', None)
        if code == 429:
            raise ModelUnavailable('the model\'s free-tier quota or rate limit was reached')
        if code in (401, 403):
            raise ModelUnavailable('the API key was rejected')
        raise ModelUnavailable(f'the model API returned an error ({code})')
    except Exception as exc:  # network errors, timeouts, unexpected responses
        raise ModelUnavailable(f'the model could not be reached ({type(exc).__name__})')

    if not text:
        raise ModelUnavailable('the model returned an empty answer')
    notice = ''
    if not log:
        notice = 'The model answered without calling any tool, so none of its numbers are backed by a calculation.'
    return {'mode': 'llm', 'text': text, 'tool_log': log, 'notice': notice,
            'verification': verify_numbers(text, log, question)}


def answer_question(question: str, model, mode: str = 'auto', api_key: str | None = None,
                    model_name: str = DEFAULT_MODEL, base: Filters | None = None,
                    history: list | None = None) -> dict:
    """mode: 'llm' (language model), 'rules' (keyword rules), 'auto' (llm, fall back to rules)."""
    if mode == 'rules':
        return answer_with_rules(question, model, base)
    if not api_key:
        if mode == 'llm':
            return answer_with_rules(question, model, base,
                                     notice='No API key is configured, so the rules-based assistant answered.')
        return answer_with_rules(question, model, base)
    try:
        return answer_with_llm(question, model, api_key, model_name, base, history)
    except ModelUnavailable as exc:
        return answer_with_rules(question, model, base,
                                 notice=f'The language model is unavailable: {exc}. '
                                        'This answer comes from the rules-based assistant instead.')
