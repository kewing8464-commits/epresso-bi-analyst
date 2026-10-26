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
```
$ python scripts/make_modified_data.py Support_EX_B_Sales.xlsx S1120280601
Row 703: Sales ID S1120280601, 2028-06-30, SH11 (Chicago), C1 Beans, P01 French Roast, Revenue 590
Original revenue: 590.00
Modified revenue: 1,590.00
```
Only that one cell changed. The app's Python code was not touched. Steps: load the original files, click **Pin as baseline**, remove the Sales file, and upload `Support_EX_B_Sales_plus1000.xlsx`.

| Check | Original | Modified | Expected | Actual | Pass |
|---|---|---|---|---|---|
| Total revenue | $42,174,440 | $42,175,440 | +$1,000 | +$1,000.00 | Pass |
| Brick & mortar revenue | $14,955,560 | $14,956,560 | +$1,000 | +$1,000.00 | Pass |
| Website revenue | $27,218,880 | $27,218,880 | no change | 0.00 | Pass |
| Revenue 2028 | $8,969,470 | $8,970,470 | +$1,000 | +$1,000.00 | Pass |
| Revenue in June (all years) | $2,743,370 | $2,744,370 | +$1,000 | +$1,000.00 | Pass |
| Beans revenue | $5,672,720 | $5,673,720 | +$1,000 | +$1,000.00 | Pass |
| Every other year, month, and category | | | no change | 0.00 | Pass |
| Filtered: Chicago, 2028 (assistant) | $269,100 | $270,100 | +$1,000 | $270,100 | Pass |
| Data fingerprint | `670c0b95` | `5edd2917` | changes | changed | Pass |
| Chart data: Annual revenue by channel, 2028 Brick & Mortar bar | $2,629,830 | $2,630,830 | +$1,000 | $2,630,830 (the same `revenue_by` result the chart plots) | Pass |

The Validation tab's "Changed metrics" banner listed exactly these six metrics: total, brick & mortar, 2028, 2028 brick & mortar, June, and Beans. The original dataset was then restored by uploading the original Sales file again. An automated version of this test is `test_adding_1000_changes_total_by_exactly_1000` in `tests/test_analytics.py`.
