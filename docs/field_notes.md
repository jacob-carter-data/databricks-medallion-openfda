# Build notes: every place this pipeline would have been confidently wrong

Written during the build, 2026-08-05 to 2026-08-14, at the moment of each finding
rather than reconstructed afterward. Reconstructed friction reads like marketing.
Recorded friction reads like a practitioner.

**One exception, marked as one:** F12 was found on 2026-08-18, after the build
closed, and is about this file rather than the pipeline. It is dated in place
rather than folded in silently.

## What this file is for

A pipeline that only emits its happy path is not measurable, and a demo that only
ever passes proves nothing. So this is not a list of features. Each finding below
records something that went wrong, what the fix was, and one thing more:

> **Without this decision:** what a competent build that skipped it would have
> shipped instead.

That last line is the point of the document. The interesting question about a
governed pipeline is never whether it works. It is what it costs you when you
leave the governance out, and that cost is almost never an error message. It is a
confident, well-formatted, wrong answer, arriving in a dashboard or out of a
natural-language layer where nobody thinks to check it.

Every one of the divergences below was found by reading output with domain
knowledge. Not one was caught by a test.

| Decision made here | What a build without it produces |
|---|---|
| Severity decomposed out of the composite score, which was renamed `activity_score` | A discount retailer with 117 reversible-harm recalls ranked twice as risky as a manufacturer with 22 potential-death ones |
| Full outer join with an explicit match flag | 37% of shortage firms silently absent, exposure understated, no signal that anything was dropped |
| Checks split into structural (hard fail) and drift (warn) | An alarm that fires every week the FDA publishes, until someone switches it off |
| Metric definitions living in Unity Catalog column comments | The semantic layer and the analyst surface drift apart, and Genie ranks on the wrong column |
| Data quality published as a Gold table | Asked which firm has the best quality management, the natural-language layer answers with a ranking instead of refusing |
| Grain restricted to firms with attributed activity | "How many firms are we tracking?" returns 1,647 when the answer is 1,471 |
| Entity resolution left untuned, the count published as an upper bound | A tuned matcher, a better-looking number, and no way to know whether it is more correct |

The findings keep their original numbers, `F1` through `F12`, which are the order
they were found in. The sections below are thematic, so the numbers do not run in
sequence.

---

# Part 1 — Findings that changed a design decision

## F1. Inferring the schema from a sample would have silently dropped four fields

A single-record probe (`?limit=1`) against the shortages endpoint returns **15
fields**. Profiling all 1,651 records returns **19 distinct fields, of which only
10 appear in every row**:

| Field | Present in |
|---|---|
| `dosage_form` | 1,634 / 1,651 (99.0%) |
| `openfda` | 1,463 (88.6%) |
| `availability` | 1,180 (71.5%) |
| `related_info` | 1,150 (69.7%) |
| `discontinued_date` | 446 (27.0%) |
| `shortage_reason` | 439 (26.6%) |
| `related_info_link` | 45 (2.7%) |
| `change_date` | 32 (1.9%) |
| `resolved_note` | 23 (1.4%) |

`discontinued_date`, `related_info_link`, `change_date` and `resolved_note` do not
appear in the first record at all.

The enforcement feed is better behaved: 25 fields, 21 of them universal. But
`center_classification_date` is present in **17,859 of 17,860** rows. Exactly one
record is missing it, which defeats any `NOT NULL` assumption derived from
spot-checking. The declared schema in `02_silver_validated.py` carries the same
split: 21 fields commented "present in all 17,860 rows", then
`center_classification_date`, `termination_date` and `more_code_info` as optional,
plus `openfda` handled outside the struct.

**Absence in a sample is not absence in the dataset.**

> **Without this decision:** four fields dropped at ingestion with no error, no
> warning, and no row-count change. The pipeline reconciles perfectly against a
> schema that is missing a quarter of the columns, and `discontinued_date` — which
> the shortage lifecycle depends on — is simply not there. This is the
> tutorial-shaped mistake: sample the response, build the model, move on.

## F3. "Active" has to be defined, not assumed

1,651 shortage records span **1,627 distinct** `(generic_name, company_name,
package_ndc, presentation)` keys. The 24 collisions are **not** duplicates. Each is
the same product recorded at two different lifecycle stages:

| Field | Entry A | Entry B |
|---|---|---|
| `status` | To Be Discontinued | Current |
| `initial_posting_date` | 10/01/2025 | 01/19/2023 |
| `update_type` | New | Reverified |
| `discontinued_date` | 10/01/2025 | *(absent)* |

Collapsing on the business key would destroy real history, so Silver deduplicates
on exact `_record_hash` only, removing exactly one row: 1,651 to 1,650.

That forces a metric definition. `status` distributes as **Current 1,180 / To Be
Discontinued 447 / Resolved 24**, so "active shortage" is a choice somebody has to
make and write down where a consumer can find it. In this build that place is the
Unity Catalog column comment, because that is what a Genie space reads.

Incidental corroboration that the optional fields are lifecycle-dependent rather
than randomly missing: `availability` is populated in exactly 1,180 rows, matching
the `Current` count.

> **Without this decision:** counting rows per firm silently mixes resolved and
> prospective shortages into a column labeled current risk. The number is not
> wrong in any way a test can see. It answers a different question than its name
> claims, and every downstream consumer inherits the confusion.

## F4 and F5. The cross-feed join is partial, and the honest treatment is to publish the rate

The Gold premise is joining shortages to recalls on firm. Measured:

| | Value |
|---|---|
| Distinct raw firm strings, shortages | 135 |
| ...which normalize to distinct `firm_key`s | 132 |
| Distinct raw firm strings, enforcement | 1,649 |
| ...which normalize to distinct `firm_key`s | 1,422 |
| Overlap on **raw** strings | 31 |
| Overlap after **normalization** | 74 |
| Overlap after adding `openfda.manufacturer_name` | **83** |

Normalization means uppercasing, stripping punctuation, and dropping corporate
suffixes and `PHARMACEUTICALS` / `LABORATORIES`-class tokens. The progression,
against the 132 normalized shortage keys:

| Pass | Method | Matched | Rate |
|---|---|---|---|
| 0 | raw exact string | 31 | 23.5% |
| 1 | normalized firm name | 74 | 56.1% |
| 2 | plus `openfda.manufacturer_name` | **83** | **62.9%** |

Normalization is worth **+43 matches**. The openFDA manufacturer enrichment adds
**+9** on top, and that second number is reported as small because it is small:
that field is populated in only 18% of enforcement rows, so a modest lift is what
should have been expected. A measured null-ish result is still a result, and
inflating it would defeat the point of measuring.

37% of shortage firms remain unmatched. They are reported as **unmatched**, never
as *failed to match*. A company with a current shortage may genuinely have no
recall record, and that is a true absence rather than a defect. Telling the two
apart requires manual review that has not been done. Both kinds are visible in the
data: `CEPHALON A WHOLLY OWNED SUBSIDIARY OF TEVA INDUSTRIES` needs parent-company
resolution, while `AVYXA` may simply have a clean recall history.

Gold therefore uses a full outer join with an explicit match flag, and publishes
the join rate in `gold.pipeline_quality`.

> **Without this decision:** an inner join, which is the default thing to write.
> It discards 37% of shortage firms and understates exposure, and it reports
> nothing at all. The output looks cleaner, because everything left in it matched.

*Correction found while preparing this file for publication:* the original note
gave 135 as the shortage firm count and 56.1% as the match rate in the same table.
Those use different denominators. 135 is distinct raw firm strings; the rate is
against the 132 normalized keys that survive into Gold. Both numbers are right and
putting them side by side without saying so was not. Normalization changes the
denominator, which is exactly the kind of thing that is easy to miss.

## F6. Absolute row counts are the wrong kind of alarm on a live feed

A real bug in the first version of the Silver notebook, and worth more than most
of the platform findings.

The notebook asserted absolute counts profiled from the **raw** 1,651 records, then
compared them against **post-dedupe** Silver at 1,650. Every shortages count came
out exactly one low:

| Check | Asserted | Actual | Δ |
|---|---|---|---|
| `openfda` populated | 1,463 | 1,462 | −1 |
| `discontinued_date` nulls | 1,205 | 1,204 | −1 |
| `change_date` nulls | 1,619 | 1,618 | −1 |

A uniform −1 is the single dropped duplicate. The pipeline was correct; the
baseline was measured at the wrong stage.

The shallow fix is to decrement three constants. The real fix is noticing that an
absolute count is the wrong kind of gate on a continuously published feed. Checks
are now split by what they describe:

- **Structural (S1 through S6), hard failure.** Accounting balances, dates never
  parse silently to NULL, dedupe touched only exact hash duplicates, no undeclared
  field dropped, categorical domains closed. These hold no matter how the data
  moves, because they are properties of the pipeline.
- **Drift (D1), warn only.** Population rates against the profiled baseline within
  a ±5pp band. These are properties of a snapshot, which legitimately moves.

The question that generalizes: **which of your assertions describe your code, and
which describe last Tuesday's data?**

> **Without this decision:** as a weekly job, this fails on every run the FDA
> published anything into. An alarm that always fires is an alarm that gets
> switched off, which leaves the pipeline *less* protected than having no alarm at
> all, because now everyone believes it is covered.

## F8. Reconciliation was exact while 10.7% of the table was empty

Gold's first run. Both reconciliation invariants were exact:
`SUM(shortages_total)` = 1,650 and `SUM(recalls_total)` = 17,860, matching Silver
to the row. The join was doing exactly what it claimed.

The firm count still did not add up:

| | |
|---|---|
| shortage firms | 132 |
| recall firms (via `recalling_firm`) | 1,422 |
| matched in both | 83 |
| **expected union** | **1,471** |
| **actual** | **1,647** |

176 unexplained rows. The cause: `firm_xref` spans three sources, and one of them,
`openfda.manufacturer_name`, is used to establish that a *match* exists but
deliberately does not attribute recall counts. Attributing to both the recaller and
the manufacturer would double-count and break the reconciliation invariant. So a
firm named only inside someone else's recall metadata arrives with every count at
zero.

**The reconciliation invariants could not have caught this.** They assert that
attributed totals tie out, and 176 rows of zeros tie out perfectly. Row-count
reconciliation proves nothing was lost or duplicated. It says nothing about whether
every row deserves to exist. Those are different questions, and it took a hand
check of 132 + 1,422 − 83 to notice.

Fix: grain restricted to firms with at least one attributed shortage or recall
(1,471), with the excluded count published in `gold.pipeline_quality`.

**Recorded as a blind spot, not a cleanup.** Those 176 are contract manufacturers
whose products were recalled by other parties. That is genuine supply-chain
exposure and this pipeline does not measure it. Arguably it is the most
interesting exposure in the dataset, because it is the kind that hides behind a
distributor's name. Measuring it needs a deliberate attribution model — does a
recall belong to the recaller, the manufacturer, or both — carried consistently
through every downstream number. Naming the gap is honest. Quietly dropping the
rows would not have been.

> **Without this decision:** one row in ten is empty, which teaches readers to skim
> past rows, and Genie answering "how many firms are we tracking?" says 1,647 when
> the answer is 1,471. Wrong by 176, with every check green.

## F9. Every check passed and the ranking was still wrong

Gold's numbers verified exactly against the raw openFDA API. Three firms hand
checked, all matching to the record:

| Firm | API ongoing / class | Gold | Score arithmetic |
|---|---|---|---|
| Kilitch Healthcare India | 22 ongoing, 22 Class I | 22 / 22 | 5×22 = 110 |
| Family Dollar Stores | 117 ongoing, 117 Class II | 0 / 117 | 2×117 = 234 |
| Akorn | 118 ongoing (116 II, 2 III) | 118 | 2×116 + 1×2 = 234 |

*Figures from the first Gold run of the 2026-08-05 build; the counts move with the
live feed (see F12). What does not move is the arithmetic, which is the finding.*

Every invariant passed. Every number reconciled. And:

> **A discount retailer with 117 reversible-harm recalls outranked a manufacturer
> with 22 potential-death recalls, two to one.**

Class I means a reasonable probability of serious adverse health consequences or
death. Class II means temporary and medically reversible. The weighted sum said
Family Dollar was twice the risk of Kilitch, because 117 × 2 beats 22 × 5.

**No weighting fixes this.** Raise Class I to 50 and some firm with 500 Class II
recalls overtakes it again. A single weighted sum cannot rank on two dimensions
that trade off; volume always wins at sufficient scale. The bug was not the
numbers. It was asking one number to answer two questions.

Fix: the composite was renamed from `risk_score` to `activity_score`, which is what
it measures, and severity moved into `has_active_class_i` and
`max_active_classification`. Dashboards and Genie sort severity first, volume
second. `Not Yet Classified` recalls, which are invisible to every class count and
contribute zero to the score, now surface through `max_active_classification`
rather than silently reading as "no recalls."

The same output produced a second finding: **"supplier" is the wrong word.**
Enforcement records name whoever *initiated* the recall. Family Dollar Stores is a
retailer; Tri-Coast Pharmacy is a compounding pharmacy. Both records are
legitimate, and `product_type` is `Drugs` for both, but neither is a supplier in a
vendor-management sense. The table now says plainly that it mixes manufacturers,
repackagers, distributors, retailers and pharmacies without distinguishing them.
Worth noting that Kilitch's recalls are for *Up&Up* products, a store brand, which
makes it a contract manufacturer and exactly the hidden-exposure pattern from F8.

> **Without this decision:** the column keeps the name `risk_score`, the dashboard
> sorts on it, and the top row is read as *the riskiest firm* no matter what the
> documentation says. Validation proves a pipeline does what it was told. It cannot
> tell you that you asked for the wrong thing.

## F10. Entity resolution fails in both directions, and the decision was to stop rather than tune

The severity-ranked output put `Hospira Inc.` at row 17 with 0 shortages, while the
earlier volume-ranked list had `Hospira, Inc., a Pfizer Company` with 166. Same
company, two rows, exposure split between them.

| Raw | `firm_key` |
|---|---|
| `Hospira Inc.` | `HOSPIRA` |
| `Hospira, Inc., a Pfizer Company` | `HOSPIRA A PFIZER` |

`a Pfizer Company` is a descriptor, not part of the name, and the suffix list does
not know that. Measured across the Gold key set: **125 keys extend another existing
key** — `ACTAVIS` versus `ACTAVIS ELIZABETH` / `FL` / `MID ATLANTIC`, `CARDINAL`
versus `CARDINAL HEALTH 200`, `AUROBINDO` versus `AUROBINDO UNIT I`, `BAYER` versus
`BAYER HEALTH CARE`.

**The same measurement shows the opposite failure.** `ABBOTT` and
`ABBOTT S COMPOUNDING PHARMACY` share a first token and are almost certainly
unrelated: a small pharmacy, not Abbott Laboratories. And keys like `ADVANCED` and
`COMPOUNDING` exist only because suffix-stripping consumed everything distinctive,
so they risk merging genuinely different firms. Under-merging and over-merging, in
one number.

**Decision: publish the number, do not tune the matcher.** Stripping
`a <X> Company` and splitting on `DBA` are each a one-line regex and would visibly
reduce the split count. But there is no labelled ground truth here, so "improving"
the matcher means moving a number nobody can verify while silently creating new
false merges. An unmeasurable improvement is not an improvement. It is a change.

`firm_key_split_candidates` is published in `gold.pipeline_quality` as a **count**
and explicitly labelled an **upper bound**, not a defect rate, because it includes
coincidental prefix collisions. It is never quoted as a percentage. The honest next
step is a labelled firm crosswalk, which is real work and out of scope here.

This also partly explains the 62.9% join rate. A company split across two keys
cannot match itself.

> **Without this decision:** a one-line regex, a smaller split count, a
> better-looking metric, and no way to tell whether any of it made the data more
> correct. The number improves and the knowledge does not.

## F11. The rail held at the model layer, and a clean pass is the weaker finding

Eight-test Genie protocol run 2026-08-14, all eight passed. Full evidence:
[`genie_verification_2026-08-14.md`](genie_verification_2026-08-14.md). The blank
protocol, written before the answers were known, is at
[`genie_verification_worksheet.md`](genie_verification_worksheet.md).

The test that mattered was B1, "which firm is the riskiest?", asked cold in a fresh
thread with the F9 trap live. **On the 2026-08-14 snapshot**, ranked by
`activity_score` the leader was Fresenius Kabi at 524; ranked by severity it was
Kilitch Healthcare India at 22 active Class I. Both figures are as of that date,
and the first has since moved — see **F12**. **Genie chose severity**, ordered on
`recalls_class_i_active` rather than
`activity_score`, and volunteered the distinction unprompted: it named Pfizer and
B. Braun as having higher activity scores and explained that this reflects "their
volume of activity across all recall classes and drug shortages, not severity."

Two layers were in effect and they are worth keeping separate. The **data layer**,
the `activity_score` column comment reading "Measures HOW MUCH activity, not HOW
SEVERE", is deterministic and always applies. The **prompt layer**, the space
instructions telling Genie to rank on `has_active_class_i` first, is
probabilistic. The pass shows both held together on this phrasing. It does not show
the prompt layer holds alone, and it cannot, because separating them would require
removing a comment that should not be removed.

**What this pass is worth.** A B1 *failure* would have been the better finding: a
rail written specifically to prevent an error, failing anyway. A pass is the
outcome you want and the weaker artifact. The honest way to carry it is that the
rail held on the phrasings tried, and that is a statement about the phrasings as
much as about the rail.

The three C-series refusals landed better than the bar. C2 asked Genie to rank
firms on quality management, something the data cannot support. It refused with a
reason nobody wrote into the instructions: *absence of evidence is not evidence of
quality*. A firm with zero recalls may be clean, or may simply have failed to
match, and it cited the pipeline's own 62.9% cross-feed join rate as the reason.

> **Without these decisions:** the metric definition lives in a notebook cell
> instead of a Unity Catalog comment, so the analyst surface never sees it and
> Genie ranks on `activity_score` like any other numeric column. And the join rate
> lives in a log instead of a Gold table, so it is not available to be cited, and
> C2 returns a confident ranking of firms by a quality signal that does not exist
> in the data. A published data-quality metric came back out of the
> natural-language layer as a caveat on an answer. That is the argument for
> publishing quality as a table rather than logging it, and it makes itself.

**A process finding, not a product one.** Two of the eight results were recorded
without retaining Genie's response: A1, captured only in part, and the three B1
rephrasings, checked and passed but not saved. The verdict is unaffected and both
are labeled in the evidence file. But the protocol's own instruction was to paste
verbatim, and the run drifted from it under time pressure on the tests that were
passing. **The tests you are confident about are the ones whose evidence you stop
keeping**, which is exactly backwards: a confident pass with no transcript is
indistinguishable later from a pass you assumed.

## F12. The pipeline treats absolute counts as unsafe, then hard-coded one in the prose and another in the prompt

*Found 2026-08-18, four days after the build closed — by reading the demo
screenshots published to this repository against the prose in this file. It is the
one finding here that was not recorded during the build, and it is dated as such.*

F6 established that an absolute row count is the wrong kind of alarm on a live
feed: the feed legitimately moves, so a fixed threshold fires on the publisher's
schedule rather than on a defect. The volume checks were written as rates against a
band for exactly that reason.

Then this file wrote, in the present tense: *"Ranked by `activity_score` the leader
is Fresenius Kabi at 524."*

On 2026-08-16 the weekly job's manual test run re-ingested both openFDA feeds and
rebuilt Bronze → Silver → Gold. `activity_score` is computed from a live feed, so
it moved.

| | 2026-08-14 snapshot | 2026-08-16 rebuild |
|---|---|---|
| `activity_score` leader | Fresenius Kabi USA, 524 | **Pfizer Inc., 180** |
| Severity leader | Kilitch Healthcare India, 22 active Class I | **Kilitch, 22 — unchanged** |

Nothing broke. The number is supposed to move. What was wrong was the tense.

**The fix is a habit, not an edit.** A numeric claim about live data carries the
date of the snapshot it describes, or it is not made. Note that the dated run
record, [`genie_verification_2026-08-14.md`](genie_verification_2026-08-14.md), was
correct as written and is deliberately **not** retro-edited: its value is that it
was fixed at a point in time and says so. Prose that speaks in the present tense
about a moving number has no such defence, which is why the correction belongs
here and not there.

**The same defect is in the Genie space instructions, and there it is worse.**
Looking for a second instance of the pattern turned one up immediately. The space
instructions hardcode the cross-feed join rate as a literal — *"Only 62.9% of
shortage firms matched into recall data"* ([`genie_space_setup.md`](genie_space_setup.md)) —
and the C2 refusal captured on 2026-08-17, after the rebuild, cites 62.9% back.

That looked at first like evidence the rate had survived the rebuild. It is not.
Genie was told the number. It repeated what it was told, in a sentence that reads
exactly like a figure read from `gold.pipeline_quality`, which is a published Gold
table computed fresh on every run and therefore the one place the true current
rate actually lives.

**Prose that goes stale is a documentation bug. An instruction that goes stale is
a confident wrong answer with a governance rail's authority behind it**, delivered
by the natural-language layer to someone who has no way to tell the difference. It
is the F9 failure mode — every check green, the answer wrong — relocated into the
prompt.

The honest position, stated because the alternative is asserting the thing this
note is about: **the current join rate is unknown here.** 62.9% describes the
2026-08-14 build. Whether the 8/16 rebuild moved it has not been checked, and the
screenshot cannot settle it either way.

> **Fix, not yet applied:** the instruction should name the table and the column
> instead of the value — *"cite the current cross-feed join rate from
> `gold.pipeline_quality`"* — so the rail points at the number rather than
> carrying a copy of it. Tracked; it needs the workspace.

**The rail held twice, on data it had never seen.** This is worth more than the
correction is worth as an inconvenience. After a full rebuild on fresh data,
Kilitch is still the severity leader, and Genie still ranked on severity —
volunteering again, unprompted, that Pfizer carries the higher activity score and
that this measures "how much regulatory activity a firm has, not how dangerous
that activity is." F11 could only claim the rail held on a single snapshot. It now
holds across two, on data that did not exist when the column comment was written.
That is a strictly stronger result than the one F11 records, and it was obtained by
accident.

> **Without this decision:** the repository argues that the real cost of skipping
> governance is a confident, well-formatted, wrong answer — while carrying one in
> its own build notes, contradicted by a screenshot committed two directories away,
> and a second one pinned inside the rail that was built to prevent exactly this.
> The failure mode this project exists to describe does not exempt the description
> of it, and a governance layer is not exempt from being governed.

---

# Part 2 — Ingestion and correctness of the raw pull

## F2. Pagination without an explicit sort is correctness by luck

Bronze reported 1,651 rows but 1,650 distinct `_record_hash` values.

The suspicious explanation was ours, not the FDA's: the pull paginates with `skip`
and sets no `sort`, and unstable ordering across pages would fetch one record twice
while missing another, with the total still matching and the loss hidden.

Tested by re-pulling with an explicit `sort=update_date:asc`. Still 1,651 fetched,
1,650 distinct. Reproducible under stable ordering, so it is a genuine duplicate in
the source:

> Quinapril Hydrochloride Tablet, Aurobindo Pharma USA, NDC 65862-618-90, status
> Current, posted 01/19/2023. Appears twice, byte-identical.

Worth recording the process rather than only the answer: the benign explanation was
assumed first and the dangerous one was tested for. The benign explanation turned
out to be right, and relying on it being right would still have been wrong, so an
explicit `sort` was added to the Bronze pull regardless
(`01_bronze_openfda.py`, `update_date:asc` and `report_date:asc`). openFDA does not
document a guaranteed default ordering, and correctness should not depend on
undocumented behaviour that happened to hold once.

> **Without this decision:** a double-fetched record and a dropped one, with the
> row count tying out perfectly. The kind of data loss that hides behind a passing
> check.

Bronze also carries `_ingested_at`, `_source_url`, `_api_last_updated` and
`_record_hash` on every row. Provenance is cheap at write time and unrecoverable
later.

## F5, structural. Silver ran clean

First Silver run. All structural invariants held:

| | shortages | enforcement |
|---|---|---|
| rows in → out | 1,651 → 1,650 | 17,860 → 17,860 |
| exact duplicates dropped | 1 | 0 |
| distinct business keys | 1,627 | — |
| date parity, all columns | ok | ok |
| undeclared fields dropped | none | none |

---

# Part 3 — Platform and onboarding friction

Separated from Part 1 on purpose. The findings above are data-engineering judgment
and would apply on any platform. These are about getting onto Databricks, and they
are what an enablement program has to front-load. See
[`enablement_track.md`](enablement_track.md) for where each one lands.

## F7. Hidden notebook state, and why "import" does not mean "replace"

Hit while re-running a corrected notebook. The invariants cell failed with a bare
`NameError: name 'report' is not defined`, while a scorecard full of correct
numbers sat directly above it on screen.

Both facts were true at once because they came from **two different notebooks**.
Importing a corrected copy of a file does not overwrite the original. Databricks
creates a second notebook alongside it, `02_silver_validated (1)`, with its own
fresh, empty Python session. The visible output belonged to the first copy; the
executed cell belonged to the second.

Two traps stacked, and they compound:

1. **Cells share one hidden session.** A cell works because earlier cells built
   state that is invisible in the cell itself. Run one out of order, or attach to a
   new session, and it fails in a way that reads like a code defect rather than an
   execution-order problem.
2. **Import creates, it does not replace.** The ` (1)` suffix is easy to miss, and
   until you notice it you are reading output from one file while editing another.

**Why this matters more than it looks.** The two common entry paths onto Databricks
are people arriving from application code and people arriving from SQL and schemas.
Neither comes from a world with hidden interpreter state: a script runs top to
bottom every time, and a SQL statement is self-contained. Notebook state is the
single biggest conceptual gap for both, and nothing in the default experience
teaches it. You learn it by losing an hour to a `NameError` that is not about your
code.

**Fixed in the artifact rather than only noted:** the scorecard and invariants cells
now open with a precondition check that catches the missing state and raises a
message naming both the execution-order cause and the duplicate-notebook cause,
instead of letting a bare `NameError` through. Cheap, and it converts a confusing
failure into a self-explaining one.

## Serverless runtime notes

Small, but the kind of thing course content has to get right or learners hit it in
session one.

- Runtime is serverless Spark. The kernel runs under a per-session
  `spark-<uuid>` home directory inside the container, so any path a notebook
  prints is ephemeral and not a workspace location.
- Python is new enough that `datetime.datetime.utcnow()` raises a
  `DeprecationWarning` recommending `datetime.datetime.now(datetime.UTC)`. Any
  teaching material using `utcnow()` emits warnings in a learner's first notebook,
  which reads as "the platform is broken" to a beginner. Timezone-aware datetimes
  throughout. Fixed in the Bronze notebook.

## Free Edition constraints

These are specific to Free Edition and do not generalize to a paid workspace. They
are recorded because the build ran under them, not because they are the subject.

Known before starting, from the Free Edition limitations documentation:

| Constraint |
|---|
| Outbound internet restricted to a limited set of trusted domains until identity verification |
| Serverless compute only; no custom compute, no GPUs; all-purpose compute limited to small |
| One SQL warehouse, `2X-Small` |
| Max 5 concurrent job tasks per account |
| One active pipeline per pipeline type |
| Quota overrun shuts down workspace compute for the rest of the day, in extreme cases the month |
| Accounts may be deleted after prolonged inactivity |
| Includes Unity Catalog, serverless SQL warehouses, AI/BI dashboards and Genie spaces |

### The outbound gate, and a hypothesis of mine that was wrong

**Going in, the hypothesis was** that the outbound restriction is the highest-impact
onboarding blocker, because the obvious first thing a new data person tries is
pulling a public API into a notebook, and the failure would not obviously point at
an account-verification setting.

**That hypothesis was largely refuted.** Databricks surfaced a verify button on the
workspace home page, unprompted, before any failure occurred. The feared sequence —
hit an opaque network error, then hunt for the cause — is not what a new user
encounters by default. The product front-loads the fix instead of leaving it to be
discovered after the error. That is good onboarding design and it is recorded as
such, because a report that only confirms its author's priors is worth nothing.

**What remains open, and is narrower:** whether that prompt explains *what
verification unlocks*. That was not assessed. The residual question is not "is the
fix discoverable" — it is — but "does a user understand what they are gaining, and
would they bother without a reason to?"

### What was not captured, stated plainly

**The blocked state was never observed first-hand.** Identity verification was
completed before any external API call was attempted, so the failure mode was never
seen. This is recorded as a miss rather than quietly dropped.

What that costs: the verbatim error text and the first-hand account. What survives:
the finding itself, which is documented by Databricks and independently verifiable.
The honest form is therefore:

> Free Edition restricts outbound internet access until you complete identity
> verification. I happened to verify before my first external API call, so I did not
> hit it. That ordering is luck. A new user whose first task is pulling a public
> dataset will hit it, and will see a network-level error with nothing pointing at
> an account setting.

That is a weaker claim than a screenshot of the failure, and it is stated as the
weaker claim.

**Deliberately not done:** creating a second account to reproduce the blocked state.
Free Edition is governed by a fair-use policy and per-account quotas, and spinning
up a duplicate to game a limit would contradict this artifact's own premise. The
finding stands on the documentation.

The test after verification, verbatim:

```
[OK]   openFDA shortages:   HTTP 200,      2,264 bytes
[OK]   openFDA enforcement: HTTP 200,      1,840 bytes
[OK]   control: pypi.org:   HTTP 200, 44,709,156 bytes
```

Both Bronze sources are reachable from a serverless notebook. Outbound access
worked immediately after verification with no further configuration: no proxy
setup, no allowlist editing, no support ticket. The gate is binary and it stays
open.

---

# Part 4 — What worked well

A report that only lists problems is a complaint, and it reads as one.

| # | What | Why it mattered |
|---|---|---|
| 1 | **The verification prompt was surfaced on the workspace home page, unprompted, before any failure occurred.** This refuted the going-in hypothesis above | The product front-loads the fix instead of leaving it to be discovered after the error |
| 2 | **Serverless meant there was no compute to configure.** No cluster sizing, no runtime selection, no waiting on a spin-up before the first line of code | The single largest historical barrier to a first Spark notebook is simply absent. For anyone who is not already a data engineer, this is what makes the platform approachable at all |
| 3 | **Bronze ran clean on the first attempt.** 1,651 + 17,860 records, row counts matching the API's own totals, catalog `workspace` exactly as guessed | No environment archaeology between signup and real data. Every problem found afterward was a *data* problem, which is where the learning value is |
| 4 | **Unity Catalog table and column comments are available on the free tier, and they are what make Genie usable.** The governance primitive is not held back for paid plans | This is what turns the exercise into a governed interface rather than a SQL passthrough. A team can be taught the right habits from day one instead of teaching them later as a migration |
| 5 | **The whole governed stack is present** — Unity Catalog, serverless SQL warehouse, AI/BI dashboards, Genie | Raw feed to governed semantic interface without leaving the free tier. That end-to-end completeness, not any single feature, is what makes it credible as a teaching platform |

---

# Part 5 — Where each finding lands in enablement

Full outline in [`enablement_track.md`](enablement_track.md).

| Finding | Audience | Where it belongs |
|---|---|---|
| **F7** — notebook state is hidden and order-dependent, and import creates a second notebook rather than replacing the first | **Both** entry paths. The single biggest conceptual gap for either | **Session 1**, taught by *inducing* the failure rather than warning about it. Pair with "run all before trusting a result" |
| **F1** — a sampled schema is not the schema | Code-first primarily | Ingestion module. The best single exercise in the build: hand them the sample, let them build the schema, then show what they dropped |
| **F2** — pagination without an explicit `sort` is correctness by luck | Code-first | Ingestion module, immediately after F1 |
| **F6** — which of your assertions describe your code, and which describe last Tuesday's data | Schema-first primarily | Data-quality module. Structural versus drift is the whole lesson: one slide and one lab |
| **F3** — "active" has to be defined, not assumed | Schema-first. The semantic-layer question made concrete | Metric-definition module, immediately before anything Genie- or dashboard-facing |
| **F4** — an inner join is a silent data-loss decision | **Both** | Joins module. Teach the match flag as the default habit, not the advanced technique |
| **F10** — entity resolution fails in both directions, and without ground truth "improving" the matcher is a change, not an improvement | Schema-first | Advanced module. Also the most honest possible answer to "why not just fix it?" |
| **F8 + F9** — validation proves a pipeline did what it was told; it cannot tell you that you asked for the wrong thing | **Both** | **Capstone.** Give them a pipeline where every test passes and the answer is wrong, and let them find it by reading output |
| **F11** — the metadata rail held at the model layer, and a clean pass is the weaker finding | **Both** | Genie module. Point a natural-language layer at the Gold tables and see whether the rail holds |
| **F12** — a live-data figure copied into prose or into a model instruction goes stale silently, and the instruction is the dangerous copy | **Both.** A documentation and prompt-authoring rule | Data-quality module, taught **with F6**, then revisited in the Genie module. The rule that generalizes: a rail cites the table, it never carries a copy of the value |
| Outbound gate | **Both** | **Session 0 / pre-work.** A track that does not front-load this loses learners before lesson one |
| `datetime.utcnow()` deprecation | **Both.** A content-authoring rule, not a lesson | Applies to every code sample. Timezone-aware datetimes throughout |

---

## Scope

Public FDA data, not for clinical use. Nothing here is an FDA judgment about any
company. `activity_score` is a metric constructed in this project. The population is
not a supplier list: enforcement records name whoever initiated the recall, mixing
manufacturers, repackagers, distributors, retailers and compounding pharmacies
without distinguishing them. Not production scale.
