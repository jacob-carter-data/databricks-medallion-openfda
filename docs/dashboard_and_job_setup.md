# AI/BI dashboard and scheduled job — configuration

The last two Phase B artifacts. Like `genie_space_setup.md`, this is configuration
kept in version control: the layout decisions below are governance, not taste.

---

# Part 1 — the AI/BI dashboard

## The design constraint that drives the whole layout

The Gold layer already made one hard call: **severity was deliberately removed
from `activity_score`**, because a weighted sum cannot rank on two dimensions that
trade off, and volume always wins at scale. That fix lives in the data.

A dashboard can undo it. If the biggest, highest tile is "Top 20 by activity
score," a reader takes the top row as *the riskiest firm* no matter what the
column is called — and the discount retailer with 117 reversible-harm recalls
once again outranks the manufacturer with 22 potential-death ones. The layout is
load-bearing: **severity leads, volume is subordinate and labeled.**

This is the same severity-versus-volume defect as Genie test B1, appearing at a third
layer. Worth saying out loud in the field notes: the error is not in any one
component, it is in the *shape of the question*, so every layer that answers it has
to be fixed separately.

## Datasets

Create these as named datasets in the dashboard's Data tab.

```sql
-- ds_headline
SELECT
  (SELECT value FROM workspace.gold.pipeline_quality WHERE metric='firms_total')        AS firms_total,
  (SELECT COUNT(*) FROM workspace.gold.supplier_risk WHERE has_active_class_i)          AS firms_active_class_i,
  (SELECT value FROM workspace.gold.pipeline_quality WHERE metric='join_rate_pct')      AS join_rate_pct,
  (SELECT value_text FROM workspace.gold.pipeline_quality
     WHERE metric='source_last_updated__fda_shortages')                                 AS shortages_updated,
  (SELECT value_text FROM workspace.gold.pipeline_quality
     WHERE metric='source_last_updated__fda_enforcement')                               AS enforcement_updated
```

```sql
-- ds_severity_leaders  (the lead chart)
SELECT display_name, recalls_class_i_active
FROM workspace.gold.supplier_risk
WHERE has_active_class_i
ORDER BY recalls_class_i_active DESC, activity_score DESC
LIMIT 15
```

```sql
-- ds_severity_distribution
SELECT max_active_classification AS classification,
       COUNT(*)                  AS firms,
       SUM(recalls_active_strict) AS active_recalls
FROM workspace.gold.supplier_risk
WHERE recalls_active_strict > 0
GROUP BY max_active_classification
ORDER BY CASE max_active_classification
           WHEN 'Class I' THEN 1 WHEN 'Class II' THEN 2 WHEN 'Class III' THEN 3 ELSE 4 END
```

```sql
-- ds_shortage_leaders
SELECT display_name, shortages_active_strict
FROM workspace.gold.supplier_risk
WHERE shortages_active_strict > 0
ORDER BY shortages_active_strict DESC
LIMIT 15
```

```sql
-- ds_activity_volume  (subordinate, and labeled as volume)
SELECT display_name, activity_score, max_active_classification
FROM workspace.gold.supplier_risk
ORDER BY activity_score DESC
LIMIT 15
```

```sql
-- ds_quality
SELECT metric, COALESCE(CAST(value AS STRING), value_text) AS value, description
FROM workspace.gold.pipeline_quality
ORDER BY metric
```

## Widgets, in page order

| # | Widget | Type | Dataset | Notes |
|---|---|---|---|---|
| 1 | Firms tracked | Counter | `ds_headline.firms_total` | |
| 2 | **Firms with an active Class I recall** | Counter | `ds_headline.firms_active_class_i` | The headline number. Class I = reasonable probability of serious harm or death |
| 3 | Cross-feed join rate | Counter, `%` | `ds_headline.join_rate_pct` | Subtitle: "Unmatched is not the same as none" |
| 4 | Source freshness | Counter ×2 | `ds_headline.*_updated` | **Two tiles, not one.** The feeds update independently; one "last updated" would be a lie about one of them |
| 5 | **Firms by active Class I recalls** | Horizontal bar | `ds_severity_leaders` | **The largest tile on the page.** Horizontal because firm names are long |
| 6 | Active recalls by severity | Horizontal bar | `ds_severity_distribution` | Sorted Class I → III, never alphabetical |
| 7 | Firms by active shortages | Horizontal bar | `ds_shortage_leaders` | Strict definition, stated in the title |
| 8 | Activity volume — **not a severity ranking** | Horizontal bar | `ds_activity_volume` | Below the fold, smaller than #5. Title carries the disclaimer; a description alone will not be read |
| 9 | Pipeline quality | Table | `ds_quality` | All 11 metrics with their descriptions — a table, because every row carries meaning and no chart beats it |

## Form and color decisions, and why

- **Counters, not one-bar charts, for #1–4.** A single current value is a stat
  tile; a bar chart of one number is noise.
- **Horizontal bars everywhere.** Firm names are long (`KILITCH HEALTHCARE INDIA
  LIMITED`); vertical columns would truncate or rotate them.
- **Sequential, one hue, more-is-darker** on every bar chart. The job is comparing
  magnitude, not telling series apart, so a categorical palette would be wrong —
  it would imply the firms are *kinds* of thing rather than points on a scale.
- **#6 is an ordered scale, so it gets the sequential ramp too**, darkest at Class
  I. Never a categorical or rainbow palette on severity: a rainbow says these are
  unrelated categories, and the entire point is that they are ranked.
- **No pie chart for #6.** Three-to-four slices where the reader needs to compare
  magnitude is a bar chart.
- **No dual-axis anywhere.** `firms` and `active_recalls` in #6 are different
  scales — if both are needed visually, that is two charts, not two y-axes.
- **#9 is a table on purpose.** Eleven metrics that all carry meaning is past the
  point where more colors help.

**Honest limitation:** AI/BI does not expose mark-level geometry (data-end radius,
inter-bar spacers, ring widths), so those specs are not applied here — this is a
BI tool with its own house style, not a hand-built chart. What *is* controllable
and does matter — form, ordering, one-hue sequential ramps, no dual axis, and the
titles — is specified above. Do not fight the tool for the rest.

## Checks before calling the dashboard done

- [ ] Widget #5 is visually dominant and above #8.
- [ ] #8's **title** (not just its description) says it is volume, not severity.
- [ ] #6 sorts Class I → II → III, not alphabetically and not by count.
- [ ] Two freshness tiles, never merged into one.
- [ ] Every bar chart is one hue, light→dark. No rainbow, no per-firm colors.
- [ ] Page footer states: public FDA data, not for clinical use; not an FDA
      judgment about any company; Free Edition sidecar, not production scale.
- [ ] Screenshot the finished page and actually look at it — check for truncated
      firm names and label collisions. The specs above govern color and form; only
      your eyes catch layout.

---

# Part 2 — the scheduled job

## Cadence: weekly, not nightly

The roadmap says nightly. **Weekly is the better call, and here is the reasoning
to keep on the record**, because changing a plan without saying why is how a
roadmap rots:

- Both openFDA feeds publish on a roughly weekly cadence. A nightly run would
  mostly re-ingest unchanged data and add Bronze rows that record nothing.
- Free Edition compute quota is a hard daily ceiling — overrun kills compute for
  the rest of the day. A weekly job spends that budget where it buys something.
- Bronze is append-only, so a redundant run is not harmless: it inflates the raw
  tables and makes `_ingested_at` history harder to read.

If freshness ever needs to be daily, that is a deliberate change with a quota
consequence, not a default.

## Job definition

**Name:** `meridian_fda_sidecar_weekly`
**Schedule:** weekly, Sunday 06:00 America/New_York
**Compute:** the Free Edition serverless default. Do not attach a custom cluster.

| Task | Notebook | Depends on |
|---|---|---|
| `bronze` | `01_bronze_openfda` | — |
| `silver` | `02_silver_validated` | `bronze` |
| `gold` | `03_gold_supplier_risk` | `silver` |

**Strictly sequential.** Free Edition caps concurrent tasks at 5, and the medallion
layers have a real data dependency — Silver reading a half-written Bronze would
produce a clean-looking, wrong result: a validated output that is not a correct one.

**Settings:**
- Max concurrent runs: **1**. Overlapping runs would double-append to Bronze.
- Retries: **0** on `bronze`. A retry against a rate-limited openFDA endpoint burns
  quota and appends a partial pull. Investigate rather than retry.
- Notifications: email on failure. **Also on success** for the first month — a job
  that silently stops running looks exactly like a job with nothing to report.
- Timeout: 45 minutes across the run.

## The invariants are the alarm

Silver's S1–S6 and Gold's G1–G6 already raise on structural violations, so the job
inherits real failure detection rather than only "did the notebook throw."

Keep structural failures and data drift separate, and do not let this blur:

- **Structural invariants → hard fail the task.** These describe the code. A
  duplicate primary key or a broken reconciliation means the pipeline is wrong.
- **Drift checks → warn, never fail.** These describe last Tuesday's data. A row
  count moving because FDA published more recalls is the feed working, not a bug.
  A job that fails on this teaches its owner to ignore its alerts.

## ⚠️ The weekly run reverts hand-edited metadata

`supplier_risk` is written with `.mode("overwrite").option("overwriteSchema","true")`,
and its table and column comments are re-applied afterwards from the `GOLD_COLS`
dict in `03_gold_supplier_risk.py`. So **any `COMMENT` typed directly into the SQL
editor is wiped on the next run — which, once this job exists, means every Sunday.**

Genie reads those comments. A metadata fix made by hand will appear to work when
tested and quietly disappear days later, with nothing failing and no alert. That is
worse than not fixing it, because by then nobody is watching.

**Rule: metadata changes go in the notebook, never in the SQL editor.** If you do
patch by hand to test something quickly, fold it into `GOLD_COLS` in the same
sitting or write it down as owed.

## After the first scheduled run

- [ ] Confirm Bronze appended exactly one batch per feed, not two.
- [ ] Confirm `pipeline_quality.source_last_updated__*` advanced (or note that the
      feed genuinely did not publish — those look identical from inside).
- [ ] Re-check the join rate. A sharp move is a drift signal worth a field note,
      not a failure.
- [ ] Record the actual quota consumed. That number is a real field-report finding:
      a new user planning a schedule has no way to estimate it up front.
