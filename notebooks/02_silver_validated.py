# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Silver: validated, typed, conformed
# MAGIC
# MAGIC Bronze's header says deduplication is Silver's job. This closes that loop.
# MAGIC
# MAGIC ## What this layer is responsible for
# MAGIC
# MAGIC | Silver does | Silver does NOT |
# MAGIC |---|---|
# MAGIC | Type and parse (dates, booleans) | Aggregate or score |
# MAGIC | Deduplicate on `_record_hash` | Join the two feeds together (Gold) |
# MAGIC | Normalize firm names into `firm_key` | Decide what "risky" means |
# MAGIC | Assert invariants and fail loudly | Reshape for a dashboard |
# MAGIC
# MAGIC ## Two decisions to be able to defend cold
# MAGIC
# MAGIC **1. Silver is current state, not all history.** It reads the *latest
# MAGIC complete `_batch_id`* per source. Bronze keeps every observation ever taken;
# MAGIC Silver is the clean current view Gold consumes. With one Bronze batch these
# MAGIC are identical, which is exactly why the choice is written down now — it
# MAGIC becomes invisible and load-bearing the moment a second batch lands.
# MAGIC
# MAGIC **2. Dedupe on `_record_hash` only — never on business key.** Profiling all
# MAGIC 1,651 shortage records found 1,627 distinct
# MAGIC (generic_name, company_name, package_ndc, presentation) keys. The 24
# MAGIC collisions are **not** duplicates: each is the same product recorded at two
# MAGIC lifecycle stages, e.g. one row `status='To Be Discontinued'` posted 10/2025
# MAGIC and another `status='Current'` posted 01/2023. Collapsing them on business
# MAGIC key would silently destroy real history. Invariant **S4** below exists
# MAGIC specifically to stop a future refactor from "helpfully" doing that.
# MAGIC
# MAGIC ## Metric definitions live here, not in the dashboard
# MAGIC
# MAGIC Both grains are emitted, each documented in its column comment. The strict
# MAGIC one is the headline; the broad one is available so a consumer asking a
# MAGIC different question gets a real answer instead of a reinterpreted number.
# MAGIC
# MAGIC | Metric | Strict (headline) | Broad |
# MAGIC |---|---|---|
# MAGIC | Active shortage | `status='Current'` | + `To Be Discontinued` |
# MAGIC | Active recall | `status='Ongoing'` | anything not `Terminated` |
# MAGIC
# MAGIC ## Honesty rails
# MAGIC
# MAGIC - **NULL over guess.** An absent value is recorded as NULL, never defaulted
# MAGIC   to something plausible. A missing number is honest; a guessed number
# MAGIC   wearing a type is not.
# MAGIC - **But `'N/A'` is preserved, not nulled.** The source explicitly saying
# MAGIC   "not applicable" is information. Only empty string becomes NULL.
# MAGIC - **Unmatched is not "failed to match."** A firm with a shortage may
# MAGIC   genuinely have zero recalls. `firm_xref` reports unmatched; it never
# MAGIC   claims a normalization failure it has not demonstrated.
# MAGIC - **Public FDA data, not for clinical use.** Nothing here is an FDA
# MAGIC   judgment about any company.

# COMMAND ----------

CATALOG = "workspace"
BRONZE_SCHEMA = "bronze"
SILVER_SCHEMA = "silver"

# Two KINDS of check, and conflating them was a real bug in the first version of
# this notebook.
#
# STRUCTURAL invariants hold no matter how the data moves: row accounting must
# balance, dates must not parse silently to NULL, no source field may be dropped.
# These are hard failures.
#
# VOLUME expectations are properties of one snapshot. openFDA is a live feed, so
# counts change every week by design. The first version asserted absolute counts
# profiled from the RAW 1,651 records -- and then compared them against POST-dedupe
# Silver, which has 1,650. Every shortages count came out exactly one low and S6
# failed on a pipeline that was working perfectly. A weekly job built that way
# would fail every single run the moment the FDA published anything.
#
# So volume checks are now RATES with a tolerance band, and they warn rather than
# abort. Keeping an absolute count as a hard gate on a live feed is a way of
# guaranteeing the alarm gets ignored.
EXPECTED = {
    "fda_shortages": {
        # Rate, not count. Profiled 1,463/1,651 raw = 88.6%.
        "openfda_rate": 0.886,
        "status_domain": {"Current", "To Be Discontinued", "Resolved"},
        "update_type_domain": {"Reverified", "New", "Revised"},
    },
    "fda_enforcement": {
        # Profiled 3,231/17,860 = 18.1%.
        "openfda_rate": 0.181,
        "status_domain": {"Terminated", "Ongoing", "Completed"},
        "classification_domain": {"Class I", "Class II", "Class III", "Not Yet Classified"},
    },
}

# How far an observed rate may drift before it is worth a human look.
RATE_TOLERANCE_PP = 5.0

# COMMAND ----------

from datetime import datetime, timezone

from pyspark.sql import functions as F
from pyspark.sql import types as T

silver_built_at = datetime.now(timezone.utc)
print(f"silver_built_at = {silver_built_at.isoformat()}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Declared schemas
# MAGIC
# MAGIC **These field lists come from profiling every landed record, not from
# MAGIC sampling one.** That distinction is the single most important lesson of the
# MAGIC Bronze phase: a `?limit=1` probe returns **15** fields for the shortages
# MAGIC endpoint, but the full 1,651 records contain **19**, and only **10** appear
# MAGIC in every row. `discontinued_date`, `related_info_link`, `change_date`, and
# MAGIC `resolved_note` are absent from the first record entirely. A schema inferred
# MAGIC from a sample would have dropped four real fields with no error at all.
# MAGIC
# MAGIC Every date is declared `StringType` here on purpose. Parsing happens in a
# MAGIC separate, *checked* step — see invariant **S2**.
# MAGIC
# MAGIC `openfda` is deliberately excluded from these structs and handled separately
# MAGIC below. It is a nested object whose 18–21 sub-fields are all **arrays**;
# MAGIC exploding it would balloon the schema and make Genie measurably worse, since
# MAGIC it answers far better over flat scalars than over arrays.

# COMMAND ----------

SHORTAGE_FIELDS = [
    # Present in all 1,651 rows.
    "company_name", "contact_info", "generic_name", "initial_posting_date",
    "package_ndc", "presentation", "status", "update_date", "update_type",
    # Present in all rows, but an array.
    "therapeutic_category",
    # Optional. Percentages are measured, not guessed.
    "dosage_form",        # 99.0%
    "availability",       # 71.5%
    "related_info",       # 69.7%
    "discontinued_date",  # 27.0%
    "shortage_reason",    # 26.6%
    "related_info_link",  #  2.7%
    "change_date",        #  1.9%
    "resolved_note",      #  1.4%
]

ENFORCEMENT_FIELDS = [
    # Present in all 17,860 rows.
    "address_1", "address_2", "city", "classification", "code_info", "country",
    "distribution_pattern", "event_id", "initial_firm_notification",
    "postal_code", "product_description", "product_quantity", "product_type",
    "reason_for_recall", "recall_initiation_date", "recall_number",
    "recalling_firm", "report_date", "state", "status", "voluntary_mandated",
    # Optional. center_classification_date is missing from exactly ONE row of
    # 17,860 -- the kind of single-row anomaly that defeats any NOT NULL
    # assumption derived from spot-checking.
    "center_classification_date",  # 17,859/17,860
    "termination_date",            # 82.9%
    "more_code_info",              # 46.4%
]

# therapeutic_category is the one top-level array in either feed.
SHORTAGE_SCHEMA = T.StructType([
    T.StructField("therapeutic_category", T.ArrayType(T.StringType()), True)
    if f == "therapeutic_category"
    else T.StructField(f, T.StringType(), True)
    for f in SHORTAGE_FIELDS
])
ENFORCEMENT_SCHEMA = T.StructType([
    T.StructField(f, T.StringType(), True) for f in ENFORCEMENT_FIELDS
])

# openfda is handled outside the struct; declare it so the unknown-field guard
# below does not flag it as a surprise.
DECLARED = {
    "fda_shortages": set(SHORTAGE_FIELDS) | {"openfda"},
    "fda_enforcement": set(ENFORCEMENT_FIELDS) | {"openfda"},
}

# Date columns and their formats. THE TWO FEEDS DISAGREE, which is precisely the
# sort of thing Silver exists to reconcile: enforcement uses compact yyyyMMdd,
# shortages uses US-style MM/dd/yyyy.
DATE_FORMATS = {
    "fda_shortages": {
        "initial_posting_date": "MM/dd/yyyy",
        "update_date": "MM/dd/yyyy",
        "discontinued_date": "MM/dd/yyyy",
        "change_date": "MM/dd/yyyy",
    },
    "fda_enforcement": {
        "report_date": "yyyyMMdd",
        "recall_initiation_date": "yyyyMMdd",
        "center_classification_date": "yyyyMMdd",
        "termination_date": "yyyyMMdd",
    },
}

# COMMAND ----------

# MAGIC %md
# MAGIC ## Helpers

# COMMAND ----------

@F.udf(T.ArrayType(T.StringType()))
def json_top_level_keys(s):
    """Return the top-level key names of a JSON object string.

    A Python UDF is slower than native Spark functions, and at 19,511 rows that
    cost is irrelevant while the certainty is worth a lot: this feeds the
    unknown-field guard, and a guard built on a function whose edge-case
    behaviour I am unsure of protects nothing. At a hundred million rows this
    would be worth replacing with a native expression and re-verifying.
    """
    import json
    if not s:
        return []
    try:
        obj = json.loads(s)
    except ValueError:
        return ["<UNPARSEABLE>"]
    return sorted(obj.keys()) if isinstance(obj, dict) else ["<NOT_AN_OBJECT>"]


# Corporate and industry suffix tokens stripped during firm-name normalization.
# Applied AFTER punctuation removal, so "L.L.C." has already become "L L C".
FIRM_SUFFIX_PATTERN = (
    r"\b(INC|INCORPORATED|LLC|L L C|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|LP|"
    r"LLP|PLC|GMBH|AG|SA|NV|BV|AB|AS|PTY|PVT|PRIVATE|USA|US|AMERICA|HOLDINGS|"
    r"GROUP|LABORATORIES|LABS|PHARMACEUTICALS|PHARMACEUTICAL|PHARMA|HEALTHCARE|"
    r"PRODUCTS)\b"
)


def normalize_firm(col):
    """Normalize a company name into a join key.

    Upper-case, strip punctuation to spaces, collapse whitespace, then remove
    corporate and industry suffix tokens. Measured effect on the cross-feed join:
    31 raw exact matches becomes 74. That doubling is the argument for doing
    entity resolution at all, and it is why `firm_xref` records the progression
    rather than just the final number.

    If stripping suffixes would empty the string entirely -- a firm literally
    named "Pharma Group" -- fall back to the punctuation-stripped form rather
    than producing an empty key.
    """
    cleaned = F.trim(F.regexp_replace(
        F.regexp_replace(F.upper(col), r"[^A-Z0-9 ]", " "), r"\s+", " "
    ))
    core = F.trim(F.regexp_replace(
        F.regexp_replace(cleaned, FIRM_SUFFIX_PATTERN, " "), r"\s+", " "
    ))
    return F.when(F.length(core) > 0, core).otherwise(
        F.when(F.length(cleaned) > 0, cleaned).otherwise(F.lit(None))
    )


def blank_to_null(col):
    """Empty string becomes NULL; every other value is left exactly as-is.

    Deliberately does NOT touch 'N/A'. An empty string means the field was
    absent; 'N/A' means the source explicitly said "not applicable". Those are
    different facts and collapsing them would destroy information. Measured:
    voluntary_mandated has 12 empty strings and 23 literal 'N/A'.
    """
    return F.when(F.trim(col) == "", None).otherwise(col)


def latest_batch(source_name):
    """The most recent complete Bronze batch for one source, deduped on hash.

    Two steps, in this order:
      1. Filter to the newest _batch_id. Silver is current state; older batches
         stay in Bronze as history.
      2. Collapse exact duplicates on _record_hash.
    """
    bronze = spark.table(f"{CATALOG}.{BRONZE_SCHEMA}.{source_name}")
    # Resolve the newest batch by INGEST TIME, not by max(_batch_id). Batch ids
    # are UUID4 and do not sort chronologically -- ordering by them would pick an
    # arbitrary batch and quietly produce a stale Silver.
    newest = (
        bronze.groupBy("_batch_id")
        .agg(F.max("_ingested_at").alias("ts"))
        .orderBy(F.desc("ts"))
        .first()["_batch_id"]
    )
    scoped = bronze.filter(F.col("_batch_id") == newest)
    rows_in = scoped.count()
    # Counted on the INPUT, so it can be compared against the output. This is what
    # makes invariant S4 meaningful rather than tautological: if someone changes
    # the dedupe key to a business key, rows_out drops below the distinct-hash
    # count and the assertion catches it.
    distinct_hashes = scoped.select("_record_hash").distinct().count()
    deduped = scoped.dropDuplicates(["_record_hash"])
    rows_out = deduped.count()
    return deduped, newest, rows_in, rows_out, distinct_hashes

# COMMAND ----------

# MAGIC %md
# MAGIC ## Build the two record tables

# COMMAND ----------

report = {}


def build_silver(source_name, schema, business_key_cols=None):
    deduped, batch_id, rows_in, rows_out, distinct_hashes = latest_batch(source_name)

    # --- Unknown-field guard -------------------------------------------------
    # If openFDA adds a field, from_json silently drops it. That is the same
    # class of failure as inferring a schema from a sample: real data disappears
    # and nothing errors. Detect it here rather than discovering it in Gold.
    observed = {
        r["k"] for r in deduped.select(
            F.explode(json_top_level_keys(F.col("raw_json"))).alias("k")
        ).distinct().collect()
    }
    unknown = observed - DECLARED[source_name]
    missing = DECLARED[source_name] - observed

    # --- Parse ---------------------------------------------------------------
    parsed = deduped.withColumn("j", F.from_json(F.col("raw_json"), schema))
    cols = [F.col(f"j.{f}").alias(f) for f in schema.fieldNames()]
    flat = parsed.select(
        "_batch_id", "_source", "_source_url", "_ingested_at",
        "_api_last_updated", "_record_hash",
        F.get_json_object("raw_json", "$.openfda").alias("openfda_json"),
        *cols,
    )

    # --- Blank-to-null on string columns ------------------------------------
    for f in schema.fieldNames():
        if isinstance(schema[f].dataType, T.StringType):
            flat = flat.withColumn(f, blank_to_null(F.col(f)))

    # --- Typed dates, with the source string retained for the parity check ---
    date_fmts = DATE_FORMATS[source_name]
    for c, fmt in date_fmts.items():
        flat = flat.withColumn(f"__src_{c}", F.col(c))
        flat = flat.withColumn(c, F.to_date(F.col(f"__src_{c}"), fmt))

    # --- openfda: lossless string + populated flag + extracted scalars -------
    flat = (
        flat
        .withColumn(
            "openfda_populated",
            F.col("openfda_json").isNotNull() & (F.col("openfda_json") != "{}"),
        )
        .withColumn("openfda_manufacturer_name",
                    F.get_json_object("openfda_json", "$.manufacturer_name[0]"))
        .withColumn("openfda_product_ndc",
                    F.get_json_object("openfda_json", "$.product_ndc[0]"))
        .withColumn("openfda_generic_name",
                    F.get_json_object("openfda_json", "$.generic_name[0]"))
    )

    # --- firm_key + metric grains -------------------------------------------
    firm_col = "company_name" if source_name == "fda_shortages" else "recalling_firm"
    flat = flat.withColumn("firm_key", normalize_firm(F.col(firm_col)))
    flat = flat.withColumn("firm_key_openfda",
                           normalize_firm(F.col("openfda_manufacturer_name")))

    if source_name == "fda_shortages":
        flat = (
            flat
            .withColumn("is_active_strict", F.col("status") == "Current")
            .withColumn("is_active_broad",
                        F.col("status").isin("Current", "To Be Discontinued"))
        )
    else:
        flat = (
            flat
            .withColumn("is_active_strict", F.col("status") == "Ongoing")
            .withColumn("is_active_broad", F.col("status") != "Terminated")
        )

    flat = flat.withColumn("_silver_built_at", F.lit(silver_built_at).cast("timestamp"))

    # --- S2 date parity, computed BEFORE dropping the source strings --------
    parity = {}
    agg = flat.agg(*[
        F.sum(F.when(F.col(f"__src_{c}").isNull(), 1).otherwise(0)).alias(f"src_{c}")
        for c in date_fmts
    ] + [
        F.sum(F.when(F.col(c).isNull(), 1).otherwise(0)).alias(f"out_{c}")
        for c in date_fmts
    ]).collect()[0]
    for c in date_fmts:
        parity[c] = {"source_nulls": agg[f"src_{c}"], "parsed_nulls": agg[f"out_{c}"]}

    # --- Business-key distinctness (S4) -------------------------------------
    bk_distinct = (
        flat.select(*[F.col(c) for c in business_key_cols]).distinct().count()
        if business_key_cols else None
    )

    # --- Categorical domains (S5) -------------------------------------------
    domains = {}
    for c in ["status", "update_type", "classification"]:
        if c in flat.columns:
            domains[c] = {
                r[c] for r in flat.select(c).distinct().collect() if r[c] is not None
            }

    final = flat.drop(*[f"__src_{c}" for c in date_fmts])
    target = f"{CATALOG}.{SILVER_SCHEMA}.{source_name}"
    final.write.format("delta").mode("overwrite") \
        .option("overwriteSchema", "true").saveAsTable(target)

    report[source_name] = {
        "batch_id": batch_id,
        "rows_in": rows_in,
        "rows_out": rows_out,
        "distinct_hashes_in": distinct_hashes,
        "dropped_duplicates": rows_in - rows_out,
        "unknown_fields": sorted(unknown),
        "missing_declared_fields": sorted(missing),
        "date_parity": parity,
        "business_keys_distinct": bk_distinct,
        "domains": {k: sorted(v) for k, v in domains.items()},
        "openfda_populated": final.filter("openfda_populated").count(),
        "firm_key_nulls_where_source_present": final.filter(
            F.col(firm_col).isNotNull() & F.col("firm_key").isNull()
        ).count(),
        "table": target,
    }
    return final


shortages = build_silver(
    "fda_shortages", SHORTAGE_SCHEMA,
    business_key_cols=["generic_name", "company_name", "package_ndc", "presentation"],
)
enforcement = build_silver("fda_enforcement", ENFORCEMENT_SCHEMA)
print("Silver record tables written.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## `firm_xref` — entity resolution, made auditable
# MAGIC
# MAGIC The cross-feed join is the weakest link in this pipeline, so it gets its own
# MAGIC table rather than being buried inside a Gold join. Three passes, each
# MAGIC measured, so the progression is a queryable result instead of a claim:
# MAGIC
# MAGIC | Pass | Method |
# MAGIC |---|---|
# MAGIC | 0 | raw exact string equality (baseline) |
# MAGIC | 1 | normalized firm name |
# MAGIC | 2 | + normalized `openfda.manufacturer_name` |
# MAGIC
# MAGIC **A firm present in only one feed is `unmatched`, not "failed to match."**
# MAGIC A company with a current shortage may genuinely have no recall record — that
# MAGIC is a true absence, not a defect in the matching. Separating the two needs
# MAGIC manual review that has not been done, so the table states what it observed
# MAGIC and stops there.

# COMMAND ----------

sh_firms = (
    shortages.select(
        F.col("company_name").alias("raw_name"), F.col("firm_key")
    ).filter(F.col("firm_key").isNotNull()).distinct()
)
en_firms = (
    enforcement.select(
        F.col("recalling_firm").alias("raw_name"), F.col("firm_key")
    ).filter(F.col("firm_key").isNotNull()).distinct()
)
en_mfr = (
    enforcement.select(
        F.col("openfda_manufacturer_name").alias("raw_name"),
        F.col("firm_key_openfda").alias("firm_key"),
    ).filter(F.col("firm_key").isNotNull()).distinct()
)

pass0 = sh_firms.select("raw_name").intersect(en_firms.select("raw_name")).count()
pass1 = sh_firms.select("firm_key").intersect(en_firms.select("firm_key")).count()
en_all_keys = en_firms.select("firm_key").union(en_mfr.select("firm_key")).distinct()
pass2 = sh_firms.select("firm_key").intersect(en_all_keys).count()
sh_key_total = sh_firms.select("firm_key").distinct().count()

xref = (
    sh_firms.select("firm_key").distinct().withColumn("in_shortages", F.lit(True))
    .join(
        en_firms.select("firm_key").distinct().withColumn("in_recalling_firm", F.lit(True)),
        on="firm_key", how="full_outer",
    )
    .join(
        en_mfr.select("firm_key").distinct().withColumn("in_openfda_mfr", F.lit(True)),
        on="firm_key", how="full_outer",
    )
    .fillna(False, subset=["in_shortages", "in_recalling_firm", "in_openfda_mfr"])
)
xref = (
    xref
    .withColumn(
        "matched_both_feeds",
        F.col("in_shortages") & (F.col("in_recalling_firm") | F.col("in_openfda_mfr")),
    )
    .withColumn(
        "match_pass",
        F.when(~F.col("in_shortages"), F.lit(None).cast("int"))
         .when(F.col("in_recalling_firm"), F.lit(1))
         .when(F.col("in_openfda_mfr"), F.lit(2))
         .otherwise(F.lit(None).cast("int")),
    )
    .withColumn("_silver_built_at", F.lit(silver_built_at).cast("timestamp"))
)
xref.write.format("delta").mode("overwrite") \
    .option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{SILVER_SCHEMA}.firm_xref")

report["firm_xref"] = {
    "shortage_firm_keys": sh_key_total,
    "pass0_raw_exact": pass0,
    "pass1_normalized": pass1,
    "pass2_plus_openfda_mfr": pass2,
    "pass1_rate_pct": round(100.0 * pass1 / sh_key_total, 1),
    "pass2_rate_pct": round(100.0 * pass2 / sh_key_total, 1),
    "rows": xref.count(),
}
print("firm_xref written.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Validation scorecard

# COMMAND ----------

# --- Precondition -----------------------------------------------------------
# Notebook cells share ONE Python session, and this cell depends on state built
# by the cells above it. Without this check the failure surfaces as a bare
# NameError, which reads like a code defect rather than an execution-order one.
try:
    report
except NameError:
    raise RuntimeError(
        "`report` is not defined in this session.\n\n"
        "This cell depends on state created by the cells above it. Run the "
        "notebook top to bottom (Run all), or at minimum re-run the 'Build the "
        "two record tables' and 'firm_xref' cells first.\n\n"
        "COMMON CAUSE: importing a corrected copy of this file creates a SECOND "
        "notebook (e.g. '02_silver_validated (1)') with its own fresh, empty "
        "session -- rather than overwriting the original. Output you are looking "
        "at may be from the other copy."
    ) from None

W = 78
print("=" * W)
print("SILVER SCORECARD")
print("=" * W)
for src in ["fda_shortages", "fda_enforcement"]:
    r = report[src]
    print(f"\n  {src}   (batch {r['batch_id'][:8]}…)")
    print(f"    rows in / out .................... {r['rows_in']:>7,} / {r['rows_out']:>7,}")
    print(f"    exact duplicates dropped ......... {r['dropped_duplicates']:>7,}")
    if r["business_keys_distinct"] is not None:
        print(f"    distinct business keys ........... {r['business_keys_distinct']:>7,}   [expect < rows: lifecycle entries share a key]")
    print(f"    openfda populated ................ {r['openfda_populated']:>7,}")
    print(f"    firm_key null w/ source present .. {r['firm_key_nulls_where_source_present']:>7,}   target 0")
    print(f"    unknown fields ................... {r['unknown_fields'] or 'none'}")
    print(f"    declared but absent .............. {r['missing_declared_fields'] or 'none'}")
    print("    date parity (source nulls vs parsed nulls):")
    for c, p in r["date_parity"].items():
        ok = "ok" if p["source_nulls"] == p["parsed_nulls"] else "MISMATCH"
        print(f"      {c:30} {p['source_nulls']:>6,} / {p['parsed_nulls']:>6,}  [{ok}]")

x = report["firm_xref"]
print(f"\n  firm_xref — entity resolution progression")
print(f"    shortage firm keys ............... {x['shortage_firm_keys']:>7,}")
print(f"    pass 0  raw exact ................ {x['pass0_raw_exact']:>7,}")
print(f"    pass 1  normalized ............... {x['pass1_normalized']:>7,}   {x['pass1_rate_pct']}%")
print(f"    pass 2  + openfda manufacturer ... {x['pass2_plus_openfda_mfr']:>7,}   {x['pass2_rate_pct']}%")
print(f"    lift from normalization .......... {x['pass1_normalized'] - x['pass0_raw_exact']:>+7,}")
print(f"    lift from openfda enrichment ..... {x['pass2_plus_openfda_mfr'] - x['pass1_normalized']:>+7,}")
print("=" * W)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Invariants
# MAGIC
# MAGIC Two kinds, deliberately separated:
# MAGIC
# MAGIC | | Checks | On violation |
# MAGIC |---|---|---|
# MAGIC | **S1–S6 structural** | Properties of the *pipeline*: accounting balances, dates parse, dedupe touched only exact duplicates, no field silently dropped, domains closed | **Abort.** The code is wrong |
# MAGIC | **D1 drift** | Properties of *this snapshot*: population rates vs the profiled baseline | **Warn.** The data moved |
# MAGIC
# MAGIC That split was learned the hard way on the first run. The original version
# MAGIC asserted absolute counts profiled from the **raw** 1,651 records, then
# MAGIC compared them against **post-dedupe** Silver at 1,650. Every shortages count
# MAGIC came out exactly one low and the run failed on a pipeline that was working
# MAGIC correctly. Worse, as a weekly job against a live feed it would have failed on
# MAGIC every run the FDA published anything into — and an alarm that always fires is
# MAGIC an alarm that gets switched off.
# MAGIC
# MAGIC **S2 is still the one that matters most.** Spark's `to_date` returns NULL on
# MAGIC an unparseable string rather than raising. If openFDA changed a date format,
# MAGIC the column would silently fill with NULLs, every downstream date filter would
# MAGIC quietly narrow, and nothing would error. That is the same class of defect as
# MAGIC this project's own eval harness reporting a passing verdict on a wrong
# MAGIC answer — the instrument failing to detect the failure. It gets the same
# MAGIC treatment: assert parity explicitly and fail loudly.

# COMMAND ----------

# --- Precondition -----------------------------------------------------------
# Notebook cells share ONE Python session, and this cell depends on state built
# by the cells above it. Without this check the failure surfaces as a bare
# NameError, which reads like a code defect rather than an execution-order one.
try:
    report
except NameError:
    raise RuntimeError(
        "`report` is not defined in this session.\n\n"
        "This cell depends on state created by the cells above it. Run the "
        "notebook top to bottom (Run all), or at minimum re-run the 'Build the "
        "two record tables' and 'firm_xref' cells first.\n\n"
        "COMMON CAUSE: importing a corrected copy of this file creates a SECOND "
        "notebook (e.g. '02_silver_validated (1)') with its own fresh, empty "
        "session -- rather than overwriting the original. Output you are looking "
        "at may be from the other copy."
    ) from None

failures = []   # hard: abort the run
warnings_ = []  # drift: report, do not abort

for src in ["fda_shortages", "fda_enforcement"]:
    r = report[src]

    # ---- STRUCTURAL INVARIANTS (hard) ------------------------------------
    # These hold regardless of how the live feed moves. A failure means the
    # pipeline is wrong, not that the data changed.

    # S1 - row accounting balances. Nothing lost, nothing conjured.
    got = r["rows_out"] + r["dropped_duplicates"]
    if got != r["rows_in"]:
        failures.append(f"S1 {src}: rows_out+dropped={got} != rows_in={r['rows_in']}")

    # S2 - date parsing did not silently fail. THE IMPORTANT ONE.
    for c, p_ in r["date_parity"].items():
        if p_["source_nulls"] != p_["parsed_nulls"]:
            extra = p_["parsed_nulls"] - p_["source_nulls"]
            failures.append(
                f"S2 {src}.{c}: {extra} row(s) failed to parse silently "
                f"(source nulls {p_['source_nulls']}, parsed nulls {p_['parsed_nulls']}). "
                f"Format {DATE_FORMATS[src][c]!r} no longer matches the data."
            )

    # S3 - firm_key derived wherever a source name existed.
    if r["firm_key_nulls_where_source_present"] != 0:
        failures.append(
            f"S3 {src}: {r['firm_key_nulls_where_source_present']} row(s) have a "
            "source firm name but a NULL firm_key"
        )

    # S4 - dedupe collapsed EXACTLY the hash duplicates and nothing else.
    # Comparing output rows against distinct hashes IN THE INPUT is what makes
    # this real: swap the dedupe key for a business key and rows_out falls below
    # the distinct-hash count, destroying lifecycle history. This is the guard
    # that stops that refactor.
    if r["rows_out"] != r["distinct_hashes_in"]:
        failures.append(
            f"S4 {src}: rows_out={r['rows_out']:,} != distinct _record_hash in "
            f"input={r['distinct_hashes_in']:,}. Dedupe collapsed rows that were "
            "NOT exact duplicates -- lifecycle history may have been destroyed."
        )

    # S5 - categorical domains are closed. A genuinely new value is rare and
    # always worth a human decision, so this stays a hard failure.
    for col, expected_key in [("status", "status_domain"),
                              ("update_type", "update_type_domain"),
                              ("classification", "classification_domain")]:
        expected = EXPECTED[src].get(expected_key)
        if expected and col in r["domains"]:
            surprise = set(r["domains"][col]) - expected
            if surprise:
                failures.append(
                    f"S5 {src}.{col}: unexpected value(s) {sorted(surprise)}; "
                    f"expected only {sorted(expected)}"
                )

    # S6 - no undeclared source field is being silently dropped by from_json.
    if r["unknown_fields"]:
        failures.append(
            f"S6 {src}: undeclared field(s) present in source and being DROPPED: "
            f"{r['unknown_fields']}. Add them to the schema."
        )

    # ---- DRIFT CHECKS (warn) ---------------------------------------------
    # Properties of a snapshot, not of the pipeline. openFDA publishes
    # continuously, so these move legitimately. Report them; abort on neither.
    rate = r["openfda_populated"] / r["rows_out"] if r["rows_out"] else 0.0
    expected_rate = EXPECTED[src]["openfda_rate"]
    drift_pp = (rate - expected_rate) * 100
    r["openfda_rate"] = round(rate, 4)
    r["openfda_rate_drift_pp"] = round(drift_pp, 2)
    if abs(drift_pp) > RATE_TOLERANCE_PP:
        warnings_.append(
            f"D1 {src}: openfda populated {rate:.1%} vs profiled {expected_rate:.1%} "
            f"({drift_pp:+.1f}pp, tolerance +/-{RATE_TOLERANCE_PP}pp). Upstream shape "
            "may have changed -- confirm before trusting openfda-derived joins."
        )

sh = report["fda_shortages"]
if sh["business_keys_distinct"] is not None:
    collisions = sh["rows_out"] - sh["business_keys_distinct"]
    sh["business_key_collisions"] = collisions
    # Reported, never asserted. Collisions are lifecycle entries; the FDA
    # resolving some is normal and must not fail a run.
    print(f"  note: {collisions} business-key collision(s) in fda_shortages "
          "(same product, different lifecycle stage). Expected and preserved.")

print("=" * W)
for w_ in warnings_:
    print(f"  ~~ DRIFT: {w_}")
if warnings_:
    print("-" * W)
if failures:
    print("  VERDICT: FAILURES PRESENT — do not build Gold on this Silver.")
    for f_ in failures:
        print(f"    !! {f_}")
    print("=" * W)
    raise AssertionError(f"{len(failures)} Silver invariant(s) failed; see above.")
print("  VERDICT: all structural invariants hold. Row accounting balances, no")
print("           date parsed silently to NULL, dedupe touched only exact hash")
print("           duplicates, no undeclared source fields dropped, categorical")
print("           domains closed.")
if warnings_:
    print(f"           {len(warnings_)} drift warning(s) above -- not blocking, but read them.")
print("=" * W)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Machine-readable summary
# MAGIC
# MAGIC Copy into the field notes as the dated Silver baseline.

# COMMAND ----------

import json as _json
print(_json.dumps({"built_at": silver_built_at.isoformat(), "report": report},
                  indent=2, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Document in Unity Catalog
# MAGIC
# MAGIC Same reasoning as Bronze: Genie reads column metadata when answering
# MAGIC questions, so undocumented columns produce worse answers in Gold. The
# MAGIC metric-definition comments matter most — they are where "active" stops
# MAGIC being ambiguous.

# COMMAND ----------

SHARED_COLS = {
    "_batch_id": "Bronze batch this row came from; identifies one ingest run.",
    "_source": "Logical source name.",
    "_source_url": "openFDA endpoint the record came from.",
    "_ingested_at": "UTC timestamp when Bronze pulled this record.",
    "_api_last_updated": "meta.last_updated reported by openFDA for the dataset.",
    "_record_hash": "SHA-256 of the canonical raw JSON; the dedupe key.",
    "_silver_built_at": "UTC timestamp when this silver table was built.",
    "openfda_json": "Raw nested openfda object as JSON, kept lossless. Empty object when the source supplied none.",
    "openfda_populated": "True when openfda held any content. Note: the key is present on every enforcement row but populated on only ~18%.",
    "openfda_manufacturer_name": "First openfda.manufacturer_name entry. Secondary firm-match key.",
    "openfda_product_ndc": "First openfda.product_ndc entry.",
    "openfda_generic_name": "First openfda.generic_name entry.",
    "firm_key": "Normalized company name used to join across feeds. Upper-cased, punctuation stripped, corporate/industry suffixes removed. A heuristic, not authoritative entity resolution.",
    "firm_key_openfda": "firm_key derived from openfda.manufacturer_name instead of the primary firm field.",
}

SPECIFIC = {
    "fda_shortages": {
        "is_active_strict": "HEADLINE METRIC. status='Current' only: the shortage is in effect now.",
        "is_active_broad": "status in ('Current','To Be Discontinued'): any open exposure, including prospective discontinuation. Wider than the headline; do not mix the two in one figure.",
        "status": "Source lifecycle state: Current, To Be Discontinued, or Resolved.",
        "update_type": "Source-supplied update kind: New, Revised, or Reverified.",
        "company_name": "Firm as reported by the shortages feed. See firm_key for the normalized join key.",
        "therapeutic_category": "Array of therapeutic categories as supplied.",
        "discontinued_date": "Parsed date. Populated for ~27% of rows; NULL is genuine absence, not a parse failure (asserted by invariant S2).",
    },
    "fda_enforcement": {
        "is_active_strict": "HEADLINE METRIC. status='Ongoing' only: the recall is still in progress.",
        "is_active_broad": "status <> 'Terminated': not yet closed by FDA, including Completed. Wider than the headline; do not mix the two in one figure.",
        "status": "Source lifecycle state: Ongoing, Completed, or Terminated.",
        "classification": "FDA recall class. Class I is the most serious (reasonable probability of serious health consequences), then II, then III.",
        "recalling_firm": "Firm as reported by the enforcement feed. See firm_key for the normalized join key.",
        "voluntary_mandated": "Whether the recall was firm-initiated or FDA-mandated. Literal 'N/A' is preserved as supplied; only empty strings were converted to NULL.",
        "termination_date": "Parsed date. NULL for ~17% of rows because the recall is not terminated, which is genuine absence rather than a parse failure.",
    },
}

LAYER_NOTE = (
    "Silver: typed, deduplicated, conformed openFDA records from the most recent "
    "bronze batch. Deduplicated on _record_hash ONLY -- never on business key, "
    "because the same product legitimately appears at multiple lifecycle stages "
    "and collapsing those would destroy real history. Represents CURRENT STATE; "
    "full observation history remains in the bronze layer. Does not aggregate, "
    "score, or join the feeds -- that is the gold layer. Public FDA data, not for "
    "clinical use, and nothing here is an FDA judgment about any company."
)

_layer_note_sql = LAYER_NOTE.replace("'", "''")
for src in ["fda_shortages", "fda_enforcement"]:
    target = f"{CATALOG}.{SILVER_SCHEMA}.{src}"
    spark.sql(f"COMMENT ON TABLE {target} IS '{_layer_note_sql}'")
    present = set(spark.table(target).columns)
    for col, desc in {**SHARED_COLS, **SPECIFIC[src]}.items():
        if col in present:
            safe = desc.replace("'", "''")
            spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} COMMENT '{safe}'")

xref_target = f"{CATALOG}.{SILVER_SCHEMA}.firm_xref"
spark.sql(f"""
    COMMENT ON TABLE {xref_target} IS
    'Silver: firm crosswalk making cross-feed entity resolution auditable. One row
     per normalized firm_key observed in either feed. A firm appearing in only one
     feed is UNMATCHED, which is not the same as failing to match -- a company with
     a shortage may genuinely have no recall record. Distinguishing true absence
     from a normalization miss requires manual review that has not been done.'
""".replace("\n", " "))
for col, desc in {
    "firm_key": "Normalized firm name. Join key for the gold layer.",
    "in_shortages": "firm_key was observed in the shortages feed.",
    "in_recalling_firm": "firm_key was observed in enforcement.recalling_firm.",
    "in_openfda_mfr": "firm_key was observed in enforcement openfda.manufacturer_name.",
    "matched_both_feeds": "Present in shortages AND in enforcement by either route.",
    "match_pass": "Which pass matched this firm: 1 = normalized recalling_firm, 2 = openfda manufacturer enrichment. NULL when not matched or not present in shortages.",
    "_silver_built_at": "UTC timestamp when this table was built.",
}.items():
    spark.sql(f"ALTER TABLE {xref_target} ALTER COLUMN {col} COMMENT '{desc}'")

print("Table and column comments applied to all three silver tables.")
