# Validation

All Streamlit results come from the app's **Validation** tab with the original files loaded (data fingerprint `670c0b95`). The **Independent check** column comes from `scripts/independent_check.py`, which reads the raw files with openpyxl and the csv module and adds revenue with plain Python loops. It uses no pandas and none of the app's code.

> **To do before submitting:** fill in the Power BI column from your own Power BI report (Power BI Desktop runs on Windows only). The notes below say what Power BI should show, based on the data model inside `NP_EX_B_EPresso.pbix`.

## 1. Power BI vs Streamlit

| Metric | Power BI result | Streamlit result | Independent check | Match? / Explanation |
|---|---|---|---|---|
| Total revenue (2025 to 2029) | _fill in_ | $42,174,440 | $42,174,440 | Power BI's line chart sums `Sales[Revenue]` with no filters, so it should match exactly. |
| Website revenue | _fill in_ | $27,218,880 | $27,218,880 | See note A. |
| Brick-and-mortar revenue | _fill in_ | $14,955,560 | $14,955,560 | See note A. |
| Revenue 2025 | _fill in_ | $7,181,750 | $7,181,750 | |
| Revenue 2026 | _fill in_ | $8,016,950 | $8,016,950 | |
| Revenue 2027 | _fill in_ | $8,824,720 | $8,824,720 | |
| Revenue 2028 | _fill in_ | $8,969,470 | $8,969,470 | |
| Revenue 2029 | _fill in_ | $9,181,550 | $9,181,550 | |
| Monthly pattern: December % of total | _fill in_ | 25.61% | $10,800,990 / $42,174,440 = 25.61% | See note B. December is the peak month and February the lowest (3.83%). |
| Additional: Espresso Machines revenue (and share) | _fill in_ | $27,093,220 (64.24%) | | Category is the slicer on the Power BI Revenue page. |

### Note A: the Store Type chart in the Power BI file
The Power BI model connects **Stores -> Survey Results -> Sales**. The last link is `Survey Results[Survey ID]` -> `Sales[Store ID]`, but a Survey ID looks like `SH11_27` and a Store ID looks like `SH11`, so **no row ever matches**. Power BI therefore cannot pass the Store Type filter on to Sales. Expect the "Sum of Revenue by StoreType" bar chart to show all $42,174,440 under **(Blank)**, or the same total repeated for every store type, instead of a website / brick & mortar split. The "Satisfaction vs Revenue" scatter has the same problem.

**Correct model (used by this app):** `Sales[Store ID]` -> `Stores[ID]` directly. For the survey, add revenue up to store + year first, then join on `Store ID` + `Year`. In Power BI the fix is to delete the `Sales[Store ID]` -> `Survey[Survey ID]` relationship and add `Sales[Store ID]` -> `E-Presso Stores[ID]`, plus `Sales[StoreYear]` -> `Survey Results[Survey ID]` if you need survey fields.

### Note B: two definitions of "monthly share"
Power BI's column chart uses **% of grand total**: each calendar month (all five years added) divided by the five-year total. The app's heatmap shows each month **as % of its own year's total**, which answers a different question (seasonality within a year). The Validation tab and the `get_monthly_revenue_share` tool report both. They differ by at most 0.02 percentage points here (for example, November is 12.71% of the grand total and 12.73% as the average of the five yearly shares), because each year's seasonal pattern is very similar.

| Month | % of 5-year total (Power BI definition) | Average % of own year (app heatmap) |
|---|---|---|
| Jan | 7.74 | 7.74 |
| Feb | 3.83 | 3.83 |
| Mar | 4.44 | 4.44 |
| Apr | 5.15 | 5.15 |
| May | 5.72 | 5.72 |
| Jun | 6.50 | 6.51 |
| Jul | 6.42 | 6.41 |
| Aug | 6.45 | 6.44 |
| Sep | 7.11 | 7.10 |
| Oct | 8.31 | 8.31 |
| Nov | 12.71 | 12.73 |
| Dec | 25.61 | 25.61 |

### Revenue by year and channel (Streamlit)
| Year | Website | Brick & Mortar | Total |
|---|---|---|---|
| 2025 | $3,160,230 | $4,021,520 | $7,181,750 |
| 2026 | $4,784,750 | $3,232,200 | $8,016,950 |
| 2027 | $5,931,010 | $2,893,710 | $8,824,720 |
| 2028 | $6,339,640 | $2,629,830 | $8,969,470 |
| 2029 | $7,003,250 | $2,178,300 | $9,181,550 |

Website revenue grew 121.6% from 2025 to 2029, while brick & mortar fell 45.8%. All 20 physical stores declined over that period. The steepest decline is SH11 Chicago (-66.8%) and the mildest is SH09 Phoenix (-11.8%).

## 2. Independent check
```
$ python scripts/independent_check.py Support_EX_B_Sales.xlsx Support_EX_B_Stores.csv
Rows read: 25,200
Total revenue: 42,174,440.00
  Brick & Mortar: 14,955,560.00
  Website: 27,218,880.00
  2025: 7,181,750.00   2026: 8,016,950.00   2027: 8,824,720.00
  2028: 8,969,470.00   2029: 9,181,550.00
  month 12: 10,800,990.00   (and the other 11 months)
```
Every figure matches the app to the cent. The check also proves that the app's joins did not add or drop revenue.

## 3. $1,000 modified-data test

### Predictions
> **Team to do:** write your own predictions before you run this test on the hosted app, then compare. The rubric asks for predictions made before the upload. The list below is the expected outcome worked out from the data, for you to check against.
- The change is +$1,000 on Sales ID `S1120280601`, a brick & mortar row for Chicago, June 2028, Beans.
- **Should change by exactly +$1,000:** total revenue ($42,174,440 to $42,175,440), brick & mortar revenue, revenue 2028, 2028 brick & mortar, June (all years), Beans, and every filter that includes Chicago + 2028.
- **Should NOT change:** website revenue, every other year, every other month, every other category, Chicago 2027, and Chicago's 2025 to 2029 growth (2028 is not one of the two end years).
- **Charts that should change:** the 2028 Brick & Mortar bar in "Annual revenue by channel"; the June 2028 point on the Brick & Mortar line in "Monthly revenue trend"; the Jun 2028 heatmap cell; the Beans bar.

### Modified-data test record
| Item | Entry |
|---|---|
| Table and row identifier | Sales table (`Support_EX_B_Sales.xlsx`, sheet Sales), Sales ID `S1120280601`, spreadsheet row 703 |
| Date / store / channel of the row | 2028-06-30 / SH11 Chicago, IL / Brick & Mortar (product P01 French Roast, category Beans) |
| Original value | $590 |
| Modified value | $1,590 (revenue is stored, so exactly $1,000 was added; no other cell changed) |
| Expected total revenue after change | $42,174,440 + $1,000 = $42,175,440 |
| Actual total revenue after change | $42,175,440 (Validation tab, Change column +1,000.00) |
| Chart that should change, and what you saw | "Annual revenue by channel": the data behind the 2028 Brick & Mortar bar went from $2,629,830 to $2,630,830. With the Store filter set to Chicago, the data behind the June 2028 point of the monthly trend went from $17,350 to $18,350. _(Confirm by hovering over the chart on your own run.)_ |
| Filter combinations checked (changed / unchanged) | See the table below |
| Code changes required? | None. The new file was uploaded through the sidebar, without a code edit or a restart. The upload cache is keyed on the file contents, and the data fingerprint changed from `670c0b95` to `5edd2917`. |
| Original data restored? | Yes. The original Sales file was loaded again (fingerprint back to `670c0b95`). The original file on disk was never edited; the script writes a separate `_plus1000` copy. |

| Filter combination | Original | Modified | Change | Predicted |
|---|---|---|---|---|
| Chicago (SH11), 2028 | $269,100 | $270,100 | +1,000 | changes |
| Chicago (SH11), Beans, 2028 | $36,800 | $37,800 | +1,000 | changes |
| Brick & Mortar, 2028 | $2,629,830 | $2,630,830 | +1,000 | changes |
| Midwest region (includes Chicago), all years | $3,084,350 | $3,085,350 | +1,000 | changes |
| Chicago (SH11), 2027 | $355,730 | $355,730 | 0 | unchanged |
| Website, 2028 | $6,339,640 | $6,339,640 | 0 | unchanged |
| Syrups, 2028 | $564,410 | $564,410 | 0 | unchanged |
| Denver (SH13), all years | $732,380 | $732,380 | 0 | unchanged |
| Chicago growth 2025 to 2029 | -66.81% | -66.81% | 0 | unchanged |

Every result matched the expected outcome. On the Validation tab, the "Changed metrics" banner listed exactly six metrics, each +1,000.00: total, brick & mortar, 2028, 2028 brick & mortar, June, and Beans.

**Assistant before and after:** "How much revenue did the Chicago store generate in 2028?" returned $269,100 (data `670c0b95`), then $270,100 (data `5edd2917`). Both answers show their tool-call log with `get_revenue_total(filters={years:[2028], stores:[Chicago]})`.

An automated version of this test is `test_adding_1000_changes_total_by_exactly_1000` in `tests/test_analytics.py`.

> Attach screenshots: the Validation tab with the baseline pinned, and both assistant answers with their tool-call logs open.
