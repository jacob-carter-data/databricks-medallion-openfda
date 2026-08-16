# Databricks medallion pipeline over openFDA

A Bronze → Silver → Gold pipeline over two live public openFDA feeds, drug
shortages and recall enforcement, registered in Unity Catalog with a Genie space
on top and a verification record for it.

The pipeline is the substance. The point is what sits beside it: a written record
of **every place this pipeline would have produced a confident wrong answer, and
the data-layer decision that stopped it.**

**Not production scale.** Two feeds, one weekly job, a single small warehouse,
built on Databricks Free Edition. That constraint is stated wherever it affects a
result, and nothing here is presented as measured unless it was measured.

## What this demonstrates

A pipeline that works is not the interesting claim. Any competent build on these
feeds works. The interesting question is what it costs when the governance is left
out, and that cost is almost never an error message. It is a confident,
well-formatted, wrong answer arriving in a dashboard or out of a natural-language
layer where nobody thinks to check it.

| Decision made here | What a build without it produces |
|---|---|
| Severity decomposed out of the composite score, which was renamed `activity_score` | A discount retailer with 117 reversible-harm recalls ranked twice as risky as a manufacturer with 22 potential-death ones |
| Full outer join with an explicit match flag | 37% of shortage firms silently absent, exposure understated, no signal anything was dropped |
| Checks split into structural (hard fail) and drift (warn) | An alarm that fires every week the FDA publishes, until someone switches it off |
| Metric definitions living in Unity Catalog column comments | The semantic layer and the analyst surface drift apart, and Genie ranks on the wrong column |
| Data quality published as a Gold table | Asked which firm has the best quality management, the natural-language layer answers with a ranking instead of refusing |
| Grain restricted to firms with attributed activity | "How many firms are we tracking?" returns 1,647 when the answer is 1,471 |
| Entity resolution left untuned, the count published as an upper bound | A tuned matcher, a better-looking number, and no way to know whether it is more correct |

Every one of those was found by reading output with domain knowledge. Not one was
caught by a test. Each is written up, with the measurement behind it, in
[`docs/field_notes.md`](docs/field_notes.md).

## What is here

Reading order, if you are landing here cold: the build notes, then the Genie
verification record, then the code.

| Path | What it is |
|---|---|
| [`docs/field_notes.md`](docs/field_notes.md) | **Start here.** Findings F1–F11, written during the build. Each carries what a build without that decision would have shipped instead |
| [`docs/genie_verification_2026-08-14.md`](docs/genie_verification_2026-08-14.md) | The eight-test Genie run of 2026-08-14. All eight passed. It grades its own evidence, marks two passes as weaker than the other six, and says outright that a failure would have been the more useful finding |
| [`docs/genie_verification_worksheet.md`](docs/genie_verification_worksheet.md) | The blank protocol, kept alongside the run so the tests are visibly older than the results |
| [`docs/enablement_track.md`](docs/enablement_track.md) | An outline for getting a team productive on Databricks and safe to put an AI layer on top, built from the findings rather than from a feature list |
| [`docs/genie_space_setup.md`](docs/genie_space_setup.md) | The Genie space configuration: scope, instructions, example queries, seeded questions |
| [`docs/dashboard_and_job_setup.md`](docs/dashboard_and_job_setup.md) | AI/BI dashboard spec and the weekly job definition |
| `notebooks/01_bronze_openfda.py` | Lands both feeds as append-only raw Delta tables. Stores exactly what the API returned, plus ingestion metadata |
| `notebooks/02_silver_validated.py` | Schema enforcement, dedupe, date parsing, and validation expressed as rates against a tolerance band |
| `notebooks/03_gold_supplier_risk.py` | Per-firm join into `gold.supplier_risk`, plus a `gold.pipeline_quality` table that publishes the pipeline's own data-quality metrics |

## The finding that justified the build

The Gold composite was originally called `risk_score`. The first run disproved
the name:

| Firm | Active Class I | Active recalls | Score |
|---|---|---|---|
| Kilitch Healthcare India | 22 | 22 | 110 |
| Family Dollar Stores | 0 | 117, all Class II | 234 |

Class I means a reasonable probability of death or serious harm. Class II means
temporary and reversible. A weighted sum ranked a retailer twice as risky as a
manufacturer with 22 potential-death recalls, because 117 × 2 beats 22 × 5.

**No linear weighting fixes this** — a large enough count beats any ratio. So the
column was renamed to what it measures, `activity_score`, severity moved into
`has_active_class_i` and `max_active_classification`, and the dashboard and Genie
space sort on severity first. The rename is the fix; the weighting was never
going to be.

That is also why the metric definition lives in the Unity Catalog **table and
column comments** rather than in a notebook cell: Genie reads those comments, so
the semantic layer and the analyst surface cannot drift apart.

**And that rail was tested rather than assumed.** Asked cold, in a fresh thread,
"which firm is the riskiest?", the Genie space ranked on severity rather than on
`activity_score` and volunteered that two other firms score higher on activity,
which "reflects their volume of activity across all recall classes and drug
shortages, not severity." Two
rails were in effect and only one of them is deterministic: the column comment
always applies, the space instruction is prompt-level and probabilistic. The pass
shows they held together on that phrasing. It does not show the prompt rail holds
alone. Both statements are in the run record, along with the two results whose
evidence was not retained.

## Data quality is published, not assumed

`gold.pipeline_quality` is a first-class output. It carries the cross-feed join
rate, source freshness per feed, and entity-resolution split candidates, because
a pipeline that only emits its happy path is not measurable.

Known limits, stated rather than waited for:

- **Only 62.9% of shortage firms match into enforcement.** The join is a full
  outer (built as two left joins onto a firm cross-reference) with an explicit
  match flag. An inner join would have dropped 49 firms and understated exposure
  silently.
- **Entity resolution is unresolved.** `firm_key_split_candidates` counts firm
  keys that are prefix-extensions of another key. It is an upper bound on splits,
  not a measured error rate. Tuning was declined: with no hand-labeled ground
  truth, an unmeasurable improvement is a change, not an improvement.
- **Contract-manufacturer exposure is not measured.**
- **No size normalization.** A firm with more products on the market has more
  recalls available to it.

## What this is not

- **Not an FDA judgment.** `activity_score` is a metric constructed here. openFDA's
  own non-clinical-use disclaimers apply.
- **Not a supplier list.** Enforcement records name whoever *initiated* the
  recall: manufacturers, repackagers, distributors, retailers, and compounding
  pharmacies, undifferentiated.
- **Not production scale**, and not a migration of anything. It reads a public
  feed and shares no data with any other system.

## Related

A companion project, [Cadence][cadence], is a retrieval app with an eval harness
that checks whether its own answers are true. The two are separate systems; the
plan is for Cadence's live-source corpus to eventually consume this pipeline's
Silver table rather than re-implement the pull.

[cadence]: https://github.com/jacob-carter-data/cadence

## License

MIT. See [LICENSE](LICENSE).
