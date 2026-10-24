# Tableau Public companion

A public, interactive Tableau workbook beside the Streamlit dashboard (ADR 0002, ticket 17). It is a dated snapshot: Tableau Public cannot refresh from Snowflake, so you re-export and republish.

## 1. Export the data

```
uv run --env-file .env python -m tableau.export
```

Writes two files to `tableau/out/` (git-ignored):

| File | Grain | Columns |
|---|---|---|
| `nyc311_daily.csv` | creation day × borough × agency × Problem × channel | `created_date`, `borough`, `agency`, `problem`, `channel`, `requests`, `closed_requests`, `open_requests` |
| `nyc311_coverage.csv` | one row | `publication_id`, `first_created_date`, `last_created_date`, `current_requests`, `latest_extracted_at_new_york` |

Both files come from the current Publication (ticket 07), so they always agree. Every measure is a count, so `SUM` is always correct. Open means not yet Closed as of the latest Observation. It is not Overdue.

## 2. Set up Tableau Public

1. Create a free account at public.tableau.com and install **Tableau Public** (the desktop app).
2. **Connect → To a File → Text file** → `nyc311_daily.csv`.
3. On the Data Source tab, check the types: `created_date` is a Date and the three counts are Numbers (whole). Rename the fields to title case (Requests, Closed Requests, Open Requests, Problem…).
4. **Data → New Data Source → Text file** → `nyc311_coverage.csv`. You'll use it only for the "as of" caption.

## 3. Calculated fields (on `nyc311_daily`)

| Name | Formula |
|---|---|
| Closure Rate | `SUM([Closed Requests]) / SUM([Requests])`, formatted as a percentage with 1 decimal |
| Open Share | `SUM([Open Requests]) / SUM([Requests])` |
| Enough Requests | `SUM([Requests]) >= 20`, which hides small cells in rate views (same rule as ticket 09) |

## 4. Worksheets

1. **KPIs**: Requests, Closed Requests, Open Requests and Closure Rate. Use Measure Names on Columns, Measure Values on Text, and make the numbers large.
2. **Requests per day**: `DAY(created_date)` on Columns and Measure Values (Closed and Open Requests) on Rows, stacked bars, Measure Names on Color. Blue for Closed and orange for Open, matching Streamlit (`#2a78d6` and `#eb6834`).
3. **By borough**: Borough on Rows, SUM(Requests) as a horizontal bar sorted descending, Closure Rate on the label.
4. **Top Problems**: Problem on Rows, SUM(Requests) as bars. Filter Problem to Top 15 by SUM(Requests) and color by Closure Rate.
5. **Agency × borough heatmap**: Agency on Rows, Borough on Columns, square marks colored by Closure Rate. Put Enough Requests on Filters and keep True.
6. **Channel mix**: Channel on Color, SUM(Requests) as a 100% stacked bar per Borough (Quick Table Calculation → Percent of Total, computed along Channel).
7. **As of** (coverage source): a text sheet reading `Created <first_created_date> to <last_created_date> · data as of <latest_extracted_at_new_york> New York time`.

## 5. Dashboard and interactivity

1. **New Dashboard**, Size → Automatic (or 1200 × 900). Title it *NYC 311 Signal: Requests and Closure*.
2. Layout: KPIs and the As of sheet across the top, Requests per day full width, then By borough and Top Problems side by side, then the heatmap and Channel mix.
3. **Filters**: show `created_date` (range), Borough, Agency and Problem (multi-select dropdown). On each filter, choose **Apply to Worksheets → All Using This Data Source**.
4. **Actions** (Dashboard → Actions):
   - *Filter*: source By borough, targets all sheets, run on Select. Clicking a borough filters the whole dashboard.
   - *Filter*: source Top Problems → targets Requests per day and the heatmap.
   - *Highlight*: source Channel mix, run on Hover.
5. Tooltips: edit each one in plain words, e.g. `<Requests> Requests created in <Borough>, <Closure Rate> Closed so far`.
6. Add a caption text box: *Counts of NYC 311 Service Requests by creation date. Closed is the administrative Closure recorded by the source, not proof the problem was fixed. No addresses or locations below borough.*

## 6. Publish

**File → Save to Tableau Public**. Everything you save there is public. Then copy the link into the project docs (ticket 17).

## Refresh

Run the export again, open the workbook, choose **Data → Refresh** for both sources, and save to Tableau Public again. Do this after the full backfill (ticket 08) and then whenever you want a newer snapshot.
