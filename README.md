# E-Presso BI Analyst

## What this application does
E-Presso BI Analyst is a revenue dashboard and analytical assistant for E-Presso, a company that sells espresso products through one website and 20 physical stores. A manager uploads the company's sales, store, and survey files. The app checks the data, joins the tables safely, and shows revenue by channel, year, month, store, category, and product. A built-in assistant answers business questions by running approved calculations on the uploaded data and shows the calculation behind every answer. Nothing is hard-coded: upload a changed file and every number updates.

## Live app
_URL here after deploying on Streamlit Community Cloud. The app may take up to a minute to wake up._

## How to use it
1. **Upload the data** in the sidebar. Select all the files at once:
   - `Support_EX_B_Sales.xlsx`, a workbook with sheets **Sales**, **Categories**, and **Products** (an export of the Access database)
   - `Support_EX_B_Stores.csv` (tab-delimited)
   - `Support_EX_B_Survey.xlsx`, which has a **Survey Results** sheet. The title rows above the header are skipped automatically.

   File names and sheet order do not matter. Each table is recognised by its column names. The tables can also be uploaded as separate CSV files.
2. **Filter** by year, sales channel, region, store, category, or product. All KPIs and charts recalculate.
3. **Dashboard tab:** revenue KPIs, annual revenue by channel, the monthly trend, monthly share of annual revenue, and revenue by category.
4. **Stores & products tab:** store growth between any two years, satisfaction vs revenue, top products, and a store table.
5. **Assistant tab:** ask a question, or click an example. Each answer shows its scope, findings, interpretation, limitations, a number check, and the tool-call log.
6. **Data & quality tab:** which files were read, the data model, join checks, data-quality issues, and table profiles.
7. **Validation tab:** key totals for comparison with Power BI. Click **Pin as baseline**, upload a modified file, and the Change column shows exactly what moved.

## Data model
The **Data & quality** tab rebuilds this grain table from whatever files are loaded, including the source file and sheet, the row count, and duplicate keys.

| Table | Grain (one row is...) | Key |
|---|---|---|
| Sales | one store, one product, one month. `Date` is the last day of the month and `Revenue` is that month's revenue. 25,200 rows = 21 stores x 20 products x 60 months. | Sales ID |
| Stores | one store. SH00 is the website and SH01 to SH20 are physical stores. | ID |
| Categories | one product category | Category ID |
| Products | one product | Product ID |
| Survey Results | one store per year: average satisfaction (0 to 5) and interest (0 to 100) | Survey ID (= Store ID + two-digit year) |

**Relationships used**
- `Sales[Store ID]` -> `Stores[ID]` (many-to-one)
- `Sales[Category ID]` -> `Categories[Category ID]` (many-to-one)
- `Sales[Product ID]` -> `Products[Product ID]` (many-to-one)
- **Survey is joined at its own grain.** Revenue is first summed to store + year, then matched to the survey on `Store ID` + `Year`. The survey is never joined to individual sales rows, so it cannot duplicate revenue.

**Merge checks.** Every join is run through `check_merge()`, which compares the row count and the revenue total before and after the join, and the Data tab shows the result with an explanation. All three lookup joins keep 25,200 rows and $42,174,440. The survey join keeps 105 store-year rows and the same total. If a lookup table ever contains a repeated key, the duplicate is dropped and reported so it cannot multiply revenue (this is covered by a test).

**A relationship we deliberately did not use.** The original Power BI model joins `Sales[Store ID]` (e.g. `SH11`) to `Survey Results[Survey ID]` (e.g. `SH11_27`). Those values never match, so that relationship links nothing. See [docs/VALIDATION.md](docs/VALIDATION.md).

## Metric definitions
- **Revenue** = sum of `Sales[Revenue]` for the rows in scope. It is sales income only. **It is not profit**: the data has no costs, prices, or quantities.
- **Website revenue** = revenue of stores whose StoreType is "Website" (SH00). **Brick & mortar** = every other store.
- **Website share** = website revenue / total revenue of the rows in scope.
- **Growth** = (revenue in end year - revenue in start year) / revenue in start year. Only the two end years are compared.
- **Monthly share of annual revenue** = a month's revenue / the total revenue of the same calendar year, within the current filters. The assistant also reports Power BI's "% of grand total" version: each calendar month (all years added) / the total of all years.
- **Satisfaction vs revenue** = Pearson correlation between a store's annual revenue and its survey score for that year. This is an association, not a cause.

## Updated data
The app expects the same file names, sheet names, column names, and data types, but any values can change. Rows may be added or removed, values may be blank, and stores, products, categories, or years may appear or disappear. Every filter list is built from the loaded data. Blank values are reported, not fatal. A missing required column produces a plain message naming it. The upload cache is keyed on the file contents, so a modified file is never confused with the original one. `tests/test_updated_data.py` covers each of these cases.

## Data quality checks
Run on every upload: missing or non-numeric revenue, invalid dates, exact duplicate rows, repeated Sales IDs, keys missing from lookup tables, duplicate lookup keys, products in more than one category, stores missing months, StoreYear not matching Store ID + date, survey scores outside their scales, survey stores missing from the stores table, and the same state assigned to several regions.

Found in the supplied data:
- Three different products share the name **"Base Model"** (P11 Espresso Machines, P15 Accessories, P19 Frothers). Grouping by name would combine them, so they are shown as "Base Model (P11)" and so on.
- The website (SH00) has no survey scores for any year, so it is excluded from satisfaction analysis.
- Region labels look inconsistent: the two Orlando, FL stores are in different regions (South and East), and Flushing, NY is labelled "Southwest". Region results depend on these labels as given.

## Assistant
**What it is.** A live language-model agent using **Google Gemini function calling** (`gemini-3.5-flash` by default; if a model has been retired or is overloaded, the app tries `gemini-3.8-flash`, `gemini-flash-latest`, then `gemini-flash-lite-latest`). When no API key is configured, or the model fails or runs out of quota, a **rules-based assistant** answers instead and the app says so on screen. Both use the same approved functions.

**How an answer is produced**
1. The user asks a question in the chat.
2. The model (or the keyword rules) chooses one or more approved functions and their arguments.
3. `agent.run_tool()` (the dispatcher) checks that the function exists and that every argument is allowed, then runs it on the currently loaded data.
4. The result goes back to the model, which writes the answer starting with its **Scope**, then **Findings** (tool results only), **Interpretation**, **Limitations**, and **Next steps**. Next steps says "No recommendation is supported by this data." when the data cannot support one.
5. The app checks every number in the answer against the tool results (`verify_numbers`) and shows the full tool-call log: function name, arguments, and raw result.

**Approved functions** (in `analytics.py`)
| Function | What it calculates |
|---|---|
| `get_revenue_total` | Total revenue, period, and average per month for any subset |
| `get_revenue_breakdown` | Revenue and % share by channel, year, month, year-month, store, category, product, or region |
| `get_revenue_growth` | Change between two years for the total or each channel, store, category, product, or region, ranked by % change |
| `get_monthly_revenue_share` | Each month as % of its year's total, the average share per month, and the % of grand total |
| `get_satisfaction_vs_revenue` | Correlation of survey scores with store-year revenue, with caveats |
| `get_data_quality_report` | Every data-quality issue and join check |
| `get_data_coverage` | What the data contains, and what it does not (no costs, so no profit) |

**What the assistant is not allowed to do**
- Run code. The model can only choose among the seven named functions. There is no `eval`, `exec`, SQL, pandas agent, or code interpreter.
- Change the data. No function writes anything.
- State numbers that are not in a tool result. The system prompt forbids it, and the app flags any number it cannot trace.
- Treat revenue as profit, or correlation as causation.
- Recommend closing or investing in a store based on revenue alone.

**Scope.** By default, answers cover the full dataset unless the question names a subset. A toggle applies the sidebar filters to every tool call instead; the scope then says "(from the sidebar filter)" and the tool-call log lists the sidebar filters that were applied, so no filter state is hidden.

**Usage limits.** 15 questions per browser session, and 200 model calls per day across all visitors. When a limit is reached, or the API fails, the rules-based assistant answers.

## Validation
See [docs/VALIDATION.md](docs/VALIDATION.md) for the Power BI comparison, the independent check (`scripts/independent_check.py`), and the $1,000 modified-data test (`scripts/make_modified_data.py`). See [docs/AGENT_TESTS.md](docs/AGENT_TESTS.md) for the five required assistant tests.

## Limitations
- Revenue is not profit. There are no costs, prices, quantities, customers, staffing, or lease data, so the app cannot say which store is most profitable or whether one should close.
- Survey scores are annual store averages, available for physical stores only. The sample is 100 store-years. Correlation does not show causation.
- Growth compares two end years and ignores the path in between.
- Region labels come from the stores file as given, and some look wrong (see Data quality).
- Rows with missing revenue or dates are excluded from every calculation and reported. Exact duplicate rows are kept, with a warning.
- The language model can still misread a question. The number check catches invented numbers, but not a wrong choice of function. Always read the tool-call log.

## Files
| File | Purpose |
|---|---|
| `streamlit_app.py` | Screen layout and interaction |
| `analytics.py` | Loading, data model, quality checks, metrics, and the approved tools (no Streamlit code) |
| `agent.py` | Tool registry, dispatcher, rules router, Gemini router, and number check |
| `tests/` | 45 tests on a tiny hand-checkable dataset: calculations, the assistant (with a fake model for the function-calling loop), and updated-data cases (blank values, new or removed stores, years and categories, missing columns) |
| `scripts/independent_check.py` | Recalculates totals from the raw files without pandas or app code |
| `scripts/make_modified_data.py` | Creates the +$1,000 test file |
| `docs/` | Validation and assistant test evidence |
| `requirements.txt` | Packages the hosted app needs |

## Run it
Hosted: deploy from this repository on share.streamlit.io with main file `streamlit_app.py`.

Locally: `pip install -r requirements-dev.txt`, then `streamlit run streamlit_app.py`. Run the tests with `pytest`.

## Secrets
Never commit API keys. On Streamlit Community Cloud, open **App settings > Secrets** and add:
```toml
GEMINI_API_KEY = "your-key"
# optional: GEMINI_MODEL = "gemini-3.5-flash"
```
For local work, put the same lines in `.streamlit/secrets.toml`, which is git-ignored.
