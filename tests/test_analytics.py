"""Tests use a tiny made-up E-Presso dataset so every answer can be checked by hand.

Sales (6 rows):
  website W0 : 2025-01 $100, 2025-02 $300, 2026-01 $400     -> website total $800
  store  S1  : 2025-01  $50, 2026-01  $25                    -> S1 falls 50%
  store  S2  : 2026-02 $150                                  (no 2025 sales)
Total revenue = $1,025.
"""
import io
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics as an  # noqa: E402


def sales_df():
    return pd.DataFrame({
        'Sales ID': ['a', 'b', 'c', 'd', 'e', 'f'],
        'Date': ['2025-01-31', '2025-02-28', '2026-01-31', '2025-01-31', '2026-01-31', '2026-02-28'],
        'Store ID': ['W0', 'W0', 'W0', 'S1', 'S1', 'S2'],
        'StoreYear': ['W0_25', 'W0_25', 'W0_26', 'S1_25', 'S1_26', 'S2_26'],
        'Category ID': ['C1', 'C1', 'C2', 'C1', 'C2', 'C1'],
        'Product ID': ['P1', 'P1', 'P2', 'P1', 'P2', 'P1'],
        'Revenue': [100, 300, 400, 50, 25, 150],
    })


def tables(**overrides):
    t = {
        'sales': sales_df(),
        'stores': pd.DataFrame({'ID': ['W0', 'S1', 'S2'], 'City': [None, 'Chicago', 'Denver'],
                                'State': [None, 'IL', 'CO'],
                                'StoreType': ['Website', 'Brick & Mortar', 'Brick & Mortar'],
                                'Location': ['Website', 'Midwest', 'West']}),
        'categories': pd.DataFrame({'Category ID': ['C1', 'C2'], 'Category': ['Beans', 'Syrups']}),
        'products': pd.DataFrame({'Product ID': ['P1', 'P2'], 'Product': ['Dark Roast', 'Vanilla']}),
        'survey': pd.DataFrame({'Survey ID': ['S1_25', 'S1_26', 'S2_26', 'W0_25'],
                                'Store ID': ['S1', 'S1', 'S2', 'W0'],
                                'Year': ['Y2025', 'Y2026', 'Y2026', 'Y2025'],
                                'Satisfaction': [4.0, 2.0, 3.0, None], 'Interest': [80, 70, 75, None]}),
    }
    t.update(overrides)
    return t


@pytest.fixture
def model():
    return an.build_model(tables())


# ------------------------------------------------------------------ loading
def test_identifies_tables_and_skips_title_rows():
    """The survey sheet has two title rows above its header, like the real file."""
    survey = pd.DataFrame([['E-Presso Market Survey', None, None, None, None],
                           [None, None, None, None, None],
                           ['Survey ID', 'Store ID', 'Year', 'Satisfaction', 'Interest'],
                           ['S1_25', 'S1', 'Y2025', 4.1, 89]])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        survey.to_excel(w, sheet_name='Survey Results', index=False, header=False)
        pd.DataFrame({'Notes': ['hello']}).to_excel(w, sheet_name='Documentation', index=False)
    stores_tsv = 'ID\tCity\tStoreType\nS1\tChicago\tBrick & Mortar\n'.encode()
    found, log = an.load_files([('survey.xlsx', buf.getvalue()), ('stores.csv', stores_tsv)])
    assert set(found) == {'survey', 'stores'}
    assert found['survey'].iloc[0]['Satisfaction'] == 4.1
    assert any(entry['table'] is None for entry in log)        # Documentation ignored


def test_column_names_are_matched_loosely():
    df = sales_df().rename(columns={'Store ID': 'store_id', 'Revenue': ' REVENUE '})
    raw = pd.DataFrame([df.columns.tolist()] + df.values.tolist())
    kind, row = an.identify_table(raw)
    assert kind == 'sales' and row == 0
    assert 'Store ID' in an.extract_table(raw, kind, row).columns


# -------------------------------------------------------------------- model
def test_total_and_joins_preserve_revenue(model):
    assert model.sales['Revenue'].sum() == 1025
    assert all(not m['changed'] for m in model.merges)
    assert len(model.sales) == 6


def test_duplicate_lookup_key_cannot_duplicate_revenue():
    stores = tables()['stores']
    stores = pd.concat([stores, stores.iloc[[1]]])            # S1 listed twice
    m = an.build_model(tables(stores=stores))
    assert m.sales['Revenue'].sum() == 1025
    assert any(i['check'] == 'Duplicate ID' for i in m.issues)


def test_orphan_key_is_reported_and_still_counted():
    s = sales_df()
    s.loc[5, 'Product ID'] = 'P9'
    m = an.build_model(tables(sales=s))
    assert m.sales['Revenue'].sum() == 1025
    assert any('not found in lookup' in i['check'] for i in m.issues)


def test_bad_revenue_is_excluded_and_reported():
    s = sales_df().astype({'Revenue': object})
    s.loc[0, 'Revenue'] = 'abc'
    m = an.build_model(tables(sales=s))
    assert m.sales['Revenue'].sum() == 925
    assert any(i['check'] == 'Non-numeric revenue' for i in m.issues)


def test_money_text_is_parsed():
    s = sales_df().astype({'Revenue': object})
    s.loc[0, 'Revenue'] = '$1,100'
    assert an.build_model(tables(sales=s)).sales['Revenue'].sum() == 2025


def test_shared_product_names_stay_separate():
    p = pd.DataFrame({'Product ID': ['P1', 'P2'], 'Product': ['Base Model', 'Base Model']})
    m = an.build_model(tables(products=p))
    assert set(m.sales['Product']) == {'Base Model (P1)', 'Base Model (P2)'}


def test_adding_1000_changes_total_by_exactly_1000(model):
    s = sales_df()
    s.loc[4, 'Revenue'] += 1000
    changed = an.build_model(tables(sales=s))
    assert changed.sales['Revenue'].sum() - model.sales['Revenue'].sum() == 1000
    assert changed.fingerprint != model.fingerprint
    r = an.get_revenue_total(changed, {'stores': ['S1'], 'years': [2026]})
    assert r.value['total_revenue'] == 1025


# ------------------------------------------------------------------ filters
def test_filters_and_aliases(model):
    f, err = an.normalize_filters(model, {'channels': ['online'], 'years': ['2025']})
    assert err is None and f.channels == ['Website'] and f.years == [2025]
    assert an.apply_filters(model.sales, f)['Revenue'].sum() == 400


def test_unknown_values_return_an_error_not_zero(model):
    r = an.get_revenue_total(model, {'years': [2031]})
    assert r.error and r.value is None
    assert an.get_revenue_total(model, {'stores': ['Atlantis']}).error


def test_city_resolves_to_store_id(model):
    assert an.get_revenue_total(model, {'stores': ['chicago']}).value['total_revenue'] == 75


def test_dashboard_filters_combine_with_tool_filters(model):
    base = an.Filters(years=[2026])
    r = an.get_revenue_total(model, {'channels': ['Website']}, base=base)
    assert r.value['total_revenue'] == 400 and r.scope['dashboard_filters_applied']


# -------------------------------------------------------------------- tools
def test_breakdown_by_channel(model):
    r = an.get_revenue_breakdown(model, 'channel')
    groups = {g['group']: g for g in r.value['groups']}
    assert groups['Website']['revenue'] == 800 and groups['Brick & Mortar']['revenue'] == 225
    assert groups['Website']['share_pct'] == round(800 / 1025 * 100, 2)


def test_growth_by_store(model):
    r = an.get_revenue_growth(model, 'store')
    g = {x['group']: x for x in r.value['groups']}
    assert g['S1 Chicago, IL']['pct_change'] == -50.0
    assert g['W0 Website']['pct_change'] == 0.0            # 400 -> 400
    assert g['S2 Denver, CO']['pct_change'] is None        # no 2025 revenue
    assert r.value['summary']['largest_pct_decline'] == 'S1 Chicago, IL'


def test_growth_says_when_nothing_declined(model):
    r = an.get_revenue_growth(model, 'channel', filters={'stores': ['W0']})
    assert r.value['summary']['largest_pct_decline'] is None
    assert any('No channel declined' in c for c in r.caveats)


def test_monthly_share_uses_same_year_denominator(model):
    m = an.monthly_share(model.sales)
    assert m.groupby('Year')['Share %'].sum().round(6).eq(100).all()
    jan25 = m[(m['Year'] == 2025) & (m['MonthNo'] == 1)]['Share %'].iloc[0]
    assert jan25 == pytest.approx(150 / 450 * 100)


def test_survey_joins_at_store_year_grain(model):
    sy = an.store_year_survey(model, model.sales)
    assert sy['Revenue'].sum() == 1025                     # no duplication
    s1_26 = sy[(sy['Store ID'] == 'S1') & (sy['Year'] == 2026)].iloc[0]
    assert s1_26['Revenue'] == 25 and s1_26['Satisfaction'] == 2.0


def test_satisfaction_tool_reports_missing_and_causation(model):
    r = an.get_satisfaction_vs_revenue(model)
    assert r.value['store_years_with_scores'] == 3
    assert r.value['store_years_without_scores'] == 2      # website, both years
    assert any('does not show' in c for c in r.caveats)


def test_coverage_lists_what_is_missing(model):
    r = an.get_data_coverage(model)
    assert any('profit' in x for x in r.value['not_in_the_data'])


def test_grain_table_counts_rows_and_duplicate_keys(model):
    g = an.grain_table(model).set_index('table')
    assert g.loc['sales', 'rows'] == 6 and g.loc['sales', 'duplicate keys'] == 0
    assert bool(g.loc['sales', 'contains revenue?']) and not bool(g.loc['stores', 'contains revenue?'])
    assert g.loc['survey', 'rows'] == 4
