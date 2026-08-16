# Enablement outline: getting a team productive on Databricks, and safe to put an AI layer on top

**Basis:** the build notes in [`field_notes.md`](field_notes.md), written while
building a Bronze/Silver/Gold pipeline over two live openFDA feeds and putting a
Genie space on the result.

This is an outline, not a course. It is deliberately short on slide-level detail
and long on why each module exists and how you would know it worked.

---

## Posture

Every module below exists because something went wrong during the build, and each
one says which finding motivated it. That is the design principle: **a curriculum
built from where a practitioner actually got stuck, rather than from a feature
list.**

A feature-shaped track teaches what the product has. A friction-shaped track
teaches what the product costs you before it pays you back. The second is what
turns a signup into a working engineer, and it is only writable by someone who did
the work and wrote down the failures, including the ones that made them look bad.

**And there is a second thing the track has to carry.** Getting a team fluent in
notebooks and Delta tables is the easy half. The hard half is that the moment you
put a natural-language layer in front of the result, every unstated definition and
every ungoverned join becomes a confident wrong answer delivered to somebody who
has no way to check it. The build found that at three separate layers — a
composite metric, a dashboard layout, and a Genie space — and had to fix it
separately at each. A track that teaches the platform without teaching that
produces people who ship faster and are wrong sooner.

---

## Why one track cannot serve both entry paths

Two paths lead onto Databricks, and they trip on genuinely different things:

| | Arriving from application code | Arriving from SQL and schemas |
|---|---|---|
| **Comes from** | Scripts that run top to bottom, every time | Self-contained statements; a durable, governed schema |
| **First real shock** | Notebook state is hidden and order-dependent (F7) | The same, for a different reason: there is no schema owner, and "active" is undefined until someone defines it (F3) |
| **Instinct that misleads them** | "Sample the response, build the model" (F1) | "The counts reconcile, so it is correct" (F8) |
| **What they undervalue** | Governance and metric definitions, until the AI layer answers wrongly | Iteration speed; they will fight the notebook rather than use it |
| **What they already have** | Comfort with APIs, retries, pagination | Comfort with constraints, grain, and what a key means |

**Both share one failure**, which is why it is taught once and taught first:
notebook hidden state. Neither path comes from a world that has it.

The split is not an assumption. It is falsifiable, and the measurement section
below says what result would collapse it back to a single track.

---

## Structure

Two shared sessions, then a fork, then two shared closers. The shared bookends are
deliberate: the capstone is where each group sees why the other's instincts
mattered.

```
Session 0  -- pre-work, both ---------- the gate
Session 1  -- shared ------------------ the notebook contract
              |
    +---------+---------+
  Track A            Track B
  code-first         schema-first
  (3 modules)        (3 modules)
    +---------+---------+
              |
Capstone   -- shared ------------------ validated, but wrong
Module G   -- shared ------------------ the AI layer, and whether the rail holds
```

---

## Session 0 — pre-work (both, ~20 min, asynchronous)

**Exists because of:** the outbound gate.

Free Edition restricts outbound internet to trusted domains until identity
verification is complete. The obvious first exercise in any data course, pulling a
public API into a notebook, therefore fails before the course begins, with a
network-level error that points at nothing.

- Create the account; complete identity verification **before session 1**.
- Run a three-line connectivity check and post the result. It doubles as a signal
  for whether everyone actually did the pre-work.
- State plainly what verification is: an anti-abuse identity check inside
  Databricks. Nothing is posted or shared. People will otherwise hesitate at the
  prompt, and hesitation at step one is attrition.

**Honest note for whoever runs this:** Databricks surfaces the verify prompt on the
workspace home page unprompted, so this is less dangerous than it looks. What is
*not* established is whether the prompt explains what verification unlocks. If it
does, this module shrinks to a checkbox.

On a paid workspace this module is different or absent. Keep it conditional rather
than assuming the free-tier gate.

---

## Session 1 — the notebook contract (both, ~45 min)

**Exists because of:** F7. The single biggest conceptual gap for both entry paths,
and nothing in the default experience teaches it.

**Taught by inducing the failure, not warning about it.** Warning produces nodding.
Inducing produces memory.

1. Learners run cells out of order and hit a bare `NameError` while a screen full
   of correct output sits directly above it.
2. Then the second trap, stacked: import a corrected copy of a notebook and watch
   Databricks create `notebook (1)` alongside the original, with its own empty
   session. You are now reading output from one file while editing another.
3. Only then the explanation: state is what makes iterative exploration fast, and
   the price is that correctness becomes order-dependent.
4. The habit that falls out: **run all before trusting any result.**
5. The defensive pattern: a precondition check at the top of any cell that depends
   on earlier state, raising a message that names *both* causes, execution order
   and duplicate notebooks, instead of letting a bare `NameError` through.

Point 5 is the transferable part. It is what a practitioner does about the problem
rather than merely knowing it exists, and it is already applied in the build's own
Silver notebook.

---

## Track A — code-first (3 modules)

### A1. Ingestion: the schema you inferred is not the schema
**Exists because of:** F1, the best single teaching example in the build.

1. Give learners `?limit=1`. Have them build a schema. They will get 15 fields.
2. Have them profile all 1,651 records. There are 19, and only 10 appear in every
   row.
3. Show what they dropped: `discontinued_date`, `related_info_link`, `change_date`,
   `resolved_note`. None present in the first record, none raising an error.

**Absence in a sample is not absence in the dataset**, and a sample-built schema
fails silently, which is the worst way to fail. Close with the enforcement feed's
`center_classification_date`: present in 17,859 of 17,860 rows. A single-row
anomaly defeats any `NOT NULL` derived from spot-checking.

### A2. Ingestion, part two: pagination and provenance
**Exists because of:** F2.

Paginating with `skip` and no explicit `sort` makes correctness depend on
undocumented API ordering. Unstable ordering double-fetches one record and drops
another **with the total still tying out**, so the loss hides behind a passing
count.

Teach the diagnostic process, not just the fix: assume the benign explanation, then
*test for the dangerous one*. In the build, re-pulling with an explicit sort proved
the duplicate was genuinely in the source. Then the Bronze contract: append-only,
`_ingested_at`, `_source_url`, `_api_last_updated`, `_record_hash`. Provenance is
cheap at write time and unrecoverable later.

### A3. Joining across sources without silently losing rows
**Exists because of:** F4.

An inner join between the two feeds discards 37% of shortage firms and understates
exposure, and reports nothing. Learners build it wrong first, measure the loss, then
rebuild with a full outer join and an explicit match flag.

The distinction to drill, because it is the one people skip: **unmatched is not the
same as failed to match.** A firm with a current shortage may genuinely have no
recall record. Telling true absence apart from a normalization miss needs manual
review, so the honest table reports what was observed and publishes the join rate as
a first-class number.

---

## Track B — schema-first (3 modules)

### B1. Grain, lifecycle, and defining "active"
**Exists because of:** F3.

1,651 shortage records span 1,627 distinct business keys. The 24 collisions are
**not** duplicates. They are the same product at different lifecycle stages.
Deduplicating on business key destroys real history. Dedupe on exact hash only.

That forces the semantic question, which is this group's home ground: `status`
distributes Current 1,180 / To Be Discontinued 447 / Resolved 24, so **"active
shortage" must be defined, not assumed**, and the definition has to live where a
consumer can find it. Counting all rows per firm silently mixes resolved and
prospective shortages into a number labeled current risk.

Land it in Unity Catalog table and column comments, and show that those comments are
what a Genie space actually reads. Governance stops being paperwork the moment it
visibly changes an answer.

*Open question, worth resolving before this module is delivered:* whether Unity
Catalog metric views are available on the target workspace. If they are, they are
the correct home for the "active" definition and this module should teach the
platform primitive rather than a table comment.

### B2. Which assertions describe your code, and which describe last Tuesday's data?
**Exists because of:** F6, a real bug in the build's own first version.

The Silver notebook asserted absolute row counts profiled at the wrong stage. Every
check came out exactly one low. The shallow fix is to decrement three constants. The
real fix is recognizing that **absolute counts are the wrong kind of gate on a
continuously published feed**: as a weekly job it fails every time the FDA publishes
anything, and an alarm that always fires gets switched off, leaving the pipeline
less protected than if it had none, because now everyone believes it is covered.

The split learners implement themselves:

- **Structural, hard fail.** Accounting balances, dates never parse silently to
  NULL, dedupe touched only exact duplicates, no undeclared field dropped,
  categorical domains closed. Properties of the pipeline.
- **Drift, warn only.** Population rates against a baseline, with a band. Properties
  of a snapshot, which legitimately move.

The lab: hand them ten assertions, have them sort into the two buckets and defend
the ambiguous ones. The argument *is* the lesson.

### B3. Entity resolution, and when not to fix it
**Exists because of:** F10.

Normalization lifts the cross-feed match from 31 firms to 74, and manufacturer-name
enrichment adds 9 more, reaching 62.9%. A concrete, measurable argument for doing
entity resolution at all.

Then both failure directions, from one measurement:

- **Under-merging:** `Hospira Inc.` becomes `HOSPIRA` but `Hospira, Inc., a Pfizer
  Company` becomes `HOSPIRA A PFIZER`. One company, two rows, exposure split. 125
  keys extend another existing key.
- **Over-merging risk:** `ABBOTT` and `ABBOTT S COMPOUNDING PHARMACY` share a first
  token and are almost certainly unrelated. Suffix-stripping produces keys like
  `ADVANCED` and `COMPOUNDING` that could collapse genuinely different firms.

**Then the module's actual point, which is a judgment call rather than a
technique.** Stripping `a <X> Company` is a one-line regex that would visibly reduce
the split count. It was not applied. With no labelled ground truth, "improving" the
matcher means moving a number nobody can verify while silently creating new false
merges. The number is published as an explicit upper bound, as a count and never a
rate, and the matcher left alone.

Discussion prompt, and the most valuable ninety seconds in this track: *when is
publishing a known-imperfect number better than fixing it?*

---

## Capstone — validated, but wrong (both, ~90 min)

**Exists because of:** F8 and F9, and it is why the two tracks rejoin.

Learners are handed a pipeline where **every automated check passes** and the answer
is wrong. Two defects, found by reading output rather than running tests.

**Defect one (F8).** Reconciliation is exact; attributed totals tie to source to the
row. But 176 of 1,647 rows (10.7%) are entirely zeros, because a firm named only
inside another firm's recall metadata establishes a *match* without having any
counts *attributed* to it. Row-count reconciliation proves nothing was lost or
duplicated. It says nothing about whether a row deserves to exist. Those are
different questions.

**Defect two (F9), the headline.** Every invariant green, three firms hand-verified
against the raw API, the composite score recomputing exactly from its own columns.
And:

> A discount retailer with 117 reversible-harm (Class II) recalls outranked a
> manufacturer with 22 potential-death (Class I) recalls, two to one.

Because 117 × 2 beats 22 × 5. **No reweighting fixes it.** Raise Class I to 50 and
some firm with 500 Class II recalls overtakes it again. A single weighted sum cannot
rank on two dimensions that trade off; volume always wins at sufficient scale. The
bug was not the numbers. It was asking one number to answer two questions.

The fix learners derive: rename `risk_score` to `activity_score` to say what it
actually measures, move severity into its own columns, and sort severity first
everywhere downstream.

**The generalization, which is the sentence the whole track exists to earn:**

> Validation proves a pipeline does what it was told. It cannot tell you that you
> asked for the wrong thing. Consistency, reconciliation and type safety verify
> *internal* coherence. Correctness is a claim about the world, and only domain
> review reaches it.

This is also the training version of the argument the whole build makes. The gap
between a pipeline that passes and a pipeline that is right is not visible from
inside the pipeline, and it is exactly the gap an AI layer will confidently paper
over.

---

## Module G — the AI layer, and whether the rail actually holds (both, ~60 min)

**Exists because of:** F11. Previously an optional extension; it is promoted here
because it is the module that pays for the rest.

The setup: point a Genie space at the Gold tables and ask it, cold, *"which firm is
the riskiest?"* — the same question the capstone just showed a composite score gets
wrong.

Two rails are in play, and telling them apart is the lesson:

| Rail | Where it lives | Property |
|---|---|---|
| The `activity_score` column comment: "measures HOW MUCH activity, not HOW SEVERE" | Unity Catalog metadata | **Deterministic.** It always applies |
| The space instructions: rank on `has_active_class_i` first | Prompt | **Probabilistic.** It applied on the phrasings tried; the frequency has not been measured |

In the build's run, Genie chose severity, ordered on `recalls_class_i_active`, and
volunteered that two other firms had higher activity scores reflecting "their
volume of activity across all recall classes and drug shortages, not severity."
Both rails held together. **That
does not show the prompt rail holds alone**, and it cannot, because separating them
would mean removing a comment that should not be removed. Learners should be able to
say which of their own rails is which.

Then the refusal tests, which are the harder bar. Ask the space something the data
cannot support — *"which firm has the best quality management?"* — and see whether it
refuses or invents. In the build it refused, citing a reason nobody wrote into the
instructions: absence of evidence is not evidence of quality, because a firm with
zero recalls may be clean or may simply have failed to match, and it named the
pipeline's own 62.9% join rate as why. **A published data-quality metric came back
out of the natural-language layer as a caveat on an answer.** That is the argument
for publishing quality as a Gold table rather than logging it, and it makes itself.

**The exercise that matters most is running the protocol before you know the
answers.** Write the tests, including the ones you expect to fail, then run them.
The build kept a blank worksheet and a dated run file side by side precisely so the
tests are visibly older than the results.

**And the process lesson, which is not about the product.** In the build's own run,
two of eight results were recorded without retaining the response, and both were
tests that passed. The tests you are confident about are the ones whose evidence you
stop keeping, which is exactly backwards: a confident pass with no transcript is
indistinguishable later from a pass you assumed. **Evidence discipline has to
survive good news, not just bad.**

---

## What to measure to know the program worked

The honest starting point: **completion rate and satisfaction scores would not tell
us.** Both measure whether people finished and enjoyed it, and a feature-tour course
scores well on both while changing nothing about what learners build. If the program
is judged on those, it will drift back toward a feature tour, because that is what
optimizes them.

What follows is split into what can be measured cheaply and what actually matters,
because they are not the same and pretending otherwise is the failure mode this
whole approach argues against.

### Leading indicators — cheap, available in-platform, weak on their own

| Measure | Why it is worth having | What it cannot tell you |
|---|---|---|
| Pre-work completion before session 1 | Directly tests the Session 0 hypothesis: is the gate an attrition point? | Nothing about learning |
| Time from account to first governed table | The end-to-end path the program claims to shorten | Whether the table is any good |
| Share of learner tables carrying column comments 30 days later | Governance habits either persist unsupervised or they were never taught | Whether the comments are meaningful |
| Return-to-workspace rate at 7 / 30 / 90 days | Adoption | Cause. A returning user may be doing something else entirely |

### The real measures — behavioral, each tied to a specific module

These are assessments, not surveys. Each has a right answer the learner either
produces or does not.

1. **The F1 test.** Give a new dataset and a one-record sample. Does the learner
   profile the full set before building a schema? *Binary, observable, traceable to
   module A1.*
2. **The F6 sort.** Hand over ten assertions from an unfamiliar pipeline. Can they
   sort structural from drift and defend the ambiguous ones? *Scored on the defense,
   not the sort.*
3. **The capstone find.** Given a pipeline where all tests pass, do they find the
   severity inversion, and how long does it take? *Time-to-find is the single best
   proxy for whether the central lesson landed.*
4. **The refusal test.** Given a question the data cannot support, do they say so and
   name what would be required, or do they substitute a related number? *The habit
   with the longest half-life, and it transfers to every job they will ever do.*
5. **The rail test, from module G.** Shown a system with a metadata rail and a prompt
   rail, can they say which is deterministic and which is not, and design a test that
   distinguishes them?
6. **Ninety-day artifact review.** Sample real tables built by graduates. Do they
   carry defined grain, comments, and split checks? *The only measure that survives
   contact with their actual work.*

### The measure that matters most, and is hardest to get

**Did a graduate catch a real defect in production that their automated checks
passed?** That is the program's actual thesis, and one credible instance is worth
more than any completion statistic.

It is also self-reported, rare, and lagging by months, so it cannot be the operating
metric. Collect it as a standing prompt in the 90-day check-in and treat each
instance as a case study, not a data point. **Do not build a dashboard tile for it.**
A number that reads 0 for two quarters and then 1 invites exactly the wrong reaction
from whoever is reading the dashboard.

### What would falsify the program's value

Stated up front, because a program design that cannot be wrong is not a design:

- Graduates pass the capstone but their 90-day artifacts show no defined grain and no
  comments. The track teaches a test-taking skill, not a working habit.
- Pre-work completion is high and 30-day return is flat. We solved onboarding
  friction that was not the binding constraint.
- Both tracks score the same on the forked modules. The split is unjustified overhead
  and should collapse to one track.

---

## Open questions

Flagged rather than papered over.

1. **Does the verification prompt explain what it unlocks?** Unassessed. If the copy
   is clear, Session 0 shrinks to a checkbox. If it is not, that is a product finding
   worth more than the curriculum item.
2. **Do Unity Catalog metric views exist on the target workspace?** If they do, they
   are the correct home for the "active" definition in B1, and that module should
   teach the platform primitive rather than a table comment. Unverified.
3. **What does the capstone cost in compute?** On Free Edition a quota overrun kills
   workspace compute for the rest of the day. A cohort of twenty running a Gold
   rebuild simultaneously is an untested load and a bad way to find the ceiling.
   **This needs measuring before any cohort runs, not after.**
4. **Is 90 minutes enough for the capstone?** The severity inversion took real time to
   spot *while holding all the context*. Someone with none may need a hint path, and a
   hint path that arrives too early destroys the exercise.

---

## Scope

The exercises above use public FDA data, which is not for clinical use. Nothing here
is an FDA judgment about any company. `activity_score` is a metric constructed in
this project. The population is not a supplier list: enforcement records name whoever
initiated the recall, mixing manufacturers, repackagers, distributors, retailers and
compounding pharmacies without distinguishing them. Firm names appear only as worked
examples of normalization and severity ranking, and the claim that two similarly named
entities are unrelated is an illustration of a matching hazard, not a finding about
either company. Not production scale.
