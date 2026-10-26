# Team contribution statement

> **Team to do:** fill this in yourselves. Every member needs commits in the repository history (the guide checks this), and each person should be able to explain the calculation or decision listed in their row during the individual defense.

| Team member | Main responsibilities | Key calculation or design decision I can explain | Approximate commits |
|---|---|---|---|
| | | | |
| | | | |

## Suggested defense topics (each member should be able to explain all of these)
- **Grain and joins:** why Sales joins to Stores on `Store ID` = `ID`, why the survey is joined at store-year grain, and why `Sales[Store ID]` -> `Survey[Survey ID]` (the Power BI relationship) matches nothing.
- **Merge checks:** what `check_merge()` compares, and what it would show if a lookup table had a duplicate key.
- **Revenue definitions:** website vs brick & mortar, the growth formula, and the two monthly-share denominators (own year vs five-year total).
- **The $1,000 test:** which row changed, what changed, what did not, and why the cache does not show old numbers.
- **The assistant:** the flow from question to model to tool to result to answer; why the model cannot run code; what `verify_numbers` checks and what it cannot catch; and what happens when the API fails.
- **Limitations:** why the app cannot answer profitability, and why correlation is not causation.
