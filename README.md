# Databricks medallion pipeline over openFDA

A Bronze → Silver → Gold pipeline on **Databricks Free Edition**, built over two
live public openFDA feeds — drug shortages and recall enforcement — registered in
Unity Catalog with a Genie space on top.

**This is a Free Edition sidecar, not production scale.** One weekly job, two
feeds, a single small warehouse. It is a working pipeline built to learn the
platform and to write down what a new user actually hits, and it is described
that way throughout.

## What is here

| Path | What it is |
|---|---|
| `notebooks/01_bronze_openfda.py` | Lands both feeds as append-only raw Delta tables. Stores exactly what the API returned, plus ingestion metadata |
| `notebooks/02_silver_validated.py` | Schema enforcement, dedupe, date parsing, and validation expressed as rates against a tolerance band |
| `notebooks/03_gold_supplier_risk.py` | Per-firm join into `gold.supplier_risk`, plus a `gold.pipeline_quality` table that publishes the pipeline's own data-quality metrics |
| `docs/genie_space_setup.md` | The Genie space configuration: scope, instructions, example queries, seeded questions |
| `docs/genie_verification_worksheet.md` | A blank eight-test protocol for verifying the Genie space. **No run has been recorded** |
| `docs/dashboard_and_job_setup.md` | AI/BI dashboard spec and the weekly job definition |

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
