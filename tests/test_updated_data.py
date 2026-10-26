"""Guide Section 5: in updated data, values change, rows come and go, values may be blank,
and a store, product, category or year may appear or disappear. None of that may crash
the app, and every problem must be reported."""
import io
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import analytics as an  # noqa: E402
from test_analytics import sales_df, tables  # noqa: E402

QUESTIONS = ['How much revenue did the website generate in 2026?',
             'Which store experienced the greatest percentage revenue decline?',
             'Which store is the most profitable?',
             'Does higher customer satisfaction cause higher revenue?',
             'Should E-Presso close its Chicago store?',
             'What is the busiest month?', 'Any data quality problems?',
             'Compare revenue by category', 'Revenue by year']


def exercise_everything(m):
    """Run every tool and every sample question; nothing may raise."""
    for dim in an.BREAKDOWN_DIMS:
        an.get_revenue_breakdown(m, dim)
    for dim in an.GROWTH_DIMS:
        an.get_revenue_growth(m, dim)
    an.get_revenue_total(m)
    an.get_monthly_revenue_share(m)
    an.get_satisfaction_vs_revenue(m)
    an.get_data_quality_report(m)
    an.get_data_coverage(m)
    an.grain_table(m)
    an.kpis(m.sales)
    for q in QUESTIONS:
        out = agent.answer_question(q, m, mode='rules')
        assert '**Next steps:**' in out['text'] or 'can only answer' in out['text']


def checks(m):
    return {i['check'] for i in m.issues}


def test_blank_values_are_reported_not_fatal():
    s = sales_df().astype(object)
    s.loc[0, 'Revenue'] = None
    s.loc[1, 'Store ID'] = None
    s.loc[2, 'Category ID'] = None
    s.loc[3, 'Date'] = None
    sv = tables()['survey'].astype(object)
    sv.loc[0, 'Satisfaction'] = None
    sv.loc[1, 'Year'] = None
    m = an.build_model(tables(sales=s, survey=sv))
    exercise_everything(m)
    assert {'Missing revenue', 'Missing Store ID', 'Missing Category ID',
            'Missing or invalid date'} <= checks(m)
    assert m.sales['Revenue'].sum() == 1025 - 100 - 50     # blank revenue and blank date excluded
    assert 'Unknown' in set(m.sales['Store'])


def test_new_store_appears():
    s = pd.concat([sales_df(), pd.DataFrame([{'Sales ID': 'g', 'Date': '2026-03-31', 'Store ID': 'S9',
                                              'StoreYear': 'S9_26', 'Category ID': 'C1',
                                              'Product ID': 'P1', 'Revenue': 999}])])
    m = an.build_model(tables(sales=s))
    exercise_everything(m)
    assert m.sales['Revenue'].sum() == 1025 + 999           # still counted
    assert 'Store ID not found in lookup table' in checks(m)
    assert an.get_revenue_total(m, {'stores': ['S9']}).value['total_revenue'] == 999


def test_store_and_year_disappear():
    s = sales_df()
    s = s[(s['Store ID'] != 'S1') & (~s['Date'].str.startswith('2025'))]
    m = an.build_model(tables(sales=s))
    exercise_everything(m)
    assert m.years == [2026]
    assert an.get_revenue_total(m, {'years': [2025]}).error         # no guessing
    assert an.get_revenue_growth(m, 'store').error                  # needs two years
    assert 'S1' not in set(m.sales['Store ID'])


def test_new_category_and_product_appear():
    s = sales_df()
    s.loc[5, ['Category ID', 'Product ID']] = ['C7', 'P7']
    m = an.build_model(tables(sales=s))
    exercise_everything(m)
    assert {'C7', 'P7'} <= set(m.sales['Category']) | set(m.sales['Product'])


def test_new_year_appears():
    s = pd.concat([sales_df(), pd.DataFrame([{'Sales ID': 'h', 'Date': '2030-01-31', 'Store ID': 'S1',
                                              'StoreYear': 'S1_30', 'Category ID': 'C1',
                                              'Product ID': 'P1', 'Revenue': 10}])])
    m = an.build_model(tables(sales=s))
    exercise_everything(m)
    assert m.years == [2025, 2026, 2030]
    assert an.get_revenue_growth(m, 'store').value['end_year'] == 2030


def test_optional_tables_missing():
    m = an.build_model({'sales': sales_df()})
    exercise_everything(m)
    assert set(m.missing_tables) == {'stores', 'categories', 'products', 'survey'}
    assert an.get_satisfaction_vs_revenue(m).error


def test_missing_required_column_gets_a_plain_message():
    buf = io.BytesIO()
    sales_df().drop(columns='Revenue').to_excel(buf, sheet_name='Sales', index=False)
    found, log = an.load_files([('sales.xlsx', buf.getvalue())])
    assert 'sales' not in found
    assert 'missing required column(s): Revenue' in log[0]['note']
    with pytest.raises(ValueError, match='missing required column\\(s\\): Revenue'):
        an.build_model(found, log)


def test_rows_removed_change_totals():
    m = an.build_model(tables(sales=sales_df().iloc[:-1]))
    assert m.sales['Revenue'].sum() == 1025 - 150


def test_all_survey_scores_blank():
    sv = tables()['survey'].assign(Satisfaction=np.nan)
    m = an.build_model(tables(survey=sv))
    exercise_everything(m)
    assert an.get_satisfaction_vs_revenue(m).value['pearson_r_satisfaction_vs_revenue'] is None
