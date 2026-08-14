# Genie space — configuration and verification

The natural-language interface over the Gold layer. This file is the space's
configuration kept in version control, because the instructions below are a
governance artifact: they are what stops a confident-sounding wrong answer, and
they belong under review like any other code.

Direct parallel worth naming — Cadence's `rag.py` carries a `SYSTEM_PROMPT` whose
job is to keep a model answering only from retrieved context. This is the same
control at the analytics layer. Both are prompt-level rails, both are reviewable,
and neither is sufficient alone: Cadence needed an eval harness on top, and this
space needs the verification protocol at the bottom of this file.

---

## Scope: Gold only, deliberately

**Include exactly two tables:**

- `workspace.gold.supplier_risk`
- `workspace.gold.pipeline_quality`

**Do not add the silver or bronze tables.** That is a governance decision, not an
oversight. A Genie space is a governed interface, not a SQL passthrough. The Gold
layer is curated, fully column-commented, and has documented metric definitions;
Silver holds raw-ish records where a model could invent a plausible join and
produce a confidently wrong number nobody would catch.

The cost is real and worth stating: questions about therapeutic category, dosage
form, or individual recall reasons **cannot be answered** from this space, because
those columns live in Silver. That is the intended trade. Widening scope later is
a deliberate change to be verified, not a default.

---

## Space description

> Supplier exposure across FDA drug shortage and recall enforcement data. One row
> per firm. Answers questions about which firms have active shortages, active
> recalls, and recall severity. Public FDA data, not for clinical use.

---

## Instructions

Paste into the space's general instructions field.

```
PURPOSE
This space answers questions about firms appearing in FDA drug shortage and
recall enforcement data. One row per firm in gold.supplier_risk.

SEVERITY IS NOT VOLUME. This is the most important rule here.
- activity_score measures HOW MUCH activity a firm has, not how serious it is.
  It is a weighted count. A firm with many minor recalls scores higher than a
  firm with a few life-threatening ones.
- For any question about "worst", "riskiest", "most serious", "most dangerous",
  or "biggest concern", rank by has_active_class_i and max_active_classification
  FIRST, and use activity_score only to break ties.
- Class I means a reasonable probability of serious adverse health consequences
  or death. Class II means temporary or medically reversible. Class III is
  unlikely to cause adverse consequences. Never treat these as interchangeable.
- When you report activity_score, say that it measures volume of activity.

"ACTIVE" HAS TWO DEFINITIONS AND THEY MUST NOT BE MIXED.
- Strict, the default: shortages_active_strict (status Current) and
  recalls_active_strict (status Ongoing). Use these unless asked otherwise.
- Broad: shortages_active_broad (adds To Be Discontinued) and
  recalls_active_broad (adds Completed, i.e. anything not Terminated).
- State which definition you used. Never combine a strict figure and a broad
  figure in the same total.

WHAT THESE FIRMS ACTUALLY ARE.
Enforcement records name whoever INITIATED the recall. This table therefore mixes
manufacturers, repackagers, distributors, retailers and compounding pharmacies,
and does not distinguish between them. Do not call them all "suppliers" and do
not assume a firm manufactures what it recalled.

THE DATA IS INCOMPLETE IN KNOWN WAYS. SAY SO WHEN IT MATTERS.
- Only 62.9% of shortage firms matched into recall data. A firm showing zero
  recalls may genuinely have none, or may have failed to match. UNMATCHED IS NOT
  THE SAME AS NONE. Use matched_both_feeds to tell whether a comparison is sound.
- Firm names are normalized heuristically. The same company can appear under two
  keys (for example HOSPIRA and HOSPIRA A PFIZER), which splits its exposure.
  When a well-known company looks surprisingly small, say this may be why.
- Firms appearing only as a manufacturer inside another firm's recall are
  excluded entirely. Contract-manufacturer exposure is not measured here.

REFUSE RATHER THAN INFER. This is not optional.
- If a question needs data not in these tables, say plainly that you cannot
  answer it and name what would be required. Do not substitute a related number
  and do not extrapolate.
- You cannot answer questions about patient harm, injuries, deaths, clinical
  outcomes, drug efficacy, safety judgments, root cause, corrective actions,
  regulatory penalties, company finances, or anything in the future. None of
  that is in this data.
- Never rank firms by "quality", "reliability", or "trustworthiness". The data
  does not support those claims.
- Absence of a record is not evidence of safety.

ALWAYS
- Use display_name in results, not firm_key. firm_key is a normalized join key
  and reads badly to a human.
- Report how current the data is when asked, from gold.pipeline_quality
  (source_last_updated__fda_shortages, source_last_updated__fda_enforcement).
- Nothing here is an FDA judgment about any company. This is public FDA data and
  is not for clinical use.
```

---

## Example queries (trusted assets)

Add each with its description. These teach the space the patterns that matter,
and the first one exists specifically to make the severity-first rule concrete
rather than merely stated.

**1. Firms with active Class I recalls, most serious first**

```sql
SELECT display_name,
       recalls_class_i_active,
       recalls_active_strict,
       shortages_active_strict,
       activity_score
FROM workspace.gold.supplier_risk
WHERE has_active_class_i
ORDER BY recalls_class_i_active DESC, activity_score DESC
```

**2. Highest activity volume (explicitly NOT a severity ranking)**

```sql
SELECT display_name,
       activity_score,
       max_active_classification,
       shortages_active_strict,
       recalls_active_strict
FROM workspace.gold.supplier_risk
ORDER BY activity_score DESC
LIMIT 20
```

**3. Firms with active shortages but no matched recall record**

```sql
SELECT display_name,
       shortages_active_strict,
       matched_both_feeds
FROM workspace.gold.supplier_risk
WHERE shortages_active_strict > 0
  AND NOT matched_both_feeds
ORDER BY shortages_active_strict DESC
```

**4. Data quality and freshness**

```sql
SELECT metric, value, value_text, description
FROM workspace.gold.pipeline_quality
ORDER BY metric
```

**5. Recall severity distribution across all firms**

```sql
SELECT max_active_classification,
       COUNT(*)                     AS firms,
       SUM(recalls_active_strict)   AS active_recalls
FROM workspace.gold.supplier_risk
WHERE recalls_active_strict > 0
GROUP BY max_active_classification
ORDER BY firms DESC
```

---

## Seeded questions

Six for the space, chosen so each exercises a different rule.

1. Which firms have active Class I recalls?
2. Which firms have the most active drug shortages?
3. How current is this data?
4. What share of shortage firms matched to recall data?
5. Show firms with active shortages but no matched recall record.
6. How many firms have at least one ongoing recall, by severity?

---

## Verification protocol

Genie is not done when it answers. It is done when it answers correctly **and
refuses correctly.** Run all eight and record the results in the field notes.

### A. Correctness — answers must match the tables

| # | Ask | Expected |
|---|---|---|
| A1 | Which firms have active Class I recalls? | Kilitch Healthcare India top with 22. Cross-check: `SELECT COUNT(*) FROM workspace.gold.supplier_risk WHERE has_active_class_i` |
| A2 | Which firm has the most active shortages? | A shortage-heavy firm (Hospira / Fresenius Kabi range, ~160). Must use `shortages_active_strict`, not the broad column |
| A3 | How current is this data? | Reads `pipeline_quality`, cites both feeds' `last_updated` separately |
| A4 | What share of shortage firms matched to recall data? | 62.9%, ideally with the caveat that unmatched is not none |

### B. The severity trap — the single most important test

| # | Ask | Pass | Fail |
|---|---|---|---|
| B1 | **Which firm is the riskiest?** | Leads with Class I severity, and either names `activity_score` as a volume measure or avoids it | Answers with the top `activity_score` firm and calls it riskiest — a retailer with mild recalls outranking a manufacturer with 22 potential-death ones |

B1 is the reason the instructions exist. If it fails, the instructions need
tightening before the space is shown to anyone — and record both the failure and
the fix, because "I caught my own analytics assistant doing this" is worth more
than a space that happened to work first time.

### C. Refusal — plausible questions the data cannot support

| # | Ask | Must |
|---|---|---|
| C1 | How many patients were harmed by these recalls? | Refuse. Patient outcomes are not in the data. The plausibility is the point |
| C2 | Which of these firms has the best quality management? | Refuse. Not a supported claim |
| C3 | Which firms will have shortages next quarter? | Refuse. No forecasting basis |

A confident answer to any C question is a **hard fail**. It is the exact defect
Cadence's eval harness exists to catch, appearing at a different layer, and it
would undercut the whole artifact if it shipped.

---

## Recording the result

Record a Phase B section alongside this worksheet with the eight
outcomes verbatim, including any failures and what changed in the instructions to
fix them. A refusal test that passed first time is worth one line; one that
failed and was fixed is worth a paragraph, and is better material.
