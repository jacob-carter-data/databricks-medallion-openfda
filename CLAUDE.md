# CLAUDE.md — standing instructions for this repository

This file is read by Claude Code at the start of every session in this repo. It is
committed deliberately: how a project governs AI-assisted work is part of the
project, and this repository's whole argument is that governance is the thing worth
showing.

These are constraints, not preferences. When a rule here conflicts with a
convenient shortcut, the rule wins.

---

## What this is

A Bronze → Silver → Gold pipeline over two live public openFDA feeds — drug
shortages and recall enforcement — registered in Unity Catalog, with a Genie space
on top and a verification record for it. Built on Databricks Free Edition.

**The pipeline is not the claim.** Any competent build on these feeds works. The
artifact is `docs/field_notes.md`: every place this pipeline would have produced a
confident wrong answer, and the data-layer decision that stopped it.

| Path | What it is |
|---|---|
| `notebooks/01_bronze_openfda.py` | Append-only raw Delta landing. Stores what the API returned, plus ingestion metadata |
| `notebooks/02_silver_validated.py` | Schema enforcement, dedupe, date parsing, validation as rates against a band |
| `notebooks/03_gold_supplier_risk.py` | Per-firm join into `gold.supplier_risk`, plus `gold.pipeline_quality` |
| `docs/field_notes.md` | **The deliverable.** Findings F1–F12 |
| `docs/genie_verification_2026-08-14.md` | A dated run record. Never retro-edit it |
| `docs/enablement_track.md` | The learning track, built from the findings |

---

## The honesty rails

- **Severity is not volume.** `activity_score` measures how much regulatory
  activity a firm has, not how dangerous it is. Severity lives in
  `has_active_class_i` and `max_active_classification`. Anything that ranks firms
  leads on severity; volume is subordinate and labeled. **No linear weighting
  fixes this** — a large enough count beats any ratio, which is why the fix was a
  rename and not a re-weighting.
- **Metric definitions live in Unity Catalog table and column comments**, because
  Genie reads them. That is what stops the semantic layer and the analyst surface
  from drifting apart. A comment typed into the SQL editor is silently overwritten
  by the next notebook run — set them in the notebook.
- **Data quality is a published output, not a log line.** `gold.pipeline_quality`
  carries the cross-feed join rate, per-feed freshness, and entity-resolution
  split candidates. A pipeline that only emits its happy path is not measurable.
- **Volume checks are rates against a band, never absolute counts.** An absolute
  threshold on a live feed fires every time the FDA publishes. Structural checks
  hard-fail; drift checks warn.
- **Joins are full outer with an explicit match flag.** An inner join is a silent
  data-loss decision. Unmatched is not the same as none.
- **Entity resolution stays untuned and the count is published as an upper bound.**
  With no hand-labeled ground truth, an unmeasurable improvement is a change, not
  an improvement.

### The rule this repository learned last, and the easiest to break

**A rail cites the table. It never carries a copy of the value.**

A live figure copied into prose goes stale and is a documentation bug. The same
figure copied into a *model instruction* goes stale and becomes a confident wrong
answer delivered with a governance rail's authority, to a reader with no way to
tell the difference. See **F12**. Before treating agreement between two sources as
confirmation, establish that they could have disagreed.

---

## Claims that are always wrong here

Do not write these, in any file, in any commit message, in any comment:

- That this is **production scale**. Two feeds, one weekly job, one 2X-Small
  warehouse, Free Edition.
- That `activity_score` is an **FDA judgment**. It is a metric constructed here.
  openFDA's non-clinical-use disclaimers apply.
- That the population is a **supplier list**. Enforcement records name whoever
  *initiated* the recall — manufacturers, repackagers, distributors, retailers and
  compounding pharmacies, undifferentiated.
- That anything was **migrated** here from another system. It reads a public feed
  and shares no data with anything.
- That a number is **measured** when it was estimated, or that a test **passed**
  when it was not run.

**Every numeric claim about live data carries the date of the snapshot it
describes, or it is not made.** The feeds move. F12 exists because this rule was
learned by breaking it.

---

## Public voice standard

Applies to the README, `docs/`, commit messages, and code comments.

- Numbers over adjectives. If there is a measurement, use it. If there is not, say
  there is not.
- No marketing vocabulary. Specifically banned: robust, seamless, leverage,
  cutting-edge, best-in-class, excited to share, game-changing, powerful.
- Plain declarative sentences. Minimal em dashes. No triadic flourishes.
- Say what failed. A write-up of something that worked first time is one line. One
  that failed and was fixed is a paragraph, and it is the better material.
- **A clean pass is the weaker finding.** When a rail holds, say what that does and
  does not demonstrate. F11 is the model for this.

---

## Working agreement

- **Nothing lands on `main` locally.** Branch, then PR. A `pre-commit` hook
  enforces it. If work is already stranded on `main`, check
  `git log origin/main..main` *before* touching anything — the repo has held
  stranded commits and uncommitted edits at the same time, and `git switch -c` is
  what handles that without losing either.
- `pre-commit` and `pre-push` scan for credentials, absolute local paths, and
  `internal/`. Project-specific patterns go in `.private-patterns`, which is
  gitignored and **never committed** — a guard that names the thing it hides
  publishes it.
- Anything new gets a review pass for secrets *and rendering* before it goes
  public. Broken tables and wrong claims are not caught by the hooks.
- Findings keep their original numbers. The sections are thematic, so the numbers
  do not run in sequence. A finding recorded after the build is dated in place, not
  folded in silently.
- Screenshots are evidence. A file named for a test it does not show is a defect of
  the same kind this repository documents.
