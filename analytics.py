"""Business logic. Keep Streamlit code OUT of this file so every function can be
tested on its own and reused by the assistant.

Layout of this file:
  1. Loading      - read uploaded files and recognise which E-Presso table each one is
  2. Model        - type the columns, join the tables safely, record data-quality issues
  3. Filters      - one definition of "the current subset", shared by dashboard and agent
  4. Metrics      - plain pandas calculations used by the dashboard charts
  5. Tools        - the ONLY functions the assistant may call; each returns a ToolResult
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import asdict, dataclass, field, fields

import numpy as np
import pandas as pd

EXCEL_EXT = ('.xlsx', '.xlsm', '.xls')
TEXT_EXT = ('.csv', '.txt', '.tsv')

MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
WEBSITE = 'Website'
BRICK = 'Brick & Mortar'

# Columns that identify each source table. Matching ignores case, spaces and
# punctuation, so "Store ID", "store_id" and "StoreID" are all the same column.
TABLE_SPECS = {
    'sales': {
        'required': ['Sales ID', 'Date', 'Store ID', 'Category ID', 'Product ID', 'Revenue'],
        'optional': ['StoreYear'],
    },
    'stores': {
        'required': ['ID', 'StoreType'],
        'optional': ['City', 'State', 'PostalCode', 'Market', 'Location', 'Phone', 'Manager'],
    },
    'categories': {'required': ['Category ID', 'Category'], 'optional': []},
    'products': {'required': ['Product ID', 'Product'], 'optional': []},
    'survey': {
        'required': ['Survey ID', 'Store ID', 'Year', 'Satisfaction'],
        'optional': ['Interest'],
    },
}

# What one row of each table represents. Shown in the app and the README.
GRAIN = {
    'sales': 'One row per store, per product, per month (Date is the last day of the month). '
             'Revenue is the monthly revenue for that store-product.',
    'stores': 'One row per store. SH00 is the website; every other ID is a physical store.',
    'categories': 'One row per product category.',
    'products': 'One row per product.',
    'survey': 'One row per store per year: average satisfaction (0-5) and interest (0-100) '
              'from the annual market survey.',
}

KEYS = {'sales': 'Sales ID', 'stores': 'ID', 'categories': 'Category ID', 'products': 'Product ID',
        'survey': 'Survey ID (= Store ID + year)'}

# Things a manager might ask about that this data does not contain.
NOT_IN_DATA = [
    'costs, expenses, or cost of goods sold',
    'profit or profit margin',
    'prices, unit quantities, or number of transactions',
    'customer counts, customer identities, or foot traffic',
    'staffing, wages, rent, or lease terms',
    'marketing spend or promotions',
    'survey responses for the website (SH00 has no survey scores)',
]


def _norm(name) -> str:
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


# ---------------------------------------------------------------- 1. loading
def excel_sheet_names(file_bytes: bytes) -> list[str]:
    return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names


def load_table(file_bytes: bytes, filename: str, sheet_name=0) -> pd.DataFrame:
    """Read one uploaded CSV / TXT / TSV / Excel sheet into a DataFrame (header in row 1)."""
    name = filename.lower()
    buffer = io.BytesIO(file_bytes)
    if name.endswith(EXCEL_EXT):
        return pd.read_excel(buffer, sheet_name=sheet_name)
    if name.endswith(TEXT_EXT):
        return pd.read_csv(buffer, sep=None, engine='python')  # sniffs the delimiter
    raise ValueError(f'Unsupported file type: {filename}')


def _read_text_raw(file_bytes: bytes) -> pd.DataFrame:
    """Read a delimited text file with no assumptions about where the header is."""
    text = file_bytes.decode('utf-8-sig', errors='replace')
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters='\t,;|')
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = '\t' if sample.count('\t') > sample.count(',') else ','
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    width = max((len(r) for r in rows), default=0)
    rows = [r + [None] * (width - len(r)) for r in rows]
    raw = pd.DataFrame(rows, dtype=object)
    return raw.replace({'': None})


def read_raw_sheets(file_bytes: bytes, filename: str) -> dict[str, pd.DataFrame]:
    """Every sheet in the file, read without a header row (the header is found later)."""
    name = filename.lower()
    if name.endswith(EXCEL_EXT):
        book = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None, dtype=object)
        return {f'{filename} [{sheet}]': df for sheet, df in book.items()}
    if name.endswith(TEXT_EXT):
        return {filename: _read_text_raw(file_bytes)}
    raise ValueError(f'Unsupported file type: {filename}')


def identify_table(raw: pd.DataFrame, max_scan: int = 15):
    """Find the header row and decide which E-Presso table this is.
    Returns (kind, header_row_index) or (None, None)."""
    best = (None, None, 0)
    for i in range(min(max_scan, len(raw))):
        cells = {_norm(v) for v in raw.iloc[i].tolist() if v is not None and str(v).strip()}
        for kind, spec in TABLE_SPECS.items():
            required = {_norm(c) for c in spec['required']}
            if required <= cells and len(required) > best[2]:
                best = (kind, i, len(required))
        if best[0]:
            return best[0], best[1]
    return None, None


def near_miss(raw: pd.DataFrame, max_scan: int = 15):
    """A sheet that has most, but not all, of a table's required columns.
    Returns (kind, [missing column names]) or (None, [])."""
    best = (None, [], 0.0)
    for i in range(min(max_scan, len(raw))):
        cells = {_norm(v) for v in raw.iloc[i].tolist() if v is not None and str(v).strip()}
        for kind, spec in TABLE_SPECS.items():
            req = spec['required']
            hit = [c for c in req if _norm(c) in cells]
            frac = len(hit) / len(req)
            if len(hit) >= 2 and frac >= 0.5 and frac > best[2]:
                best = (kind, [c for c in req if _norm(c) not in cells], frac)
    return best[0], best[1]


def extract_table(raw: pd.DataFrame, kind: str, header_row: int) -> pd.DataFrame:
    """Cut the table out below its header row and rename columns to canonical names."""
    spec = TABLE_SPECS[kind]
    canonical = {_norm(c): c for c in spec['required'] + spec['optional']}
    header = [str(v).strip() if v is not None else '' for v in raw.iloc[header_row].tolist()]
    body = raw.iloc[header_row + 1:].copy()
    body.columns = [canonical.get(_norm(h), h) for h in header]
    body = body.loc[:, [c for c in body.columns if c]]           # drop unnamed columns
    body = body.dropna(how='all').reset_index(drop=True)          # drop blank rows
    return body


def load_files(files: list[tuple[str, bytes]]):
    """Read every uploaded file and sort its sheets into E-Presso tables.
    `files` is a list of (filename, bytes). Returns (tables, file_log)."""
    tables: dict[str, pd.DataFrame] = {}
    log = []
    for filename, data in files:
        try:
            sheets = read_raw_sheets(data, filename)
        except Exception as exc:  # report the problem, keep loading the other files
            log.append({'source': filename, 'table': None, 'rows': 0,
                        'note': f'Could not read file: {exc}'})
            continue
        for source, raw in sheets.items():
            kind, header_row = identify_table(raw)
            if kind is None:
                guess, missing = near_miss(raw)
                if guess:
                    log.append({'source': source, 'table': None, 'rows': len(raw),
                                'note': f'Looks like the {guess} table but is missing required '
                                        f'column(s): {", ".join(missing)}. Not loaded.',
                                'near_miss': guess, 'missing_columns': missing})
                else:
                    log.append({'source': source, 'table': None, 'rows': len(raw),
                                'note': 'Not an E-Presso data table (ignored).'})
                continue
            table = extract_table(raw, kind, header_row)
            note = f'Header found in row {header_row + 1}.'
            if kind in tables:
                note += f' Replaces an earlier {kind} table (the last upload wins).'
            tables[kind] = table
            log.append({'source': source, 'table': kind, 'rows': len(table), 'note': note})
    return tables, log


def grain_table(model: 'Model') -> pd.DataFrame:
    """One row per source table: where it came from, what a row means, its key, size,
    and whether it holds revenue. Built from the loaded data, not typed in."""
    sources = {e['table']: e['source'] for e in model.file_log if e.get('table')}
    frames = {'sales': model.sales, 'stores': model.stores, 'categories': model.categories,
              'products': model.products, 'survey': model.survey}
    rows = []
    for kind, df in frames.items():
        if df is None:
            rows.append({'table': kind, 'source file / sheet': 'not uploaded', 'one row =': GRAIN[kind],
                         'key column(s)': KEYS[kind], 'rows': 0, 'contains revenue?': kind == 'sales',
                         'duplicate keys': None})
            continue
        if kind == 'survey':
            dupes = int(df.duplicated(subset=['Store ID', 'Year']).sum())
        else:
            dupes = int(df[KEYS[kind]].duplicated().sum())
        rows.append({'table': kind, 'source file / sheet': sources.get(kind, ''),
                     'one row =': GRAIN[kind], 'key column(s)': KEYS[kind],
                     'rows': model.raw_sales_rows if kind == 'sales' else len(df),
                     'contains revenue?': kind == 'sales', 'duplicate keys': dupes})
    return pd.DataFrame(rows)


def profile_table(df: pd.DataFrame) -> dict:
    """Basic profile: size, duplicates, missing values, data types."""
    missing = df.isna().sum()
    return {
        'rows': len(df),
        'columns': df.shape[1],
        'duplicate_rows': int(df.duplicated().sum()),
        'missing': pd.DataFrame({
            'column': missing.index,
            'missing': missing.values,
            'pct_missing': (missing.values / max(len(df), 1) * 100).round(2),
        }),
        'dtypes': pd.DataFrame({'column': df.columns, 'dtype': df.dtypes.astype(str).values}),
    }


# ------------------------------------------------------------------ 2. model
def _clean_id(s: pd.Series) -> pd.Series:
    out = s.astype('string').str.strip()
    return out.where(out != '', pd.NA)


def _to_number(s: pd.Series) -> pd.Series:
    text = s.astype('string').str.replace(r'[$,\s]', '', regex=True)
    return pd.to_numeric(text, errors='coerce')


def _to_date(s: pd.Series) -> pd.Series:
    """Dates may arrive as real dates, text, or Excel serial numbers."""
    parsed = pd.to_datetime(s, errors='coerce')
    numeric = pd.to_numeric(s, errors='coerce')
    serial = numeric.between(20000, 80000)
    if serial.any():
        parsed = parsed.where(~serial, pd.to_datetime('1899-12-30') +
                              pd.to_timedelta(numeric.where(serial), unit='D'))
    return parsed


def _year_from_text(s: pd.Series) -> pd.Series:
    """'Y2025', '2025' or 2025 -> 2025."""
    return pd.to_numeric(s.astype('string').str.extract(r'(\d{4})')[0], errors='coerce').astype('Int64')


def check_merge(left: pd.DataFrame, right: pd.DataFrame, on, value_col: str, how: str = 'left',
                right_on=None):
    """Merge, and report whether the join changed the row count or the revenue sum.
    `value_col` must be a numeric column in `left`. Returns (merged, report)."""
    before_rows = len(left)
    before_sum = float(pd.to_numeric(left[value_col], errors='coerce').sum())
    if right_on is None:
        merged = left.merge(right, on=on, how=how)
    else:
        merged = left.merge(right, left_on=on, right_on=right_on, how=how)
    after_rows = len(merged)
    after_sum = float(pd.to_numeric(merged[value_col], errors='coerce').sum())
    fan_out = after_rows != before_rows or abs(after_sum - before_sum) > 1e-6
    report = {
        'rows_before': before_rows, 'rows_after': after_rows,
        'sum_before': before_sum, 'sum_after': after_sum,
        'changed': fan_out,
        'warning': ('The join changed the row count or the total. Check the grain and keys '
                    'of both tables before using this result.') if fan_out else '',
    }
    return merged, report


@dataclass
class Model:
    """Everything the dashboard and the assistant need, built once per upload."""
    sales: pd.DataFrame                    # clean fact table, joined to all lookups
    stores: pd.DataFrame | None
    categories: pd.DataFrame | None
    products: pd.DataFrame | None
    survey: pd.DataFrame | None
    raw_sales_rows: int
    issues: list = field(default_factory=list)
    merges: list = field(default_factory=list)
    file_log: list = field(default_factory=list)
    missing_tables: list = field(default_factory=list)

    @property
    def years(self) -> list[int]:
        return sorted(int(y) for y in self.sales['Year'].dropna().unique())

    @property
    def fingerprint(self) -> str:
        """Short ID for the loaded sales data. It changes whenever any sales value changes."""
        h = pd.util.hash_pandas_object(self.sales[['Sales ID', 'Date', 'Store ID', 'Revenue']],
                                       index=False).values
        return hashlib.sha1(h.tobytes()).hexdigest()[:8]

    def dataset_info(self) -> dict:
        return {
            'fingerprint': self.fingerprint,
            'sales_rows_used': int(len(self.sales)),
            'sales_rows_in_file': int(self.raw_sales_rows),
            'period': f'{self.sales["Date"].min():%b %Y} to {self.sales["Date"].max():%b %Y}'
            if len(self.sales) else 'no dates',
            'total_revenue_all_data': round(float(self.sales['Revenue'].sum()), 2),
        }


def _issue(issues, severity, table, check, detail, count=None, examples=None):
    issues.append({'severity': severity, 'table': table, 'check': check, 'detail': detail,
                   'count': count, 'examples': examples or []})


def _dedupe_lookup(df, key, table, issues):
    """A lookup table with a repeated key would duplicate sales rows in a join."""
    dupes = df[key][df[key].duplicated(keep=False) & df[key].notna()]
    if len(dupes):
        _issue(issues, 'error', table, f'Duplicate {key}',
               f'{dupes.nunique()} {key} value(s) appear more than once. The first row for each '
               f'was kept so the join cannot duplicate revenue.', dupes.nunique(),
               sorted(dupes.unique().tolist())[:10])
    missing = df[key].isna().sum()
    if missing:
        _issue(issues, 'warning', table, f'Missing {key}', f'{missing} row(s) have no {key} and '
               'were dropped.', int(missing))
    return df[df[key].notna()].drop_duplicates(subset=[key], keep='first')


def build_model(tables: dict, file_log: list | None = None) -> Model:
    """Type every column, check the data, and join lookups onto sales without changing
    the sales row count or the revenue total (each join is verified)."""
    if 'sales' not in tables:
        for entry in file_log or []:
            if entry.get('near_miss') == 'sales':
                raise ValueError(f'The Sales table in {entry["source"]} is missing required '
                                 f'column(s): {", ".join(entry["missing_columns"])}. The app needs '
                                 'Sales ID, Date, Store ID, Category ID, Product ID and Revenue.')
        raise ValueError('No Sales table found. Upload the file that contains Sales ID, Date, '
                         'Store ID, Category ID, Product ID and Revenue.')
    issues: list = []
    merges: list = []

    # ---- sales
    s = tables['sales'].copy()
    for col in ['Sales ID', 'Store ID', 'Category ID', 'Product ID', 'StoreYear']:
        if col in s.columns:
            s[col] = _clean_id(s[col])
    raw_revenue = s['Revenue']
    s['Revenue'] = _to_number(s['Revenue']).astype(float)
    s['Date'] = _to_date(s['Date'])
    raw_rows = len(s)

    bad_rev = s['Revenue'].isna() & raw_revenue.notna()
    if bad_rev.any():
        _issue(issues, 'error', 'sales', 'Non-numeric revenue',
               f'{int(bad_rev.sum())} row(s) have a revenue value that is not a number. '
               'They are excluded from every calculation.', int(bad_rev.sum()),
               s.loc[bad_rev, 'Sales ID'].head(10).tolist())
    miss_rev = raw_revenue.isna()
    if miss_rev.any():
        _issue(issues, 'error', 'sales', 'Missing revenue',
               f'{int(miss_rev.sum())} row(s) have no revenue and are excluded.',
               int(miss_rev.sum()), s.loc[miss_rev, 'Sales ID'].head(10).tolist())
    bad_date = s['Date'].isna()
    if bad_date.any():
        excluded = s.loc[bad_date, 'Revenue'].sum()
        _issue(issues, 'error', 'sales', 'Missing or invalid date',
               f'{int(bad_date.sum())} row(s) have no usable date and are excluded '
               f'(${excluded:,.2f} of revenue).', int(bad_date.sum()),
               s.loc[bad_date, 'Sales ID'].head(10).tolist())
    for col in ['Store ID', 'Category ID', 'Product ID']:
        n = int(s[col].isna().sum())
        if n:
            _issue(issues, 'warning', 'sales', f'Missing {col}',
                   f'{n} row(s) have no {col}. They count in totals but show as "Unknown" '
                   'in breakdowns.', n)
    neg = s['Revenue'] < 0
    if neg.any():
        _issue(issues, 'warning', 'sales', 'Negative revenue',
               f'{int(neg.sum())} row(s) have negative revenue (possible returns or errors). '
               'They are included in totals.', int(neg.sum()),
               s.loc[neg, 'Sales ID'].head(10).tolist())
    zero = s['Revenue'] == 0
    if zero.any():
        _issue(issues, 'info', 'sales', 'Zero revenue', f'{int(zero.sum())} row(s) have zero revenue.',
               int(zero.sum()))
    full_dupes = s.duplicated(keep='first')
    if full_dupes.any():
        _issue(issues, 'warning', 'sales', 'Duplicate rows',
               f'{int(full_dupes.sum())} row(s) are exact copies of another row. They are '
               'INCLUDED in totals; remove them from the file if they are errors.',
               int(full_dupes.sum()), s.loc[full_dupes, 'Sales ID'].head(10).tolist())
    id_dupes = s['Sales ID'].duplicated(keep=False) & s['Sales ID'].notna() & ~s.duplicated(keep=False)
    if id_dupes.any():
        _issue(issues, 'warning', 'sales', 'Repeated Sales ID',
               f'{int(id_dupes.sum())} row(s) share a Sales ID with a different row.',
               int(id_dupes.sum()), s.loc[id_dupes, 'Sales ID'].head(10).tolist())

    s = s[s['Revenue'].notna() & s['Date'].notna()].copy()
    s['Year'] = s['Date'].dt.year.astype(int)
    s['MonthNo'] = s['Date'].dt.month.astype(int)
    s['Month'] = s['MonthNo'].map(lambda m: MONTH_NAMES[m - 1])
    s['YearMonth'] = s['Date'].dt.to_period('M').dt.to_timestamp()

    if 'StoreYear' in s.columns:
        expected = s['Store ID'] + '_' + (s['Year'] % 100).astype(str).str.zfill(2)
        mismatch = s['StoreYear'].notna() & (s['StoreYear'] != expected)
        if mismatch.any():
            _issue(issues, 'warning', 'sales', 'StoreYear does not match Store ID and Date',
                   f'{int(mismatch.sum())} row(s). Survey joins use Store ID + year from Date, '
                   'not StoreYear, so these rows still join correctly.', int(mismatch.sum()),
                   s.loc[mismatch, 'Sales ID'].head(10).tolist())

    multi_cat = s.groupby('Product ID')['Category ID'].nunique()
    multi_cat = multi_cat[multi_cat > 1]
    if len(multi_cat):
        _issue(issues, 'warning', 'sales', 'Product in more than one category',
               f'{len(multi_cat)} product(s) appear under more than one Category ID.',
               len(multi_cat), multi_cat.index.tolist()[:10])

    # ---- stores
    stores = None
    if 'stores' in tables:
        st = tables['stores'].copy()
        for col in st.columns:
            st[col] = _clean_id(st[col])
        st = _dedupe_lookup(st, 'ID', 'stores', issues)
        st['Channel'] = np.where(st['StoreType'].str.lower().str.contains('web', na=False),
                                 WEBSITE, np.where(st['StoreType'].notna(), BRICK, 'Unknown'))
        label_city = st.get('City', pd.Series(pd.NA, index=st.index))
        label_state = st.get('State', pd.Series(pd.NA, index=st.index))
        st['Store'] = np.where(st['Channel'] == WEBSITE, st['ID'] + ' Website',
                               st['ID'] + ' ' + label_city.fillna('?') + ', ' + label_state.fillna('?'))
        physical = st[st['Channel'] == BRICK]
        for col in ['City', 'State']:
            if col in st.columns and physical[col].isna().any():
                ids = physical.loc[physical[col].isna(), 'ID'].tolist()
                _issue(issues, 'warning', 'stores', f'Missing {col}',
                       f'{len(ids)} physical store(s) have no {col}.', len(ids), ids)
        if {'State', 'Location'} <= set(st.columns):
            regions = physical.groupby('State')['Location'].nunique()
            split = regions[regions > 1].index.tolist()
            if split:
                _issue(issues, 'info', 'stores', 'Same state in several regions',
                       'Stores in the same state are assigned to different regions (Location). '
                       'Region results depend on these labels.', len(split), split)
        stores = st
    else:
        _issue(issues, 'warning', 'stores', 'Stores table missing',
               'No stores table was uploaded, so channel (website vs brick & mortar) and region '
               'breakdowns are unavailable.')

    # ---- categories / products
    cats = prods = None
    if 'categories' in tables:
        cats = tables['categories'].copy()
        cats['Category ID'] = _clean_id(cats['Category ID'])
        cats['Category'] = cats['Category'].astype('string').str.strip()
        cats = _dedupe_lookup(cats[['Category ID', 'Category']], 'Category ID', 'categories', issues)
    if 'products' in tables:
        prods = tables['products'].copy()
        prods['Product ID'] = _clean_id(prods['Product ID'])
        prods['Product'] = prods['Product'].astype('string').str.strip()
        prods = _dedupe_lookup(prods[['Product ID', 'Product']], 'Product ID', 'products', issues)
        # Different products can share a name (e.g. three "Base Model" products). Grouping by
        # name would add them together, so shared names get their ID appended.
        shared = prods['Product'].duplicated(keep=False) & prods['Product'].notna()
        if shared.any():
            names = sorted(prods.loc[shared, 'Product'].unique().tolist())
            _issue(issues, 'warning', 'products', 'Different products share a name',
                   f'{int(shared.sum())} products share the name(s) {names}. They are shown as '
                   '"Name (ID)" so their revenue is never combined.', int(shared.sum()),
                   prods.loc[shared, 'Product ID'].tolist())
            prods.loc[shared, 'Product'] = prods.loc[shared, 'Product'] + ' (' + prods.loc[shared, 'Product ID'] + ')'

    # ---- joins (each one verified: same rows, same revenue)
    if stores is not None:
        keep = [c for c in ['ID', 'Store', 'Channel', 'StoreType', 'City', 'State', 'Market',
                            'Location'] if c in stores.columns]
        s, rep = check_merge(s, stores[keep], on='Store ID', right_on='ID', value_col='Revenue')
        s = s.drop(columns='ID')
        merges.append({'join': 'Sales[Store ID] -> Stores[ID] (many-to-one)', **rep})
    else:
        s['Store'] = s['Store ID']
        s['Channel'] = 'Unknown'
    if cats is not None:
        s, rep = check_merge(s, cats, on='Category ID', value_col='Revenue')
        merges.append({'join': 'Sales[Category ID] -> Categories[Category ID] (many-to-one)', **rep})
    else:
        s['Category'] = s['Category ID']
    if prods is not None:
        s, rep = check_merge(s, prods, on='Product ID', value_col='Revenue')
        merges.append({'join': 'Sales[Product ID] -> Products[Product ID] (many-to-one)', **rep})
    else:
        s['Product'] = s['Product ID']

    for col, lookup in [('Store', 'Store ID'), ('Category', 'Category ID'), ('Product', 'Product ID')]:
        orphan = s[col].isna() & s[lookup].notna()
        if orphan.any():
            ids = sorted(s.loc[orphan, lookup].unique().tolist())
            _issue(issues, 'error', 'sales', f'{lookup} not found in lookup table',
                   f'{int(orphan.sum())} sales row(s) (${s.loc[orphan, "Revenue"].sum():,.2f}) use '
                   f'{lookup} values missing from the lookup table. They count in totals and show '
                   'as the raw ID in breakdowns.', int(orphan.sum()), ids[:10])
        s[col] = s[col].fillna(s[lookup]).fillna('Unknown')
    s['Channel'] = s['Channel'].fillna('Unknown')
    if 'Location' not in s.columns:
        s['Location'] = 'Unknown'
    s['Location'] = s['Location'].fillna('Unknown')

    # Coverage: every store should report every month in the period.
    months_per_store = s.groupby('Store ID')['YearMonth'].nunique()
    all_months = s['YearMonth'].nunique()
    short = months_per_store[months_per_store < all_months]
    if len(short):
        _issue(issues, 'warning', 'sales', 'Stores missing months',
               f'{len(short)} store(s) have no sales in some months of the period, which affects '
               'trend and growth comparisons.', len(short), short.index.tolist()[:10])

    # ---- survey (joined at its own grain later, never onto sales rows)
    survey = None
    if 'survey' in tables:
        sv = tables['survey'].copy()
        sv['Survey ID'] = _clean_id(sv['Survey ID'])
        sv['Store ID'] = _clean_id(sv['Store ID'])
        sv['Year'] = _year_from_text(sv['Year'])
        sv['Satisfaction'] = _to_number(sv['Satisfaction'])
        sv['Interest'] = _to_number(sv['Interest']) if 'Interest' in sv.columns else np.nan
        dup = sv.duplicated(subset=['Store ID', 'Year'], keep='first')
        if dup.any():
            _issue(issues, 'error', 'survey', 'Duplicate store-year',
                   f'{int(dup.sum())} survey row(s) repeat a store-year; the first was kept.',
                   int(dup.sum()))
            sv = sv[~dup]
        miss = sv['Satisfaction'].isna()
        if miss.any():
            ids = sorted(sv.loc[miss, 'Store ID'].dropna().unique().tolist())
            _issue(issues, 'info', 'survey', 'Missing satisfaction',
                   f'{int(miss.sum())} store-year(s) have no satisfaction score (stores: '
                   f'{", ".join(ids)}). They are left out of satisfaction analysis.',
                   int(miss.sum()), ids)
        out_rng = ~sv['Satisfaction'].between(0, 5) & sv['Satisfaction'].notna()
        if out_rng.any():
            _issue(issues, 'error', 'survey', 'Satisfaction outside 0-5',
                   f'{int(out_rng.sum())} score(s) are outside the 0-5 scale.', int(out_rng.sum()),
                   sv.loc[out_rng, 'Survey ID'].tolist()[:10])
        int_rng = ~sv['Interest'].between(0, 100) & sv['Interest'].notna()
        if int_rng.any():
            _issue(issues, 'error', 'survey', 'Interest outside 0-100',
                   f'{int(int_rng.sum())} score(s) are outside the 0-100 scale.', int(int_rng.sum()))
        if stores is not None:
            unknown = sorted(set(sv['Store ID'].dropna()) - set(stores['ID']))
            if unknown:
                _issue(issues, 'warning', 'survey', 'Survey store not in stores table',
                       f'{len(unknown)} survey store ID(s) are not in the stores table.',
                       len(unknown), unknown)
        survey = sv

    if survey is not None:
        # Prove the survey join is safe: revenue at store-year grain, before and after the join.
        rev_sy = s.groupby(['Store ID', 'Year'], as_index=False)['Revenue'].sum()
        sv_key = survey[['Store ID', 'Year', 'Satisfaction']].dropna(subset=['Store ID', 'Year']).copy()
        sv_key['Year'] = sv_key['Year'].astype(int)
        _, rep = check_merge(rev_sy, sv_key, on=['Store ID', 'Year'], value_col='Revenue')
        merges.append({'join': 'Store-year revenue -> Survey[Store ID + Year] (one-to-one)', **rep})

    missing_tables = [k for k in TABLE_SPECS if k not in tables]
    return Model(sales=s.reset_index(drop=True), stores=stores, categories=cats, products=prods,
                 survey=survey, raw_sales_rows=raw_rows, issues=issues, merges=merges,
                 file_log=file_log or [], missing_tables=missing_tables)


# ---------------------------------------------------------------- 3. filters
CHANNEL_ALIASES = {
    'website': WEBSITE, 'web': WEBSITE, 'online': WEBSITE, 'ecommerce': WEBSITE, 'site': WEBSITE,
    'brickmortar': BRICK, 'brickandmortar': BRICK, 'bm': BRICK, 'physical': BRICK,
    'store': BRICK, 'stores': BRICK, 'physicalstores': BRICK, 'retail': BRICK, 'instore': BRICK,
}


@dataclass
class Filters:
    """A subset of the sales data. Empty field = no restriction on that field."""
    years: list = field(default_factory=list)
    channels: list = field(default_factory=list)
    regions: list = field(default_factory=list)
    stores: list = field(default_factory=list)      # Store IDs, e.g. SH11
    categories: list = field(default_factory=list)  # category names, e.g. Beans
    products: list = field(default_factory=list)    # product names

    def is_empty(self) -> bool:
        return not any(getattr(self, f.name) for f in fields(self))

    def describe(self) -> str:
        if self.is_empty():
            return 'All stores, all years (no filters)'
        parts = []
        for f in fields(self):
            vals = getattr(self, f.name)
            if vals:
                parts.append(f'{f.name}: {", ".join(map(str, vals))}')
        return '; '.join(parts)

    def to_dict(self) -> dict:
        return {f.name: list(getattr(self, f.name)) for f in fields(self) if getattr(self, f.name)}

    def intersect(self, other: 'Filters') -> 'Filters':
        """Combine two filter sets (both must hold)."""
        out = Filters()
        for f in fields(self):
            a, b = getattr(self, f.name), getattr(other, f.name)
            if a and b:
                setattr(out, f.name, [v for v in a if v in b])
                if not getattr(out, f.name):
                    setattr(out, f.name, ['<no overlap>'])
            else:
                setattr(out, f.name, list(a or b))
        return out


def resolve_store(model: Model, token: str):
    """'SH11', 'sh11', 'Chicago' -> ['SH11']. Returns (ids, error)."""
    if model.stores is None:
        ids = sorted(model.sales['Store ID'].dropna().unique())
        return ([token.upper()], None) if token.upper() in ids else ([], f'Unknown store {token}')
    st = model.stores
    t = token.strip().lower()
    # A store can appear in updated sales data before it is added to the stores file.
    if t.upper() in set(st['ID']) | set(model.sales['Store ID'].dropna()):
        return [t.upper()], None
    if t in ('website', 'web', 'online'):
        return st.loc[st['Channel'] == WEBSITE, 'ID'].tolist(), None
    if 'City' in st.columns:
        hits = st[st['City'].str.lower() == t]
        if len(hits) == 1:
            return hits['ID'].tolist(), None
        if len(hits) > 1:
            return [], (f'"{token}" matches {len(hits)} stores: ' +
                        ', '.join(hits['Store'].tolist()) + '. Use a store ID.')
    return [], f'Unknown store "{token}". Valid stores: ' + ', '.join(st['Store'].tolist())


def _match_names(wanted, valid, label, aliases=None):
    out, bad = [], []
    lookup = {_norm(v): v for v in valid}
    for w in wanted:
        key = _norm(w)
        if aliases and key in aliases and aliases[key] in valid:
            out.append(aliases[key])
        elif key in lookup:
            out.append(lookup[key])
        else:
            bad.append(w)
    err = (f'{bad} not found in {label}. Valid values: {sorted(map(str, valid))}' if bad else None)
    return out, err


def normalize_filters(model: Model, raw: dict | Filters | None):
    """Turn user / model supplied values into exact values from the LOADED data.
    Unknown values return an error instead of silently matching nothing."""
    if isinstance(raw, Filters):
        raw = raw.to_dict()
    raw = raw or {}
    f = Filters()
    s = model.sales

    def as_list(v):
        if v is None or v == '':
            return []
        return list(v) if isinstance(v, (list, tuple, set)) else [v]

    years = as_list(raw.get('years'))
    if years:
        try:
            years = [int(str(y).strip()[:4]) for y in years]
        except ValueError:
            return None, f'Years must be numbers, got {years}'
        valid = set(model.years)
        bad = [y for y in years if y not in valid]
        if bad:
            return None, f'No data for year(s) {bad}. The loaded data covers {model.years}.'
        f.years = sorted(set(years))
    for name, col, label, aliases in [('channels', 'Channel', 'channels', CHANNEL_ALIASES),
                                      ('regions', 'Location', 'regions', None),
                                      ('categories', 'Category', 'categories', None),
                                      ('products', 'Product', 'products', None)]:
        vals = as_list(raw.get(name))
        if vals:
            matched, err = _match_names(vals, set(s[col].dropna().unique()), label, aliases)
            if err:
                return None, err
            setattr(f, name, sorted(set(matched)))
    store_vals = as_list(raw.get('stores'))
    for token in store_vals:
        ids, err = resolve_store(model, str(token))
        if err:
            return None, err
        f.stores.extend(i for i in ids if i not in f.stores)
    return f, None


def apply_filters(df: pd.DataFrame, f: Filters | None) -> pd.DataFrame:
    if f is None or f.is_empty():
        return df
    mask = pd.Series(True, index=df.index)
    for name, col in [('years', 'Year'), ('channels', 'Channel'), ('regions', 'Location'),
                      ('stores', 'Store ID'), ('categories', 'Category'), ('products', 'Product')]:
        vals = getattr(f, name)
        if vals:
            mask &= df[col].isin(vals)
    return df[mask]


# ---------------------------------------------------------------- 4. metrics
def revenue_by(df: pd.DataFrame, cols) -> pd.DataFrame:
    cols = [cols] if isinstance(cols, str) else list(cols)
    return df.groupby(cols, dropna=False, observed=True)['Revenue'].sum().reset_index()


def kpis(df: pd.DataFrame) -> dict:
    total = float(df['Revenue'].sum())
    web = float(df.loc[df['Channel'] == WEBSITE, 'Revenue'].sum())
    brick = float(df.loc[df['Channel'] == BRICK, 'Revenue'].sum())
    years = sorted(df['Year'].unique())
    yoy = None
    if len(years) >= 2:
        last, prev = years[-1], years[-2]
        r_last = df.loc[df['Year'] == last, 'Revenue'].sum()
        r_prev = df.loc[df['Year'] == prev, 'Revenue'].sum()
        if r_prev:
            yoy = {'from': int(prev), 'to': int(last), 'pct': float((r_last - r_prev) / r_prev * 100)}
    return {
        'total': total, 'website': web, 'brick': brick,
        'website_share': web / total * 100 if total else None,
        'stores': int(df['Store ID'].nunique()),
        'months': int(df['YearMonth'].nunique()),
        'avg_month': total / df['YearMonth'].nunique() if len(df) else 0.0,
        'yoy': yoy, 'rows': int(len(df)),
    }


def monthly_share(df: pd.DataFrame) -> pd.DataFrame:
    """Each month's revenue as a % of the SAME year's total (within the current subset)."""
    m = revenue_by(df, ['Year', 'MonthNo', 'Month'])
    annual = m.groupby('Year')['Revenue'].transform('sum')
    m['Share %'] = np.where(annual != 0, m['Revenue'] / annual * 100, np.nan)
    return m.sort_values(['Year', 'MonthNo'])


def growth_table(df: pd.DataFrame, by: str | None, start: int, end: int) -> pd.DataFrame:
    """Revenue in start year vs end year for each group, with change and % change."""
    sub = df[df['Year'].isin([start, end])]
    if by:
        t = sub.pivot_table(index=by, columns='Year', values='Revenue', aggfunc='sum',
                            fill_value=0.0, observed=True)
    else:
        t = pd.DataFrame([sub.groupby('Year')['Revenue'].sum()], index=['Total'])
    for y in (start, end):
        if y not in t.columns:
            t[y] = 0.0
    out = pd.DataFrame({'start': t[start], 'end': t[end]})
    out['change'] = out['end'] - out['start']
    out['pct_change'] = np.where(out['start'] != 0, out['change'] / out['start'] * 100, np.nan)
    out.index.name = 'group'
    return out.reset_index()


def store_year_survey(model: Model, df: pd.DataFrame) -> pd.DataFrame:
    """Revenue aggregated to the survey's grain (store + year) BEFORE joining the survey,
    so survey rows can never duplicate sales revenue."""
    rev = revenue_by(df, ['Store ID', 'Store', 'Channel', 'Year'])
    if model.survey is None:
        rev['Satisfaction'] = np.nan
        rev['Interest'] = np.nan
        return rev
    sv = model.survey[['Store ID', 'Year', 'Satisfaction', 'Interest']].copy()
    sv['Year'] = sv['Year'].astype('Int64')
    rev['Year'] = rev['Year'].astype('Int64')
    return rev.merge(sv, on=['Store ID', 'Year'], how='left', validate='one_to_one')


# ------------------------------------------------------------------ 5. tools
@dataclass
class ToolResult:
    """Every analytical function returns this. The assistant may only narrate it.
    Keep `value` to plain Python types (numbers, strings, dicts, lists)."""
    tool: str
    value: object
    definition: str
    scope: dict
    rows_used: int
    caveats: list = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _error(tool: str, message: str, scope: dict | None = None) -> ToolResult:
    return ToolResult(tool=tool, value=None, definition='', scope=scope or {},
                      rows_used=0, caveats=[], error=message)


REVENUE_CAVEAT = 'Revenue is sales income only. It is not profit; the data has no costs.'


def _prepare(model: Model, tool: str, filters, base: Filters | None):
    """Validate filters, combine with any dashboard filters, and return the subset."""
    f, err = normalize_filters(model, filters)
    if err:
        return None, None, _error(tool, err, {'requested_filters': filters})
    if base is not None and not base.is_empty():
        f = f.intersect(base)
    scope = {'filters': f.describe(),
             'dashboard_filters_applied': bool(base is not None and not base.is_empty()),
             'dataset': model.dataset_info()}
    sub = apply_filters(model.sales, f)
    if sub.empty:
        return None, None, ToolResult(tool=tool, value=None, definition='', scope=scope, rows_used=0,
                                      error='No sales rows match these filters, so there is nothing '
                                            'to calculate.')
    return sub, scope, None


def _money(x) -> float:
    return round(float(x), 2)


def _pct(x):
    return None if x is None or pd.isna(x) else round(float(x), 2)


def get_revenue_total(model: Model, filters: dict | None = None, base: Filters | None = None) -> ToolResult:
    """Total revenue for a subset, with its period and row count."""
    name = 'get_revenue_total'
    sub, scope, err = _prepare(model, name, filters, base)
    if err:
        return err
    k = kpis(sub)
    value = {
        'total_revenue': _money(k['total']),
        'period': f'{sub["Date"].min():%b %Y} to {sub["Date"].max():%b %Y}',
        'months_covered': k['months'],
        'stores_covered': k['stores'],
        'average_monthly_revenue': _money(k['avg_month']),
    }
    return ToolResult(tool=name, value=value, definition='Sum of Sales[Revenue] for the rows in scope',
                      scope=scope, rows_used=int(len(sub)), caveats=[REVENUE_CAVEAT])


BREAKDOWN_DIMS = {
    'channel': 'Channel', 'year': 'Year', 'month': 'Month', 'year_month': 'YearMonth',
    'store': 'Store', 'category': 'Category', 'product': 'Product', 'region': 'Location',
}


def get_revenue_breakdown(model: Model, dimension: str, filters: dict | None = None,
                          top_n: int | None = None, base: Filters | None = None) -> ToolResult:
    """Revenue by one dimension, each group's share of the subset total, ranked."""
    name = 'get_revenue_breakdown'
    if dimension not in BREAKDOWN_DIMS:
        return _error(name, f'Unknown dimension "{dimension}". Use one of {list(BREAKDOWN_DIMS)}.')
    sub, scope, err = _prepare(model, name, filters, base)
    if err:
        return err
    col = BREAKDOWN_DIMS[dimension]
    g = revenue_by(sub, col)
    total = g['Revenue'].sum()
    g['share_pct'] = g['Revenue'] / total * 100 if total else np.nan
    if dimension == 'month':
        order = {m: i for i, m in enumerate(MONTH_NAMES)}
        g = g.sort_values(col, key=lambda c: c.map(order))
    elif dimension in ('year', 'year_month'):
        g = g.sort_values(col)
    else:
        g = g.sort_values('Revenue', ascending=False)
    rows = []
    for rank, (_, r) in enumerate(g.iterrows(), start=1):
        label = r[col].strftime('%Y-%m') if dimension == 'year_month' else (
            int(r[col]) if dimension == 'year' else str(r[col]))
        rows.append({'group': label, 'revenue': _money(r['Revenue']), 'share_pct': _pct(r['share_pct']),
                     'rank_by_revenue': None})
    ranks = g['Revenue'].rank(ascending=False, method='min').astype(int).tolist()
    for row, rk in zip(rows, ranks):
        row['rank_by_revenue'] = rk
    truncated = False
    if top_n and dimension not in ('year', 'year_month', 'month') and len(rows) > top_n:
        rows, truncated = rows[:int(top_n)], True
    caveats = [REVENUE_CAVEAT, 'share_pct = group revenue / total revenue of the rows in scope.']
    if dimension == 'month':
        caveats.append('Month groups add the same calendar month across every year in scope.')
    if truncated:
        caveats.append(f'Only the top {top_n} groups are listed.')
    if dimension == 'region':
        caveats.append('Regions come from the stores table "Location" column; some labels look '
                       'inconsistent (see the data quality report).')
    return ToolResult(tool=name, value={'subset_total': _money(total), 'groups': rows},
                      definition=f'Sum of Sales[Revenue] grouped by {dimension}',
                      scope=scope, rows_used=int(len(sub)), caveats=caveats)


GROWTH_DIMS = {'total': None, 'channel': 'Channel', 'store': 'Store', 'category': 'Category',
               'product': 'Product', 'region': 'Location'}


def get_revenue_growth(model: Model, dimension: str = 'total', start_year: int | None = None,
                       end_year: int | None = None, filters: dict | None = None,
                       base: Filters | None = None) -> ToolResult:
    """Revenue change between two years, for each group, ranked by % change."""
    name = 'get_revenue_growth'
    if dimension not in GROWTH_DIMS:
        return _error(name, f'Unknown dimension "{dimension}". Use one of {list(GROWTH_DIMS)}.')
    filters = dict(filters or {})
    filters.pop('years', None)            # the two years are set by start_year / end_year
    sub, scope, err = _prepare(model, name, filters, base)
    if err:
        return err
    years = sorted(sub['Year'].unique())
    start = int(start_year) if start_year else int(years[0])
    end = int(end_year) if end_year else int(years[-1])
    missing = [y for y in (start, end) if y not in years]
    if missing:
        return _error(name, f'No data for {missing}. Years in scope: {[int(y) for y in years]}.', scope)
    if start >= end:
        return _error(name, 'start_year must be earlier than end_year.', scope)
    t = growth_table(sub, GROWTH_DIMS[dimension], start, end).sort_values('pct_change', ascending=False)
    groups = [{'group': str(r['group']), f'revenue_{start}': _money(r['start']),
               f'revenue_{end}': _money(r['end']), 'change': _money(r['change']),
               'pct_change': _pct(r['pct_change'])} for _, r in t.iterrows()]
    valid = t.dropna(subset=['pct_change'])
    declined = valid[valid['pct_change'] < 0]
    grew = valid[valid['pct_change'] > 0]
    summary = {
        'largest_pct_increase': str(grew.iloc[0]['group']) if len(grew) else None,
        'largest_pct_decline': str(declined.sort_values('pct_change').iloc[0]['group']) if len(declined) else None,
        'groups_that_declined': int(len(declined)),
        'groups_compared': int(len(t)),
    }
    caveats = [REVENUE_CAVEAT,
               f'pct_change = (revenue {end} - revenue {start}) / revenue {start} x 100. '
               'Only the two end years are compared; years in between are ignored.']
    if not len(declined) and dimension != 'total':
        caveats.append(f'No {dimension} declined between {start} and {end}.')
    if len(t) - len(valid):
        caveats.append('Groups with zero revenue in the start year have no % change.')
    scope['years_compared'] = [start, end]
    return ToolResult(tool=name, value={'start_year': start, 'end_year': end, 'summary': summary,
                                        'groups': groups},
                      definition=f'Revenue {start} vs {end} by {dimension}', scope=scope,
                      rows_used=int(sub['Year'].isin([start, end]).sum()), caveats=caveats)


def get_monthly_revenue_share(model: Model, filters: dict | None = None,
                              base: Filters | None = None) -> ToolResult:
    """Each month's revenue as a % of that year's total; also the average share per month."""
    name = 'get_monthly_revenue_share'
    sub, scope, err = _prepare(model, name, filters, base)
    if err:
        return err
    m = monthly_share(sub)
    by_year = {}
    for y, grp in m.groupby('Year'):
        by_year[str(int(y))] = {r['Month']: _pct(r['Share %']) for _, r in grp.iterrows()}
    avg = m.groupby(['MonthNo', 'Month'])['Share %'].mean().reset_index().sort_values('MonthNo')
    avg_share = {r['Month']: _pct(r['Share %']) for _, r in avg.iterrows()}
    # Power BI's "%GT" view: calendar month across all years / grand total of all years.
    by_month = m.groupby(['MonthNo', 'Month'])['Revenue'].sum().reset_index().sort_values('MonthNo')
    grand = by_month['Revenue'].sum()
    gt_share = {r['Month']: _pct(r['Revenue'] / grand * 100) for _, r in by_month.iterrows()} if grand else {}
    peak = avg.loc[avg['Share %'].idxmax(), 'Month']
    low = avg.loc[avg['Share %'].idxmin(), 'Month']
    caveats = ['Denominator: total revenue of the same calendar year, within the rows in scope.',
               'Average share is the simple average of each year\'s share for that month.',
               'share_pct_of_grand_total_by_month instead divides each calendar month (all years '
               'added) by the total of all years in scope, like Power BI\'s "% of grand total".']
    incomplete = [str(int(y)) for y, grp in m.groupby('Year') if grp['MonthNo'].nunique() < 12]
    if incomplete:
        caveats.append(f'Year(s) {incomplete} have fewer than 12 months, so their shares are not '
                       'comparable with full years.')
    return ToolResult(tool=name, value={'peak_month': peak, 'lowest_month': low,
                                        'average_share_pct_by_month': avg_share,
                                        'share_pct_of_grand_total_by_month': gt_share,
                                        'share_pct_by_year': by_year},
                      definition='Monthly revenue / annual revenue x 100', scope=scope,
                      rows_used=int(len(sub)), caveats=caveats)


def _corr(x: pd.Series, y: pd.Series):
    ok = x.notna() & y.notna()
    if ok.sum() < 3 or x[ok].nunique() < 2 or y[ok].nunique() < 2:
        return None
    return round(float(np.corrcoef(x[ok].astype(float), y[ok].astype(float))[0, 1]), 3)


def get_satisfaction_vs_revenue(model: Model, filters: dict | None = None,
                                base: Filters | None = None) -> ToolResult:
    """Association between survey scores and revenue at the store-year level."""
    name = 'get_satisfaction_vs_revenue'
    if model.survey is None:
        return _error(name, 'No survey table is loaded, so satisfaction cannot be analysed.')
    sub, scope, err = _prepare(model, name, filters, base)
    if err:
        return err
    sy = store_year_survey(model, sub)
    scored = sy[sy['Satisfaction'].notna()]
    n_missing = int(sy['Satisfaction'].isna().sum())
    r_sat = _corr(scored['Satisfaction'], scored['Revenue'])
    r_int = _corr(scored['Interest'], scored['Revenue'])
    # Within-year correlation removes the effect of all stores growing over time.
    within = {}
    for y, grp in scored.groupby('Year'):
        within[str(int(y))] = _corr(grp['Satisfaction'], grp['Revenue'])
    strength = ('none/very weak' if r_sat is None or abs(r_sat) < 0.1 else 'weak' if abs(r_sat) < 0.3
                else 'moderate' if abs(r_sat) < 0.6 else 'strong')
    top = scored.sort_values('Satisfaction', ascending=False).head(3)
    bottom = scored.sort_values('Satisfaction').head(3)
    fmt = lambda d: [{'store': r['Store'], 'year': int(r['Year']), 'satisfaction': float(r['Satisfaction']),
                      'revenue': _money(r['Revenue'])} for _, r in d.iterrows()]
    caveats = [
        'Correlation measures association only. It does not show that satisfaction causes revenue '
        '(or the reverse); store size, market, and location can drive both.',
        'Each point is one store in one year. Survey scores are annual averages, not individual responses.',
        REVENUE_CAVEAT,
    ]
    if n_missing:
        caveats.append(f'{n_missing} store-year(s) have no satisfaction score (the website has no survey) '
                       'and are excluded.')
    if len(scored) < 30:
        caveats.append(f'Only {len(scored)} store-years have scores, a small sample.')
    return ToolResult(tool=name, value={
        'store_years_with_scores': int(len(scored)), 'store_years_without_scores': n_missing,
        'pearson_r_satisfaction_vs_revenue': r_sat, 'strength': strength,
        'pearson_r_interest_vs_revenue': r_int,
        'pearson_r_satisfaction_vs_revenue_within_each_year': within,
        'average_satisfaction': _pct(scored['Satisfaction'].mean()),
        'highest_satisfaction': fmt(top), 'lowest_satisfaction': fmt(bottom),
    }, definition='Pearson correlation of annual store revenue with the survey score for that store '
                  'and year (revenue summed to store-year before joining the survey)',
        scope=scope, rows_used=int(len(sub)), caveats=caveats)


def get_data_quality_report(model: Model) -> ToolResult:
    """Problems found while loading: missing values, duplicates, orphan keys, bad joins."""
    sev = {'error': 0, 'warning': 1, 'info': 2}
    issues = sorted(model.issues, key=lambda i: sev.get(i['severity'], 3))
    return ToolResult(tool='get_data_quality_report', value={
        'sales_rows_in_file': model.raw_sales_rows, 'sales_rows_used': int(len(model.sales)),
        'tables_missing': model.missing_tables,
        'issues': [{k: i[k] for k in ('severity', 'table', 'check', 'detail')} for i in issues],
        'joins': [{'join': m['join'], 'revenue_or_rows_changed': m['changed']} for m in model.merges],
    }, definition='Checks run on the loaded files', scope={'filters': 'none (full dataset)',
                                                         'dataset': model.dataset_info()},
        rows_used=model.raw_sales_rows,
        caveats=['A clean report means these specific checks passed, not that the data is correct.'])


def get_data_coverage(model: Model) -> ToolResult:
    """What the loaded data contains, and what it does not (e.g. no costs, so no profit)."""
    s = model.sales
    stores = []
    if model.stores is not None:
        for _, r in model.stores.iterrows():
            stores.append({'id': r['ID'], 'name': r['Store'], 'channel': r['Channel'],
                           'region': r.get('Location'), 'market': r.get('Market')})
    return ToolResult(tool='get_data_coverage', value={
        'years': model.years,
        'period': model.dataset_info()['period'],
        'channels': sorted(s['Channel'].unique().tolist()),
        'regions': sorted(s['Location'].unique().tolist()),
        'categories': sorted(s['Category'].unique().tolist()),
        'products': sorted(s['Product'].unique().tolist()),
        'stores': stores,
        'measures_available': ['monthly revenue by store, product and category',
                               'annual satisfaction (0-5) and interest (0-100) by physical store'],
        'not_in_the_data': NOT_IN_DATA,
    }, definition='Description of the loaded tables', scope={'filters': 'none (full dataset)',
                                                            'dataset': model.dataset_info()},
        rows_used=int(len(s)),
        caveats=['Questions about anything in not_in_the_data cannot be answered from this data.'])
