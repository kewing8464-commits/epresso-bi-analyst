"""Main app: screen layout and interaction only.
Calculations live in analytics.py and the assistant in agent.py."""
from __future__ import annotations

import datetime as dt
import json
import threading

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import agent
import analytics as an

MAX_QUESTIONS = 15        # per browser session
MAX_DAILY_LLM_CALLS = 200  # across all visitors, protects the free API quota

# Colors: one fixed color per entity so a filter never repaints a series.
CHANNEL_COLORS = {an.WEBSITE: '#2a78d6', an.BRICK: '#eb6834', 'Unknown': '#8a8984'}
CHANNEL_ORDER = {'Channel': [an.WEBSITE, an.BRICK, 'Unknown']}
UP, DOWN = '#2a78d6', '#eb6834'
SEQ = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']

st.set_page_config(page_title='E-Presso BI Analyst', page_icon='☕', layout='wide')


# ------------------------------------------------------------------ helpers
@st.cache_data(show_spinner='Reading and checking the files...')
def cached_model(files: tuple):
    # Keyed on the file CONTENTS, so a modified file is never confused with the original.
    tables, log = an.load_files(list(files))
    return an.build_model(tables, log)


@st.cache_resource
def _usage():
    return {'date': dt.date.today(), 'count': 0, 'lock': threading.Lock()}


def take_llm_call() -> bool:
    """App-wide daily cap on model calls so a public URL cannot drain the free quota."""
    u = _usage()
    with u['lock']:
        if u['date'] != dt.date.today():
            u['date'], u['count'] = dt.date.today(), 0
        if u['count'] >= MAX_DAILY_LLM_CALLS:
            return False
        u['count'] += 1
        return True


def secret(name: str, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:  # no secrets file locally
        return default


def md_safe(text: str) -> str:
    # Streamlit reads $...$ as LaTeX math, which garbles answers like "$269,100 ... $22,425".
    return text.replace('$', '\\$')


def money(x: float) -> str:
    return f'${x:,.0f}'


def fig_style(fig, height=360):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), legend_title_text='',
                      legend=dict(orientation='h', yanchor='bottom', y=1.02, x=0),
                      hoverlabel=dict(namelength=-1))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridwidth=0.5)
    return fig


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header('1. Data')
    uploads = st.file_uploader(
        'Upload the E-Presso files (Excel or CSV). Select them all at once.',
        type=['csv', 'txt', 'tsv', 'xlsx', 'xls'], accept_multiple_files=True,
        help='Sales (with Categories and Products), Stores, and Survey. Each table is '
             'recognised by its column names, so file names do not matter.')

st.title('☕ E-Presso BI Analyst')
st.caption('Revenue performance for E-Presso\'s website and 20 physical stores. Every number is '
           'calculated from the files you upload; nothing is hard-coded.')

if not uploads:
    st.info('**Upload the E-Presso data files in the sidebar to begin.**\n\n'
            'Expected tables: **Sales** (Sales ID, Date, Store ID, Category ID, Product ID, Revenue), '
            '**Categories**, **Products**, **Stores** (ID, StoreType, City, ...) and **Survey Results**. '
            'They may be in one workbook or several files.')
    st.stop()

files = tuple(sorted((u.name, u.getvalue()) for u in uploads))
try:
    model = cached_model(files)
except Exception as exc:
    st.error(f'Could not build the data model: {exc}')
    st.stop()

sales = model.sales

with st.sidebar:
    loaded = [k for k in an.TABLE_SPECS if k not in model.missing_tables]
    st.success(f'Loaded: {", ".join(loaded)}')
    if model.missing_tables:
        st.warning(f'Missing: {", ".join(model.missing_tables)}. Some views will be limited.')
    for entry in model.file_log:
        if entry.get('missing_columns'):
            st.error(entry['note'], icon='⚠️')
    st.caption(f'Data fingerprint `{model.fingerprint}` changes whenever any sales value changes.')

    st.header('2. Filters')
    if st.button('Reset filters', use_container_width=True):
        for k in ('f_years', 'f_channels', 'f_regions', 'f_stores', 'f_categories', 'f_products'):
            st.session_state.pop(k, None)
    f_years = st.multiselect('Year', model.years, key='f_years', placeholder='All years')
    f_channels = st.multiselect('Sales channel', sorted(sales['Channel'].unique()), key='f_channels',
                                placeholder='All channels')
    f_regions = st.multiselect('Region', sorted(sales['Location'].unique()), key='f_regions',
                               placeholder='All regions')
    store_opts = sales[['Store ID', 'Store']].drop_duplicates().sort_values('Store ID')
    f_stores = st.multiselect('Store', store_opts['Store ID'].tolist(), key='f_stores',
                              format_func=dict(zip(store_opts['Store ID'], store_opts['Store'])).get,
                              placeholder='All stores')
    f_categories = st.multiselect('Category', sorted(sales['Category'].unique()), key='f_categories',
                                  placeholder='All categories')
    prod_pool = sales if not f_categories else sales[sales['Category'].isin(f_categories)]
    f_products = st.multiselect('Product', sorted(prod_pool['Product'].unique()), key='f_products',
                                placeholder='All products')

filters = an.Filters(years=f_years, channels=f_channels, regions=f_regions, stores=f_stores,
                     categories=f_categories, products=[p for p in f_products
                                                        if p in set(prod_pool['Product'])])
view = an.apply_filters(sales, filters)
st.markdown(f'**Showing:** {filters.describe()}  ·  {len(view):,} of {len(sales):,} sales rows')
if view.empty:
    st.warning('No sales rows match these filters. Change or reset the filters.')
    st.stop()

tab_dash, tab_stores, tab_assistant, tab_data, tab_valid = st.tabs(
    ['📊 Dashboard', '🏪 Stores & products', '💬 Assistant', '🧹 Data & quality', '✅ Validation'])

# ---------------------------------------------------------------- dashboard
with tab_dash:
    k = an.kpis(view)
    c = st.columns(3)
    c[0].metric('Total revenue', money(k['total']), help='Sum of Sales[Revenue] for the filtered rows.')
    c[1].metric('Website revenue', money(k['website']), help='Store SH00 (StoreType = Website).')
    c[2].metric('Brick & mortar revenue', money(k['brick']), help='Stores SH01 to SH20.')
    c = st.columns(3)
    c[0].metric('Website share of revenue', f'{k["website_share"]:.1f}%' if k['website_share'] is not None else 'n/a',
                help='Website revenue / total revenue of the filtered rows.')
    if k['yoy']:
        c[1].metric(f'Growth {k["yoy"]["from"]} to {k["yoy"]["to"]}', f'{k["yoy"]["pct"]:+.1f}%',
                    help='Revenue in the latest year vs the year before, within the filters.')
    else:
        c[1].metric('Year-over-year growth', 'n/a', help='Needs at least two years in the filter.')
    c[2].metric('Average monthly revenue', money(k['avg_month']),
                help='Total revenue / number of months in the filtered period.')
    st.caption(f'Period: {view["Date"].min():%b %Y} to {view["Date"].max():%b %Y} · '
               f'{k["stores"]} store(s). '
               'Revenue is not profit: the data contains no costs.')

    left, right = st.columns(2)
    with left:
        st.subheader('Annual revenue by channel')
        annual = an.revenue_by(view, ['Year', 'Channel'])
        fig = px.bar(annual, x='Year', y='Revenue', color='Channel', barmode='group',
                     color_discrete_map=CHANNEL_COLORS, category_orders=CHANNEL_ORDER,
                     labels={'Revenue': 'Revenue ($)'})
        fig.update_traces(hovertemplate='%{x}<br>%{fullData.name}: $%{y:,.0f}<extra></extra>')
        fig.update_xaxes(type='category')
        st.plotly_chart(fig_style(fig), use_container_width=True)
    with right:
        st.subheader('Monthly revenue trend')
        monthly = an.revenue_by(view, ['YearMonth', 'Channel'])
        fig = px.line(monthly, x='YearMonth', y='Revenue', color='Channel',
                      color_discrete_map=CHANNEL_COLORS, category_orders=CHANNEL_ORDER,
                      labels={'YearMonth': 'Month', 'Revenue': 'Revenue ($)'})
        fig.update_traces(line_width=2, hovertemplate='%{x|%b %Y}: $%{y:,.0f}<extra></extra>')
        fig.update_layout(hovermode='x unified')
        st.plotly_chart(fig_style(fig), use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.subheader('Monthly share of annual revenue')
        st.caption('Each cell = that month\'s revenue / the same year\'s total (within the filters).')
        share = an.monthly_share(view)
        grid = share.pivot(index='Year', columns='Month', values='Share %')
        grid = grid[[m for m in an.MONTH_NAMES if m in grid.columns]]
        fig = go.Figure(go.Heatmap(z=grid.values, x=grid.columns, y=[str(y) for y in grid.index],
                                   colorscale=[[i / (len(SEQ) - 1), c] for i, c in enumerate(SEQ)],
                                   text=grid.round(1).astype(str).values + '%', texttemplate='%{text}',
                                   hovertemplate='%{y} %{x}: %{z:.2f}% of the year<extra></extra>',
                                   colorbar=dict(title='% of year', ticksuffix='%')))
        st.plotly_chart(fig_style(fig, 330), use_container_width=True)
    with right:
        st.subheader('Revenue by category')
        cat = an.revenue_by(view, 'Category').sort_values('Revenue')
        cat['Share'] = cat['Revenue'] / cat['Revenue'].sum() * 100
        fig = px.bar(cat, x='Revenue', y='Category', orientation='h', labels={'Revenue': 'Revenue ($)'},
                     custom_data=['Share'], text=cat['Share'].map(lambda s: f'{s:.1f}%'))
        fig.update_traces(marker_color=CHANNEL_COLORS[an.WEBSITE], textposition='outside', cliponaxis=False,
                          hovertemplate='%{y}: $%{x:,.0f} (%{customdata[0]:.1f}%)<extra></extra>')
        fig.update_xaxes(range=[0, cat['Revenue'].max() * 1.18])
        st.plotly_chart(fig_style(fig, 330), use_container_width=True)

# ------------------------------------------------------------ stores & products
with tab_stores:
    yrs = sorted(view['Year'].unique())
    st.subheader('Store revenue change')
    if len(yrs) < 2:
        st.info('Select at least two years (or clear the Year filter) to compare store growth.')
    else:
        c1, c2 = st.columns(2)
        start = c1.selectbox('From year', yrs[:-1], index=0)
        end = c2.selectbox('To year', [y for y in yrs if y > start], index=len([y for y in yrs if y > start]) - 1)
        g = an.growth_table(view, 'Store', start, end).sort_values('pct_change')
        g['Direction'] = g['pct_change'].map(lambda p: 'Grew' if p > 0 else 'Declined')
        fig = px.bar(g, x='pct_change', y='group', orientation='h', color='Direction',
                     color_discrete_map={'Grew': UP, 'Declined': DOWN},
                     custom_data=['start', 'end'], labels={'pct_change': f'% change {start} to {end}', 'group': ''},
                     text=g['pct_change'].map(lambda p: f'{p:+.1f}%'))
        fig.update_traces(textposition='outside', cliponaxis=False,
                          hovertemplate='%{y}<br>' + f'{start}: ' + '$%{customdata[0]:,.0f}<br>' +
                                        f'{end}: ' + '$%{customdata[1]:,.0f}<br>%{x:+.2f}%<extra></extra>')
        st.plotly_chart(fig_style(fig, 40 + 26 * len(g)), use_container_width=True)
        declined = (g['pct_change'] < 0).sum()
        st.caption(f'{declined} of {len(g)} store(s) declined between {start} and {end}. '
                   '% change = (end year revenue - start year revenue) / start year revenue.')

    left, right = st.columns(2)
    with left:
        st.subheader('Satisfaction vs revenue')
        if model.survey is None:
            st.info('Upload the survey file to see this chart.')
        else:
            sy = an.store_year_survey(model, view).dropna(subset=['Satisfaction'])
            if len(sy) < 3:
                st.info('Not enough store-years with survey scores in the current filter.')
            else:
                r = an._corr(sy['Satisfaction'], sy['Revenue'])
                sy['Year'] = sy['Year'].astype(str)
                fig = px.scatter(sy, x='Satisfaction', y='Revenue', hover_name='Store', hover_data={'Year': True},
                                 labels={'Satisfaction': 'Satisfaction (0-5)', 'Revenue': 'Annual store revenue ($)'})
                fig.update_traces(marker=dict(size=9, color=CHANNEL_COLORS[an.WEBSITE], opacity=0.75,
                                              line=dict(width=1, color='white')))
                st.plotly_chart(fig_style(fig, 340), use_container_width=True)
                st.caption(f'One point per physical store per year (n = {len(sy)}). Pearson r = {r}. '
                           'Association only: this does not show that satisfaction causes revenue. '
                           'The website has no survey scores.')
    with right:
        st.subheader('Top products')
        prod = an.revenue_by(view, ['Product', 'Category']).sort_values('Revenue', ascending=False)
        prod['Share %'] = (prod['Revenue'] / prod['Revenue'].sum() * 100).round(2)
        st.dataframe(prod.head(20), hide_index=True, use_container_width=True,
                     column_config={'Revenue': st.column_config.NumberColumn(format='$%,.0f')})

    st.subheader('Store table')
    tbl = an.revenue_by(view, ['Store ID', 'Store', 'Channel', 'Location']).sort_values('Revenue', ascending=False)
    tbl['Share %'] = (tbl['Revenue'] / tbl['Revenue'].sum() * 100).round(2)
    st.dataframe(tbl, hide_index=True, use_container_width=True,
                 column_config={'Revenue': st.column_config.NumberColumn(format='$%,.0f'),
                                'Location': 'Region'})

# ------------------------------------------------------------------ assistant
with tab_assistant:
    api_key = secret('GEMINI_API_KEY')
    model_name = secret('GEMINI_MODEL', agent.DEFAULT_MODEL)
    st.session_state.setdefault('n_questions', 0)
    st.session_state.setdefault('history', [])

    top = st.columns([3, 2])
    with top[0]:
        if api_key:
            st.markdown(f'**Mode:** live language-model agent (`{model_name}`, Google Gemini function calling). '
                        'If the model is unavailable, the rules-based assistant answers instead and says so.')
        else:
            st.markdown('**Mode:** rules-based assistant (no API key configured). Keyword rules choose '
                        'the same approved functions a language model would.')
        st.caption('The assistant can only call the 7 approved functions listed below. It cannot run code '
                   'or change the data. Every number it states is checked against the tool results.')
    with top[1]:
        use_dash = st.toggle('Apply dashboard filters to the assistant', value=False,
                             help='Off: answers cover the full dataset unless the question names a subset. '
                                  'On: every tool call is limited to the current dashboard filters.')
        st.caption(f'Questions used: {st.session_state["n_questions"]} of {MAX_QUESTIONS} this session.')
    base = filters if use_dash else None

    with st.expander('Approved functions'):
        st.table(pd.DataFrame([{'function': n, 'what it calculates': t['description']}
                               for n, t in agent.TOOLS.items()]))

    examples = ['How much revenue did the website generate in 2028?',
                'Which store experienced the greatest percentage revenue decline?',
                'Which E-Presso store is the most profitable?',
                'Does higher customer satisfaction cause higher revenue?',
                'Should E-Presso close its Chicago store?']
    st.caption('Try one:')
    ex_cols = st.columns(len(examples))
    clicked = None
    for col, ex in zip(ex_cols, examples):
        if col.button(ex, use_container_width=True):
            clicked = ex

    for item in st.session_state['history']:
        with st.chat_message('user'):
            st.write(item['question'])
        with st.chat_message('assistant'):
            a = item['answer']
            badge = f'Live model ({a.get("model", model_name)})' if a['mode'] == 'llm' else 'Rules-based'
            st.caption(f'{badge} · data `{item["fingerprint"]}` · scope: {item["scope"]}')
            if a.get('notice'):
                st.warning(a['notice'])
            st.markdown(md_safe(a['text']))
            v = a['verification']
            if v['unverified']:
                st.error('Numbers not found in any tool result (treat as unverified): ' +
                         ', '.join(v['unverified']))
            elif v['numbers_checked']:
                st.success(f'All {v["numbers_checked"]} numbers in this answer match a tool result.', icon='✅')
            with st.expander(f'Tool-call log ({len(a["tool_log"])} call(s))'):
                for i, entry in enumerate(a['tool_log'], 1):
                    st.markdown(f'**{i}. `{entry["tool"]}`**')
                    st.code(json.dumps(entry['arguments'], indent=2, default=str), language='json')
                    st.json(entry['result'], expanded=2)

    question = st.chat_input('Ask a business question about the loaded data') or clicked
    if question:
        if st.session_state['n_questions'] >= MAX_QUESTIONS:
            st.warning('Question limit reached for this session. Reload the page to start over.')
        else:
            st.session_state['n_questions'] += 1
            mode = 'auto'
            if api_key and not take_llm_call():
                mode = 'rules'
            with st.spinner('Calculating...'):
                answer = agent.answer_question(
                    question, model, mode=mode, api_key=api_key, model_name=model_name, base=base,
                    history=[{'question': h['question'], 'text': h['answer']['text']}
                             for h in st.session_state['history']])
            if api_key and mode == 'rules':
                answer['notice'] = ('The app-wide daily limit for model calls was reached, so the '
                                    'rules-based assistant answered.')
            scope = (f'dashboard filters ({filters.describe()})' if base is not None and not filters.is_empty()
                     else 'full dataset unless the question names a subset')
            st.session_state['history'].append({'question': question, 'answer': answer,
                                                'fingerprint': model.fingerprint, 'scope': scope})
            st.rerun()

# -------------------------------------------------------------- data & quality
with tab_data:
    st.subheader('Files read')
    st.dataframe(pd.DataFrame(model.file_log), hide_index=True, use_container_width=True)

    st.subheader('Grain statements')
    st.caption('What one row of each table represents. Calculated from the loaded files.')
    st.dataframe(an.grain_table(model), hide_index=True, use_container_width=True)

    st.subheader('Data model')
    st.markdown(
        '**Relationships used**\n'
        '- Sales[Store ID] → Stores[ID] (many-to-one)\n'
        '- Sales[Category ID] → Categories[Category ID] (many-to-one)\n'
        '- Sales[Product ID] → Products[Product ID] (many-to-one)\n'
        '- Survey joins at its own grain: revenue is first summed to **store + year**, then matched to '
        'Survey on Store ID + Year. The survey is never joined onto individual sales rows, so it cannot '
        'duplicate revenue.\n'
        '- Sales[Store ID] (e.g. `SH11`) is **not** joined to Survey[Survey ID] (e.g. `SH11_27`). '
        'Those values never match.')
    st.markdown('**Join checks** (a safe join keeps the same row count and the same revenue total)')
    mj = pd.DataFrame(model.merges)
    if not mj.empty:
        mj['result'] = mj['changed'].map({False: '✅ unchanged', True: '⚠️ changed'})
        mj['explanation'] = mj.apply(lambda r: r['warning'] if r['changed'] else (
            'Each store-year matches at most one survey row, so no revenue is added or lost.'
            if 'Survey' in r['join'] else
            'Many-to-one: each sales row finds at most one lookup row, so no revenue is added or lost.'),
            axis=1)
        st.dataframe(mj[['join', 'rows_before', 'rows_after', 'sum_before', 'sum_after', 'result',
                         'explanation']],
                     hide_index=True, use_container_width=True,
                     column_config={'sum_before': st.column_config.NumberColumn(format='$%,.0f'),
                                    'sum_after': st.column_config.NumberColumn(format='$%,.0f')})

    st.subheader('Data quality checks')
    if model.issues:
        icon = {'error': '🔴', 'warning': '🟠', 'info': '🔵'}
        st.dataframe(pd.DataFrame([{'severity': f'{icon.get(i["severity"], "")} {i["severity"]}',
                                    'table': i['table'], 'check': i['check'], 'detail': i['detail'],
                                    'examples': ', '.join(map(str, i['examples']))}
                                   for i in model.issues]), hide_index=True, use_container_width=True)
    else:
        st.success('No issues found by the automatic checks.')
    st.caption(f'{len(model.sales):,} of {model.raw_sales_rows:,} sales rows are usable. Checks: missing and '
               'non-numeric revenue, invalid dates, duplicate rows and IDs, keys missing from lookup tables, '
               'duplicate lookup keys, products in several categories, stores missing months, survey score '
               'ranges, and region labels.')

    st.subheader('Table profiles')
    prof_tables = {'sales': model.sales, 'stores': model.stores, 'categories': model.categories,
                   'products': model.products, 'survey': model.survey}
    pick = st.selectbox('Table', [k for k, v in prof_tables.items() if v is not None])
    df = prof_tables[pick]
    p = an.profile_table(df)
    c = st.columns(3)
    c[0].metric('Rows', f'{p["rows"]:,}')
    c[1].metric('Columns', p['columns'])
    c[2].metric('Duplicate rows', p['duplicate_rows'])
    st.dataframe(df.head(50), use_container_width=True)
    l, r = st.columns(2)
    l.markdown('**Missing values**')
    l.dataframe(p['missing'], hide_index=True, use_container_width=True)
    r.markdown('**Data types**')
    r.dataframe(p['dtypes'], hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- validation
with tab_valid:
    st.markdown('Key results calculated from the loaded data, **ignoring the sidebar filters**, for '
                'comparison with Power BI. Pin a baseline, upload a modified file, and the change column '
                'shows exactly what moved.')
    full = model.sales
    rows = [('Total revenue (all years)', full['Revenue'].sum()),
            ('Website revenue', full.loc[full['Channel'] == an.WEBSITE, 'Revenue'].sum()),
            ('Brick & mortar revenue', full.loc[full['Channel'] == an.BRICK, 'Revenue'].sum())]
    rows += [(f'Revenue {int(y)}', v) for y, v in full.groupby('Year')['Revenue'].sum().items()]
    rows += [(f'Revenue {int(y)} - {c}', v) for (y, c), v in
             full.groupby(['Year', 'Channel'])['Revenue'].sum().items()]
    rows += [(f'Revenue in {m} (all years)', v) for m, v in
             full.groupby('MonthNo')['Revenue'].sum().rename(lambda i: an.MONTH_NAMES[i - 1]).items()]
    month_tot = full.groupby('MonthNo')['Revenue'].sum()
    rows += [(f'{an.MONTH_NAMES[i - 1]} % of 5-year total (Power BI %GT)', v / month_tot.sum() * 100)
             for i, v in month_tot.items()]
    rows += [(f'Revenue - {c}', v) for c, v in full.groupby('Category')['Revenue'].sum().items()]
    rows += [('Sales rows used', float(len(full)))]
    vt = pd.DataFrame(rows, columns=['Metric', 'Streamlit result'])

    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button('📌 Pin as baseline'):
        st.session_state['baseline'] = {'fingerprint': model.fingerprint,
                                        'values': dict(zip(vt['Metric'], vt['Streamlit result']))}
    if b2.button('Clear baseline'):
        st.session_state.pop('baseline', None)
    bl = st.session_state.get('baseline')
    if bl:
        vt['Baseline'] = vt['Metric'].map(bl['values'])
        vt['Change'] = vt['Streamlit result'] - vt['Baseline']
        st.caption(f'Baseline data `{bl["fingerprint"]}` vs current data `{model.fingerprint}`.')
        moved = vt[vt['Change'].abs() > 1e-9]
        if bl['fingerprint'] != model.fingerprint and not moved.empty:
            st.info('Changed metrics: ' + '; '.join(f'{r.Metric} {r.Change:+,.2f}' for r in moved.itertuples()))
    money_cols = {c: st.column_config.NumberColumn(format='%,.2f') for c in vt.columns if c != 'Metric'}
    st.dataframe(vt, hide_index=True, use_container_width=True, column_config=money_cols,
                 height=36 * (len(vt) + 1) + 4)
    st.download_button('Download validation table (CSV)', vt.to_csv(index=False).encode(),
                       f'validation_{model.fingerprint}.csv', 'text/csv')
