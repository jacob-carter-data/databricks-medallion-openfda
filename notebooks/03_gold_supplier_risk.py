# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Gold: supplier risk
# MAGIC
# MAGIC One row per firm, joining drug shortages to recall enforcement so a single
# MAGIC question can be asked of both: **which suppliers carry the most exposure
# MAGIC right now?**
# MAGIC
# MAGIC ## The join is partial, and that is published, not hidden
# MAGIC
# MAGIC Measured in Silver's `firm_xref`: of 132 normalized shortage firms, **83
# MAGIC (62.9%)** also appear in enforcement. An **inner join would silently discard
# MAGIC 49 firms** and understate total exposure, so this uses a **FULL OUTER join**
# MAGIC with an explicit match flag, and surfaces the join rate as a data-quality
# MAGIC measure on the dashboard.
# MAGIC
# MAGIC A firm present in only one feed is **unmatched**, which is not the same as
# MAGIC *failed to match*. A company with a current shortage may genuinely have no
# MAGIC recall record. Telling true absence apart from a normalization miss needs
# MAGIC manual review nobody has done, so the table states what was observed.
# MAGIC
# MAGIC ## How recalls are attributed to firms
# MAGIC
# MAGIC Counts come from **`recalling_firm` only** — the authoritative field,
# MAGIC populated on all 17,860 records. The `openfda.manufacturer_name` enrichment
# MAGIC that lifted matching from 74 to 83 is surfaced as a separate boolean
# MAGIC (`appears_as_openfda_manufacturer`), **not folded into the counts**.
# MAGIC
# MAGIC Attributing a recall to both its recalling firm and its manufacturer would
# MAGIC let one recall count twice across different firms, making the per-firm
# MAGIC numbers sum to more than the real total. Keeping attribution single-sourced
# MAGIC means `SUM(recalls_total)` reconciles exactly to Silver — asserted below as
# MAGIC invariant **G2**.
# MAGIC
# MAGIC **Grain: firms with at least one attributed shortage or recall.** A
# MAGIC consequence of the above is that a firm named *only* inside another firm's
# MAGIC `openfda.manufacturer_name` has nothing attributed to it — on the first run
# MAGIC that was **176 of 1,647 rows (10.7%) that were entirely zeros.** Those are
# MAGIC excluded, and the count is published in `gold.pipeline_quality`.
# MAGIC
# MAGIC **That exclusion is a known blind spot, not a cleanup.** Those firms are
# MAGIC contract manufacturers whose products were recalled by somebody else, which
# MAGIC is real exposure this pipeline does not measure. Counting it honestly needs
# MAGIC a deliberate attribution model — does a recall belong to the recaller, the
# MAGIC manufacturer, or both — carried consistently through every downstream
# MAGIC number. That is a design decision, not a flag to flip.
# MAGIC
# MAGIC ## What the population actually is
# MAGIC
# MAGIC "Supplier" overstates it. Enforcement records name **whoever initiated the
# MAGIC recall**, which includes manufacturers, repackagers, distributors, retailers
# MAGIC and compounding pharmacies. Verified in the first run's top 20: *Family
# MAGIC Dollar Stores* (a discount retailer) and *Tri-Coast Pharmacy* (a compounding
# MAGIC pharmacy) both rank high. Their records are legitimate — `product_type` is
# MAGIC `Drugs` for both — but neither is a supplier in a vendor-management sense.
# MAGIC **The table does not distinguish entity types, and nothing here infers one.**
# MAGIC
# MAGIC ## activity_score is constructed, and severity is deliberately NOT in it
# MAGIC
# MAGIC It was originally called `risk_score`. The first run disproved that name:
# MAGIC
# MAGIC | Firm | Active Class I | Active recalls | Score |
# MAGIC |---|---|---|---|
# MAGIC | Kilitch Healthcare India | **22** | 22 | 110 |
# MAGIC | Family Dollar Stores | 0 | 117 (all Class II) | **234** |
# MAGIC
# MAGIC Class I means a reasonable probability of death or serious harm; Class II
# MAGIC means temporary and reversible. A weighted sum ranked the retailer twice as
# MAGIC "risky" as a manufacturer with 22 potential-death recalls, because 117 × 2
# MAGIC beats 22 × 5. **No linear weighting fixes this** — any ratio loses to a large
# MAGIC enough count.
# MAGIC
# MAGIC So the composite was renamed to what it measures — **volume of activity** —
# MAGIC and severity moved into its own columns, `has_active_class_i` and
# MAGIC `max_active_classification`. Dashboards and Genie sort on severity first,
# MAGIC volume second. One number was the wrong shape for two questions.
# MAGIC
# MAGIC Two further limitations:
# MAGIC
# MAGIC 1. **The weights are a judgment call**, informed by the FDA's own class
# MAGIC    definitions but not derived from data. They are constants at the top of
# MAGIC    this notebook, and invariant **G4** asserts the published score actually
# MAGIC    recomputes from the published columns — so the formula and the number can
# MAGIC    never drift apart. Note the score also ignores `Not Yet Classified`
# MAGIC    recalls (one record today), which contribute nothing; those surface via
# MAGIC    `max_active_classification` instead of vanishing.
# MAGIC 2. **It is not normalized for firm size.** A large manufacturer has more
# MAGIC    products and therefore more opportunities to appear. Without a reliable
# MAGIC    product-count denominator — `openfda.product_ndc` is populated on only
# MAGIC    18% of enforcement rows, far too sparse to serve — the score partly ranks
# MAGIC    by size. Read it as "where is there the most activity," not "who is
# MAGIC    worst." Fixing this properly needs a product catalogue the pipeline does
# MAGIC    not have.
# MAGIC
# MAGIC ## Source disclaimer
# MAGIC
# MAGIC Public openFDA data. Not for clinical use. A recall record is not a
# MAGIC judgment about a company, and absence of a record is not evidence of safety.

# COMMAND ----------

CATALOG = "workspace"
SILVER_SCHEMA = "silver"
GOLD_SCHEMA = "gold"

# --- Activity score weights --------------------------------------------------
# JUDGMENT CALLS, stated as constants so they are arguable rather than buried.
# Grounded in the FDA's class definitions:
#   Class I   - reasonable probability of serious adverse health consequences
#               or death.
#   Class II  - temporary or medically reversible consequences.
#   Class III - unlikely to cause adverse health consequences.
# The ordering (I >> II > III) follows directly from those definitions. The exact
# multipliers do not; they are a defensible guess and nothing more.
W_RECALL_CLASS_I = 5
W_RECALL_CLASS_II = 2
W_RECALL_CLASS_III = 1
# An active shortage sits between Class I and Class II: a drug being unavailable
# is a real supply consequence, but not the acute-harm signal a Class I carries.
W_ACTIVE_SHORTAGE = 3

ACTIVITY_FORMULA = (
    f"{W_RECALL_CLASS_I}*recalls_class_i_active + "
    f"{W_RECALL_CLASS_II}*recalls_class_ii_active + "
    f"{W_RECALL_CLASS_III}*recalls_class_iii_active + "
    f"{W_ACTIVE_SHORTAGE}*shortages_active_strict"
)
print(f"activity_score = {ACTIVITY_FORMULA}")

# COMMAND ----------

from datetime import datetime, timezone

from pyspark.sql import functions as F

gold_built_at = datetime.now(timezone.utc)

shortages = spark.table(f"{CATALOG}.{SILVER_SCHEMA}.fda_shortages")
enforcement = spark.table(f"{CATALOG}.{SILVER_SCHEMA}.fda_enforcement")
xref = spark.table(f"{CATALOG}.{SILVER_SCHEMA}.firm_xref")

silver_shortage_rows = shortages.count()
silver_enforcement_rows = enforcement.count()
print(f"silver in: shortages={silver_shortage_rows:,} enforcement={silver_enforcement_rows:,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Per-firm aggregates
# MAGIC
# MAGIC `sample_name` carries a human-readable firm name through to Gold. `firm_key`
# MAGIC is normalized for joining and reads badly (`AUROBINDO`, not
# MAGIC `Aurobindo Pharma USA`) — a dashboard or Genie answer showing only the key
# MAGIC would look broken to a business reader. `min()` just picks deterministically
# MAGIC among the raw variants that normalized to the same key.

# COMMAND ----------

sh_agg = shortages.groupBy("firm_key").agg(
    F.min("company_name").alias("sample_shortage_name"),
    F.count("*").alias("shortages_total"),
    F.sum(F.col("is_active_strict").cast("int")).alias("shortages_active_strict"),
    F.sum(F.col("is_active_broad").cast("int")).alias("shortages_active_broad"),
    F.max("update_date").alias("latest_shortage_update"),
)

en_agg = enforcement.groupBy("firm_key").agg(
    F.min("recalling_firm").alias("sample_recall_name"),
    F.count("*").alias("recalls_total"),
    F.sum(F.col("is_active_strict").cast("int")).alias("recalls_active_strict"),
    F.sum(F.col("is_active_broad").cast("int")).alias("recalls_active_broad"),
    # Class counts, all recalls.
    F.sum((F.col("classification") == "Class I").cast("int")).alias("recalls_class_i"),
    F.sum((F.col("classification") == "Class II").cast("int")).alias("recalls_class_ii"),
    F.sum((F.col("classification") == "Class III").cast("int")).alias("recalls_class_iii"),
    # Class counts restricted to still-active recalls. These drive the score:
    # a terminated Class I from 2019 is history, not current exposure.
    F.sum(((F.col("classification") == "Class I") & F.col("is_active_strict")).cast("int"))
        .alias("recalls_class_i_active"),
    F.sum(((F.col("classification") == "Class II") & F.col("is_active_strict")).cast("int"))
        .alias("recalls_class_ii_active"),
    F.sum(((F.col("classification") == "Class III") & F.col("is_active_strict")).cast("int"))
        .alias("recalls_class_iii_active"),
    F.max("report_date").alias("latest_recall_report"),
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## The join
# MAGIC
# MAGIC Driven from `firm_xref`, which already holds every firm key observed in
# MAGIC either feed. Joining onto it with two LEFT joins is a full outer join over
# MAGIC the two aggregates by construction, and it keeps the match bookkeeping in
# MAGIC one place rather than re-deriving it here.

# COMMAND ----------

COUNT_COLS = [
    "shortages_total", "shortages_active_strict", "shortages_active_broad",
    "recalls_total", "recalls_active_strict", "recalls_active_broad",
    "recalls_class_i", "recalls_class_ii", "recalls_class_iii",
    "recalls_class_i_active", "recalls_class_ii_active", "recalls_class_iii_active",
]

gold = (
    xref.select(
        "firm_key",
        F.col("in_shortages").alias("has_shortages"),
        F.col("in_recalling_firm").alias("has_recalls"),
        F.col("in_openfda_mfr").alias("appears_as_openfda_manufacturer"),
        "matched_both_feeds",
        "match_pass",
    )
    .join(sh_agg, on="firm_key", how="left")
    .join(en_agg, on="firm_key", how="left")
)

# A firm with no recalls has ZERO recalls, not an unknown number of them -- the
# absence is informative here, unlike a missing measurement. Filling counts with 0
# is therefore correct; filling a *date* would be inventing one, so dates stay NULL.
gold = gold.fillna(0, subset=COUNT_COLS)

gold = (
    gold
    .withColumn("display_name",
                F.coalesce("sample_shortage_name", "sample_recall_name", "firm_key"))
    .withColumn("latest_event_date",
                F.greatest(F.col("latest_shortage_update"), F.col("latest_recall_report")))
    .withColumn(
        "activity_score",
        W_RECALL_CLASS_I * F.col("recalls_class_i_active")
        + W_RECALL_CLASS_II * F.col("recalls_class_ii_active")
        + W_RECALL_CLASS_III * F.col("recalls_class_iii_active")
        + W_ACTIVE_SHORTAGE * F.col("shortages_active_strict"),
    )
    # --- Severity, kept SEPARATE from the composite --------------------------
    # The composite is a weighted sum, so a large count of mild recalls outranks
    # a small count of lethal ones. Measured on the first run: Kilitch Healthcare
    # with 22 active Class I recalls (reasonable probability of death) scored 110,
    # while a discount retailer with 117 active Class II recalls (temporary,
    # reversible) scored 234. No linear weighting fixes that -- any ratio loses to
    # a big enough count -- so severity gets its own columns and the dashboard
    # sorts on those first.
    .withColumn("has_active_class_i", F.col("recalls_class_i_active") > 0)
    .withColumn(
        "max_active_classification",
        F.when(F.col("recalls_class_i_active") > 0, F.lit("Class I"))
         .when(F.col("recalls_class_ii_active") > 0, F.lit("Class II"))
         .when(F.col("recalls_class_iii_active") > 0, F.lit("Class III"))
         # 'Not Yet Classified' exists on exactly one enforcement record. It is
         # not in any class count and contributes nothing to the composite, so it
         # would otherwise vanish. Surface it rather than let it read as "none".
         .when(F.col("recalls_active_strict") > 0, F.lit("Unclassified"))
         .otherwise(F.lit(None).cast("string")),
    )
    .withColumn("_gold_built_at", F.lit(gold_built_at).cast("timestamp"))
)

# --- Grain: firms with ATTRIBUTED exposure -----------------------------------
# firm_xref spans three sources, one of which does not attribute counts. A firm
# named only inside another firm's openfda.manufacturer_name has no shortages and
# no recalls of its own, so it lands here as an all-zero row: every count 0, risk
# score 0. Measured on the first run, 176 of 1,647 rows (10.7%) were exactly that.
#
# They are excluded from the published grain. Not because they are uninteresting
# -- see the blind spot below -- but because a table where one row in ten is
# empty teaches its readers to ignore rows, and Genie answering "how many
# suppliers are we tracking?" with 1,647 would be wrong by 176.
#
# KNOWN BLIND SPOT, stated rather than buried: those firms are contract
# manufacturers whose products were recalled by somebody else. That is real
# exposure and this pipeline does not measure it. Attributing those recalls to
# them would double-count against the recalling firm and break the reconciliation
# in G2. Measuring it honestly needs a deliberate attribution model -- decide
# whether a recall belongs to the recaller, the manufacturer, or both, and carry
# that decision through every downstream number. That is real work, not a flag.
# The count is published in pipeline_quality so the gap stays visible.
gold_all = gold
manufacturer_only = gold_all.filter(
    ~F.col("has_shortages") & ~F.col("has_recalls")
).count()
gold = gold_all.filter(F.col("has_shortages") | F.col("has_recalls"))

gold.write.format("delta").mode("overwrite").option("overwriteSchema", "true") \
    .saveAsTable(f"{CATALOG}.{GOLD_SCHEMA}.supplier_risk")
print(f"gold.supplier_risk written: {gold.count():,} firms "
      f"({manufacturer_only:,} manufacturer-only rows excluded)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pipeline quality table
# MAGIC
# MAGIC The join rate and source freshness are **first-class published numbers**,
# MAGIC not footnotes. A control view that cannot say how complete it is invites the
# MAGIC reader to assume it is complete.

# COMMAND ----------

g = spark.table(f"{CATALOG}.{GOLD_SCHEMA}.supplier_risk")
shortage_firms = g.filter("has_shortages").count()
matched = g.filter("matched_both_feeds").count()

freshness = {
    r["_source"]: r["api_last_updated"]
    for r in shortages.select("_source", F.col("_api_last_updated").alias("api_last_updated"))
        .union(enforcement.select("_source", F.col("_api_last_updated").alias("api_last_updated")))
        .distinct().collect()
}

# --- Entity-resolution quality, measured not asserted ------------------------
# Normalization fails in BOTH directions, and the output shows both:
#
#   UNDER-MERGING: "Hospira Inc." -> HOSPIRA, but "Hospira, Inc., a Pfizer
#   Company" -> HOSPIRA A PFIZER. One company, two rows, its exposure split
#   across them. Same for ACTAVIS vs ACTAVIS ELIZABETH / FL / MID ATLANTIC,
#   CARDINAL vs CARDINAL HEALTH 200, AUROBINDO vs AUROBINDO UNIT I.
#
#   OVER-MERGING RISK: stripping suffixes can eat everything distinctive,
#   leaving generic keys like ADVANCED or COMPOUNDING that could collapse
#   unrelated firms. And a shared first token does not imply a shared company --
#   ABBOTT and ABBOTT S COMPOUNDING PHARMACY are almost certainly unrelated.
#
# The count below is an UPPER BOUND on under-merging, not a defect count: it
# includes coincidental prefix collisions. It is published rather than fixed,
# because without labelled ground truth "improving" the matcher means changing a
# number nobody can verify -- the same reason this project's RAG evals are pinned
# to a hand-labelled corpus. Building that ground truth is the honest next step.
_k = g.select("firm_key").distinct()
_a, _b = _k.alias("a"), _k.alias("b")
split_candidates = (
    _a.join(
        _b,
        (F.col("b.firm_key") != F.col("a.firm_key"))
        & F.col("b.firm_key").startswith(F.concat(F.col("a.firm_key"), F.lit(" "))),
    )
    .select(F.col("b.firm_key"))
    .distinct()
    .count()
)

quality_rows = [
    ("firms_total", float(g.count()), None, "Distinct normalized firms across both feeds."),
    ("firms_with_shortages", float(shortage_firms), None, "Firms appearing in the shortages feed."),
    ("firms_with_recalls", float(g.filter("has_recalls").count()), None, "Firms appearing in enforcement via recalling_firm."),
    ("firms_matched_both", float(matched), None, "Shortage firms also found in enforcement, by either route."),
    ("join_rate_pct", round(100.0 * matched / shortage_firms, 2) if shortage_firms else 0.0, None,
     "Share of shortage firms matched into enforcement. Unmatched is not the same as failed to match."),
    ("silver_shortage_rows", float(silver_shortage_rows), None, "Row count of silver.fda_shortages."),
    ("silver_enforcement_rows", float(silver_enforcement_rows), None, "Row count of silver.fda_enforcement."),
    ("firm_key_split_candidates", float(split_candidates), None,
     "UPPER BOUND on firms split across multiple keys by imperfect name "
     "normalization (a key that extends another existing key, e.g. HOSPIRA vs "
     "HOSPIRA A PFIZER). Includes coincidental prefix collisions, so it "
     "overstates the true defect count. Published, not fixed: without labelled "
     "ground truth, tuning the matcher only changes an unverifiable number. "
     "Under-merging depresses the cross-feed join rate reported above."),
    ("firms_manufacturer_only_excluded", float(manufacturer_only), None,
     "Firms appearing ONLY in openfda.manufacturer_name, never as a recalling firm "
     "or in shortages. Excluded from supplier_risk because no exposure can be "
     "attributed to them without double-counting. A known blind spot: these are "
     "contract manufacturers whose products were recalled by someone else."),
] + [
    # Freshness is a date the source reports, not a number, so it goes in
    # value_text. It belongs in the table rather than only in stdout: a dashboard
    # freshness tile has to query it, and "how current is this?" is the first
    # question anyone sensible asks of a risk view.
    (f"source_last_updated__{src}", None, str(ts),
     f"api_last_updated reported by openFDA for {src}.")
    for src, ts in sorted(freshness.items())
]

quality = spark.createDataFrame(
    [(k, v, vt, d, gold_built_at) for k, v, vt, d in quality_rows],
    schema="metric string, value double, value_text string, description string, built_at timestamp",
)
quality.write.format("delta").mode("overwrite").option("overwriteSchema", "true") \
    .saveAsTable(f"{CATALOG}.{GOLD_SCHEMA}.pipeline_quality")

print("source freshness (api_last_updated):")
for k, v in freshness.items():
    print(f"  {k:20} {v}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scorecard

# COMMAND ----------

W = 78
print("=" * W)
print("GOLD SCORECARD")
print("=" * W)
print(f"  firms (union of both feeds) ...... {g.count():>7,}")
print(f"  ...with shortages ................ {shortage_firms:>7,}")
print(f"  ...with recalls .................. {g.filter('has_recalls').count():>7,}")
matched_recalling = g.filter("has_shortages AND has_recalls").count()
matched_mfr_only = matched - matched_recalling
print(f"  ...matched in both ............... {matched:>7,}   {100.0*matched/shortage_firms:.1f}% of shortage firms")
print(f"       via recalling_firm .......... {matched_recalling:>7,}   [counts as an overlap in the union above]")
print(f"       via openfda manufacturer .... {matched_mfr_only:>7,}   [matched, but no recalls ATTRIBUTED, so not an overlap]")
print(f"  union check ...................... {shortage_firms + g.filter('has_recalls').count() - matched_recalling:>7,}   [shortage + recall - recalling_firm overlap]")
print(f"  ...shortage-only (kept, not dropped) {shortage_firms - matched:>5,}   [an inner join would have lost these]")
print()
print("  Reconciliation to silver:")
tot_sh = g.agg(F.sum("shortages_total")).collect()[0][0]
tot_en = g.agg(F.sum("recalls_total")).collect()[0][0]
print(f"    sum(shortages_total) ........... {tot_sh:>7,}  vs silver {silver_shortage_rows:,}")
print(f"    sum(recalls_total) ............. {tot_en:>7,}  vs silver {silver_enforcement_rows:,}")
print()
print(f"  entity-resolution split candidates  {split_candidates:>5,}   [upper bound; see pipeline_quality]")
print()
print(f"  activity_score = {ACTIVITY_FORMULA}")
print("=" * W)
top = (
    g.select("display_name", "max_active_classification", "shortages_active_strict",
             "recalls_class_i_active", "recalls_active_strict", "activity_score",
             "matched_both_feeds")
     .orderBy(F.desc("has_active_class_i"), F.desc("recalls_class_i_active"),
              F.desc("activity_score"))
     .limit(20)
)

# Text form as well as the interactive grid: display() renders a widget that
# cannot be copied into a runbook, a commit message, or a chat.
print("  RANKED BY SEVERITY FIRST (max active class), THEN activity volume.")
print(f"  {'firm':<34} {'worst':>9} {'shrt':>5} {'ClsI':>5} {'rcl':>5} {'activity':>9}")
print("  " + "-" * 74)
for r in top.collect():
    name = (r["display_name"] or "")[:33]
    print(f"  {name:<34} {(r['max_active_classification'] or '-'):>9} "
          f"{r['shortages_active_strict']:>5} {r['recalls_class_i_active']:>5} "
          f"{r['recalls_active_strict']:>5} {r['activity_score']:>9}")
print("=" * W)

display(top)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Invariants
# MAGIC
# MAGIC **G4 is the one that earns the phrase "transparent formula."** It recomputes
# MAGIC the score from the published component columns and asserts it matches the
# MAGIC stored value. Without it, "the formula is in the table comment" is a promise
# MAGIC rather than a fact, and a later weight change could leave the documented
# MAGIC formula and the published number silently disagreeing.

# COMMAND ----------

failures = []

# G1/G2 - every silver row is attributed to exactly one firm. If these drift, a
# join is duplicating or dropping records, which is the classic way an aggregate
# ends up confidently wrong.
if tot_sh != silver_shortage_rows:
    failures.append(f"G1: sum(shortages_total)={tot_sh:,} != silver rows {silver_shortage_rows:,}")
if tot_en != silver_enforcement_rows:
    failures.append(f"G2: sum(recalls_total)={tot_en:,} != silver rows {silver_enforcement_rows:,}")

# G3 - one row per firm.
dupes = g.count() - g.select("firm_key").distinct().count()
if dupes:
    failures.append(f"G3: {dupes} duplicate firm_key row(s) in gold.supplier_risk")

# G4 - the published score recomputes from the published columns.
mismatched = g.filter(
    F.col("activity_score") != (
        W_RECALL_CLASS_I * F.col("recalls_class_i_active")
        + W_RECALL_CLASS_II * F.col("recalls_class_ii_active")
        + W_RECALL_CLASS_III * F.col("recalls_class_iii_active")
        + W_ACTIVE_SHORTAGE * F.col("shortages_active_strict")
    )
).count()
if mismatched:
    failures.append(
        f"G4: {mismatched} row(s) where activity_score does not equal the documented "
        "formula applied to the published columns"
    )

# G5 - every firm in the silver record tables reached gold.
# The earlier version of this check compared gold against firm_xref, which gold
# is BUILT from -- it could never fail and so protected nothing. This compares
# against the silver record tables instead, which catches the real risk: a stale
# firm_xref, left behind when someone re-runs the silver record tables without
# rebuilding the crosswalk. That would silently drop firms from gold.
gold_keys = g.select("firm_key")
missing_sh = shortages.select("firm_key").distinct().subtract(gold_keys).count()
missing_en = enforcement.select("firm_key").distinct().subtract(gold_keys).count()
if missing_sh or missing_en:
    failures.append(
        f"G5: {missing_sh} shortage firm(s) and {missing_en} enforcement firm(s) "
        "present in silver are absent from gold. firm_xref is likely stale -- "
        "re-run the silver notebook end to end."
    )

# G6 - counts are never NULL. A firm with no recalls has zero, and zero is a
# number we can defend; NULL here would be an unknown we cannot.
null_counts = g.filter(
    " OR ".join(f"{c} IS NULL" for c in COUNT_COLS)
).count()
if null_counts:
    failures.append(f"G6: {null_counts} row(s) carry NULL in a count column")

print("=" * W)
if failures:
    print("  VERDICT: FAILURES PRESENT — gold is not trustworthy.")
    for f_ in failures:
        print(f"    !! {f_}")
    print("=" * W)
    raise AssertionError(f"{len(failures)} Gold invariant(s) failed; see above.")
print("  VERDICT: all invariants hold. Every silver row is attributed to exactly")
print("           one firm, one row per firm, no firm dropped by the join, and")
print("           activity_score recomputes from its published components.")
print("=" * W)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Document in Unity Catalog
# MAGIC
# MAGIC Genie reads this metadata when answering. The metric-definition comments do
# MAGIC the heaviest lifting: they are where "active" stops being ambiguous and
# MAGIC where the score's limits travel with the number instead of living in a
# MAGIC README nobody opens.

# COMMAND ----------

GOLD_TABLE_COMMENT = (
    "Gold: one row per supplier firm, joining FDA drug shortages to recall "
    "enforcement. FULL OUTER join -- firms present in only one feed are RETAINED "
    "with zero counts, because an inner join would drop roughly 37 percent of "
    "shortage firms and understate exposure. Recall counts are attributed by "
    "recalling_firm only, so per-firm totals reconcile exactly to the silver "
    "layer. GRAIN: firms with at least one attributed shortage or recall. Firms "
    "appearing only inside another firms openfda.manufacturer_name are excluded, "
    "since no exposure can be attributed to them without double-counting; their "
    "count is published in gold.pipeline_quality as a known blind spot. "
    "activity_score measures VOLUME of activity, not severity: a firm with many "
    "Class II recalls outscores one with fewer Class I recalls, so rank by "
    "has_active_class_i or max_active_classification when the question is about "
    "severity. It is not normalized for firm size either, so larger firms score "
    "higher partly because they are larger. POPULATION NOTE: enforcement records "
    "name whoever initiated the recall, so this table mixes manufacturers, "
    "repackagers, distributors, retailers and compounding pharmacies, and does "
    "not distinguish between them. Public FDA data, not for clinical use, and "
    "nothing here is an FDA judgment about any company."
)

GOLD_COLS = {
    "firm_key": (
        "Normalized firm name used as the join key. NOT a legal entity "
        "identifier and NOT authoritative entity resolution. Fails in both "
        "directions: the same company can split across keys (HOSPIRA vs HOSPIRA "
        "A PFIZER), and aggressive suffix-stripping can leave generic keys that "
        "risk merging unrelated firms. See firm_key_split_candidates in "
        "gold.pipeline_quality for the measured upper bound."
    ),
    "display_name": "Human-readable firm name carried from the source feeds, for dashboards and Genie.",
    "has_shortages": "Firm appears in the drug shortages feed.",
    "has_recalls": "Firm appears in enforcement as recalling_firm.",
    "appears_as_openfda_manufacturer": "Firm appears in enforcement openfda.manufacturer_name. Used to establish a match; deliberately NOT used to attribute recall counts, which would let one recall count twice across firms.",
    "matched_both_feeds": "Present in shortages AND in enforcement by either route.",
    "match_pass": "How the firm matched: 1 = normalized recalling_firm, 2 = openfda manufacturer enrichment. NULL if unmatched or absent from shortages.",
    "shortages_total": "All shortage records for this firm, any status.",
    "shortages_active_strict": "HEADLINE. Shortage records with status Current: in effect now.",
    "shortages_active_broad": "Shortage records with status Current or To Be Discontinued: any open exposure including prospective discontinuation. Wider than the headline; never mix the two in one figure.",
    "recalls_total": "All recall records for this firm, any status or class.",
    "recalls_active_strict": "HEADLINE. Recalls with status Ongoing: still in progress.",
    "recalls_active_broad": "Recalls not yet Terminated, including Completed. Wider than the headline.",
    "recalls_class_i": "All Class I recalls. FDA Class I means a reasonable probability of serious adverse health consequences or death.",
    "recalls_class_ii": "All Class II recalls: temporary or medically reversible consequences.",
    "recalls_class_iii": "All Class III recalls: unlikely to cause adverse health consequences.",
    "recalls_class_i_active": "Class I recalls still Ongoing. Feeds activity_score.",
    "recalls_class_ii_active": "Class II recalls still Ongoing. Feeds activity_score.",
    "recalls_class_iii_active": "Class III recalls still Ongoing. Feeds activity_score.",
    "latest_shortage_update": "Most recent shortage update_date for this firm. NULL when the firm has no shortage records.",
    "latest_recall_report": "Most recent recall report_date for this firm. NULL when the firm has no recall records.",
    "latest_event_date": "Most recent activity of either kind.",
    "activity_score": (
        f"CONSTRUCTED volume metric: {ACTIVITY_FORMULA}. Measures HOW MUCH activity "
        "a firm has, NOT how severe it is -- a firm with many Class II recalls "
        "outscores one with fewer Class I recalls. Sort by has_active_class_i or "
        "max_active_classification FIRST if the question is about severity. "
        "Weights are a judgment call informed by FDA class definitions, not "
        "derived from data. Not normalized for firm size, so larger firms score "
        "higher partly because they are larger. Ignores Not Yet Classified "
        "recalls. Valid for ranking within this dataset only, and never an FDA "
        "judgment about a company."
    ),
    "has_active_class_i": (
        "TRUE when the firm has at least one Ongoing Class I recall -- FDA's most "
        "serious category, meaning a reasonable probability of serious adverse "
        "health consequences or death. Use this, not activity_score, to rank by "
        "severity."
    ),
    "max_active_classification": (
        "Most severe classification among this firm's Ongoing recalls: Class I, "
        "Class II, Class III, or Unclassified. NULL when the firm has no ongoing "
        "recalls. Unclassified covers the FDA's 'Not Yet Classified' state, which "
        "no class count captures."
    ),
    "_gold_built_at": "UTC timestamp when this table was built.",
}

target = f"{CATALOG}.{GOLD_SCHEMA}.supplier_risk"
_gold_comment_sql = GOLD_TABLE_COMMENT.replace("'", "''")
spark.sql(f"COMMENT ON TABLE {target} IS '{_gold_comment_sql}'")
present = set(spark.table(target).columns)
for col, desc in GOLD_COLS.items():
    if col in present:
        safe = desc.replace("'", "''")
        spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} COMMENT '{safe}'")

qt = f"{CATALOG}.{GOLD_SCHEMA}.pipeline_quality"
spark.sql(
    f"COMMENT ON TABLE {qt} IS 'Gold: pipeline data-quality measures, published "
    "alongside the data rather than as a footnote. Includes the cross-feed join "
    "rate, which is a finding and not a defect to hide.'"
)
for col, desc in {
    "metric": "Measure name.",
    "value": "Measure value.",
    "value_text": "Non-numeric measure value, e.g. a source freshness date.",
    "description": "What the measure means and how to read it.",
    "built_at": "UTC timestamp when the measure was computed.",
}.items():
    spark.sql(f"ALTER TABLE {qt} ALTER COLUMN {col} COMMENT '{desc}'")

print("Comments applied to gold.supplier_risk and gold.pipeline_quality.")
