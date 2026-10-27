"""Assistant tests: the dispatcher's guard rails, the rules router, the number check,
and the language-model loop (with a fake model, so no API key is needed)."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import analytics as an  # noqa: E402
from test_analytics import sales_df, tables  # noqa: E402


@pytest.fixture
def model():
    return an.build_model(tables())


def test_dispatcher_rejects_unknown_tool_and_arguments(model):
    log = []
    assert agent.run_tool('run_python', {}, model, log).error
    assert agent.run_tool('get_revenue_total', {'code': 'x'}, model, log).error
    assert len(log) == 2                                     # failures are logged too


def test_dispatcher_handles_bad_argument_types(model):
    r = agent.run_tool('get_revenue_growth', {'start_year': 'abc'}, model, [])
    assert r.error


@pytest.mark.parametrize('question,tool', [
    ('How much revenue did the website generate in 2025?', 'get_revenue_total'),
    ('Which store had the greatest percentage revenue decline?', 'get_revenue_growth'),
    ('Which store is the most profitable?', 'get_data_coverage'),
    ('Does higher satisfaction cause higher revenue?', 'get_satisfaction_vs_revenue'),
    ('What is our busiest month?', 'get_monthly_revenue_share'),
    ('Are there duplicates or missing values?', 'get_data_quality_report'),
    ('Compare revenue by category', 'get_revenue_breakdown'),
])
def test_rules_router(question, tool, model):
    assert agent.rules_router(question, model)[0][0] == tool


def test_rules_answer_uses_current_data(model):
    q = 'How much revenue did the website generate in 2025?'
    before = agent.answer_question(q, model, mode='rules')
    s = sales_df()
    s.loc[0, 'Revenue'] += 1000
    after = agent.answer_question(q, an.build_model(tables(sales=s)), mode='rules')
    assert '$400' in before['text'] and '$1,400' in after['text']
    assert after['tool_log'][0]['result']['value']['total_revenue'] == 1400


def test_unsupported_question(model):
    out = agent.answer_question('What is the meaning of life?', model, mode='rules')
    assert out['tool_log'] == [] and 'can only answer' in out['text']


def test_ambiguous_store_name_is_not_guessed():
    st = tables()['stores']
    st.loc[2, 'City'] = 'Chicago'
    m = an.build_model(tables(stores=st))
    r = an.get_revenue_total(m, {'stores': ['Chicago']})
    assert r.error and 'matches 2 stores' in r.error


def test_verify_numbers_accepts_rounding_and_flags_inventions(model):
    log = []
    agent.run_tool('get_revenue_total', {}, model, log)
    ok = agent.verify_numbers('Total revenue was $1,025 (about $1.0 thousand).', log)
    assert ok['unverified'] == []
    bad = agent.verify_numbers('Total revenue was $1,025 and profit was $412.', log)
    assert bad['unverified'] == ['$412']


# ------------------------------------------------------------ fake LLM loop
class FakeModels:
    """Turn 1: asks for get_revenue_total(filters={'years': [2026]}). Turn 2: answers."""
    def __init__(self, answer):
        self.calls = 0
        self.answer = answer

    def generate_content(self, model, contents, config):
        from google.genai import types
        self.calls += 1
        if self.calls == 1:
            call = types.FunctionCall(name='get_revenue_total', args={'filters': {'years': [2026]}})
            content = types.Content(role='model', parts=[types.Part(function_call=call)])
            return SimpleNamespace(function_calls=[call], candidates=[SimpleNamespace(content=content)],
                                   text=None)
        return SimpleNamespace(function_calls=None, candidates=[], text=self.answer)


def test_llm_loop_runs_the_requested_tool(model):
    pytest.importorskip('google.genai')
    fake = SimpleNamespace(models=FakeModels('**Scope:** 2026\n**Findings:** Revenue was $575.'))
    out = agent.answer_with_llm('Revenue in 2026?', model, api_key='x', client=fake)
    assert out['mode'] == 'llm'
    assert out['tool_log'][0]['tool'] == 'get_revenue_total'
    assert out['tool_log'][0]['result']['value']['total_revenue'] == 575
    assert out['verification']['unverified'] == []


def test_llm_invented_number_is_flagged(model):
    pytest.importorskip('google.genai')
    fake = SimpleNamespace(models=FakeModels('Revenue was $575 and profit was $200.'))
    out = agent.answer_with_llm('Revenue in 2026?', model, api_key='x', client=fake)
    assert out['verification']['unverified'] == ['$200']


def test_model_failure_falls_back_to_rules(model, monkeypatch):
    def boom(*a, **k):
        raise agent.ModelUnavailable("the model's free-tier quota or rate limit was reached")
    monkeypatch.setattr(agent, 'answer_with_llm', boom)
    out = agent.answer_question('Total revenue?', model, mode='auto', api_key='x')
    assert out['mode'] == 'rules' and 'quota' in out['notice']
    assert out['tool_log'][0]['result']['value']['total_revenue'] == 1025


class RetiredFirstModel(FakeModels):
    """The first model name returns 404 (retired); the fallback model works."""
    def __init__(self, answer):
        super().__init__(answer)
        self.models_tried = []

    def generate_content(self, model, contents, config):
        from google.genai import errors
        self.models_tried.append(model)
        if model == 'retired-model':
            raise errors.ClientError(404, {'error': {'code': 404, 'message': 'no longer available',
                                                     'status': 'NOT_FOUND'}})
        return super().generate_content(model, contents, config)


def test_retired_model_falls_back_to_next_model(model):
    pytest.importorskip('google.genai')
    fake = RetiredFirstModel('**Scope:** 2026\n**Findings:** Revenue was $575.')
    out = agent.answer_with_llm('Revenue in 2026?', model, api_key='x', model_name='retired-model',
                                client=SimpleNamespace(models=fake))
    assert fake.models_tried[0] == 'retired-model'
    assert out['model'] == agent.FALLBACK_MODELS[0]
    assert out['tool_log'][0]['result']['value']['total_revenue'] == 575


def test_overloaded_model_falls_back_too(model):
    pytest.importorskip('google.genai')
    from google.genai import errors

    class Busy(RetiredFirstModel):
        def generate_content(self, model, contents, config):
            if model == 'busy-model':
                self.models_tried.append(model)
                raise errors.ServerError(503, {'error': {'code': 503, 'message': 'high demand',
                                                         'status': 'UNAVAILABLE'}})
            return super().generate_content(model, contents, config)

    fake = Busy('Revenue was $575.')
    out = agent.answer_with_llm('Revenue in 2026?', model, api_key='x', model_name='busy-model',
                                client=SimpleNamespace(models=fake))
    assert out['mode'] == 'llm' and out['model'] == agent.FALLBACK_MODELS[0]


def test_daily_quota_on_one_model_moves_to_the_next(model):
    pytest.importorskip('google.genai')
    from google.genai import errors

    class OutOfQuota(RetiredFirstModel):
        def generate_content(self, model, contents, config):
            if model == 'used-up-model':
                self.models_tried.append(model)
                raise errors.ClientError(429, {'error': {'code': 429, 'message': 'per day limit',
                                                         'status': 'RESOURCE_EXHAUSTED'}})
            return super().generate_content(model, contents, config)

    fake = OutOfQuota('Revenue was $575.')
    out = agent.answer_with_llm('Revenue in 2026?', model, api_key='x', model_name='used-up-model',
                                client=SimpleNamespace(models=fake))
    assert out['mode'] == 'llm' and out['model'] == agent.FALLBACK_MODELS[0]
