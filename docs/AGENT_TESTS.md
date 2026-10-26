# Assistant tests

The results below were produced by the **rules-based** mode, the fallback used when no API key is configured, and run against the original files (fingerprint `670c0b95`). Both modes use the same functions, so the numbers do not depend on the mode.

> **To do once the Gemini key is added:** run the same five questions in live-model mode and fill in the "Live model" rows. Check each answer's tool-call log, and confirm the green "All N numbers match a tool result" message appears.

| # | Test question | Function used | Expected result / behavior | Actual result (rules mode) | Pass/Fail | Revision made |
|---|---|---|---|---|---|---|
| 1 Numerical | How much revenue did the website generate in 2028? | `get_revenue_total(filters={years:[2028], channels:[Website]})` | $6,339,640 (independent check: Website 2028 row of the year x channel table) | $6,339,640, 240 sales rows, Jan to Dec 2028, scope "years: 2028; channels: Website" | Pass | None |
| 2 Comparison | Which store experienced the greatest percentage revenue decline? | `get_revenue_growth(dimension=store)` | SH11 Chicago, $539,740 (2025) to $179,130 (2029) = -66.81%. If no store had declined, the tool should say "No store declined" (covered by a test). | "Largest % decline: **SH11 Chicago, IL**", then the full ranked list. 20 of 21 stores declined; only the website grew (+121.61%). | Pass after revision | The first version printed the whole 21-store list before the answer. The direct answer now comes first. |
| 3 Missing information | Which E-Presso store is the most profitable? | `get_data_coverage`, then `get_revenue_breakdown(dimension=store)` | Must say profit cannot be calculated (no costs). May give revenue as context only. | Opens with "**This cannot be answered from the data.** The question needs profit information...", lists what the data does not contain (costs, prices, customers...), then gives the revenue ranking as context only, adding that the highest-revenue store is not necessarily the most profitable. | Pass after revision | The first version put the "cannot answer" statement at the bottom under Limitations, after a long revenue list, which a manager could easily misread as an answer. It now leads the Findings. |
| 4 Judgment | Does higher customer satisfaction cause higher revenue? | `get_satisfaction_vs_revenue` | Report the correlation, call it association not causation, and note that the website has no survey scores. | r = 0.235 (weak) across 100 store-years, interest r = 0.125. Says "association, not proof that satisfaction causes revenue". Notes that store size, market, and location can drive both, and that 5 website store-years have no score. | Pass | None |
| 5 Updated data | How much revenue did the Chicago store generate in 2028? (asked before and after the $1,000 change) | `get_revenue_total(filters={years:[2028], stores:[Chicago]})` | $269,100 before, $270,100 after, with no code change | $269,100 (data `670c0b95`), then $270,100 (data `5edd2917`). Each answer shows which data fingerprint it used. | Pass | None |
| Edge: judgment | Should E-Presso close its Chicago store? | `get_revenue_growth(dimension=store)` + `get_data_coverage` | Compare Chicago with the other stores and refuse to decide on revenue alone. | Chicago ranks 1 of 21 (largest decline, -66.81%). Says revenue cannot justify closing and names what is missing: operating costs, profitability, lease terms, customer behaviour. | Pass after revision | The first version filtered to Chicago only, so it could not compare with other stores. It now runs growth for all stores and highlights Chicago's rank. |
| Edge: ambiguous | How did Orlando do in 2027? | `get_revenue_total(filters={years:[2027], stores:[Orlando]})` | There are two Orlando stores (SH01, SH08), so the assistant must not guess. | First version: "unsupported question" (no keyword matched). After revision: the tool returns "Orlando matches 2 stores: SH01 Orlando, FL, SH08 Orlando, FL. Use a store ID." | Pass after revision | The rules router now falls back to `get_revenue_total` whenever a question names a store, year, channel, category, or product. |
| Edge: display | (any answer with two dollar amounts) | n/a | Money should display as plain text | First version: Streamlit treated "$269,100 ... $22,425" as a LaTeX math formula and garbled it. | Pass after revision | Answers are escaped (`md_safe`) before display. Retested in the browser. |

## Live model (Gemini) results
| # | Function(s) the model chose | Answer summary | Numbers verified? | Pass/Fail | Notes |
|---|---|---|---|---|---|
| 1 | _fill in_ | | | | |
| 2 | _fill in_ | | | | |
| 3 | _fill in_ | | | | |
| 4 | _fill in_ | | | | |
| 5 | _fill in_ | | | | |

## Safeguards that are tested automatically (`pytest`)
- The dispatcher rejects unknown functions (for example `run_python`) and unexpected arguments, and logs the attempt.
- Unknown filter values (a year that is not in the data, an unknown store) return an error instead of $0.
- An ambiguous city name returns an error instead of a guess.
- `verify_numbers` accepts rounding ($1,025 written as "$1.0 thousand") but flags an invented "$412 profit".
- A fake Gemini client checks the function-calling loop: the model's requested tool is run, its result is returned, and an invented number in the final answer is flagged.
- If the model is unavailable (for example, quota reached), the rules assistant answers and the notice says why.
