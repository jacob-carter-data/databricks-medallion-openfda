# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Bronze: raw openFDA ingest
# MAGIC
# MAGIC Lands two live openFDA feeds as **append-only raw Delta tables**:
# MAGIC
# MAGIC | Source | Endpoint | Approx. records (2026-08-05) |
# MAGIC |---|---|---|
# MAGIC | Drug shortages | `/drug/shortages.json` | 1,651 |
# MAGIC | Recall enforcement | `/drug/enforcement.json` | 17,860 |
# MAGIC
# MAGIC ## What "Bronze" means here, and why it matters
# MAGIC
# MAGIC Bronze stores **exactly what the API returned**, as a JSON string, plus
# MAGIC ingest metadata. Nothing is typed, cleaned, renamed, or deduplicated. It is
# MAGIC an immutable log of *what the source said at the time we asked*.
# MAGIC
# MAGIC That has a consequence people find counterintuitive: **re-running this
# MAGIC notebook deliberately creates duplicate rows.** That is correct. Two runs a
# MAGIC week apart legitimately captured two different observations of the same
# MAGIC record, and collapsing them here would destroy the history that makes the
# MAGIC layer worth having. Deduplication is Silver's job, on `_record_hash`.
# MAGIC
# MAGIC If Bronze were made idempotent by upserting, the pipeline could no longer
# MAGIC answer "what did the FDA say on August 5th?" — which is the question that
# MAGIC justifies keeping a raw layer at all.
# MAGIC
# MAGIC ## Honesty rails enforced in this notebook
# MAGIC
# MAGIC - **A blocked or failed HTTP call raises.** It never writes a partial or
# MAGIC   empty table. Free Edition restricts outbound internet until account
# MAGIC   verification, and the failure mode to avoid is a silent empty Bronze that
# MAGIC   looks like "the FDA had no recalls."
# MAGIC - **Row counts are asserted against the API's own `meta.results.total`.**
# MAGIC   A short read fails the run rather than quietly under-reporting.
# MAGIC - **Provenance travels with every row**: when we asked, what we asked, and
# MAGIC   when the source says it was last updated.
# MAGIC
# MAGIC ## Source disclaimer
# MAGIC
# MAGIC openFDA data is public and carries the FDA's own caveats: it is not for
# MAGIC clinical use, records are not a judgment about a company, and absence of a
# MAGIC record is not evidence of safety. Anything derived downstream is a
# MAGIC constructed view, never an FDA determination.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Config
# MAGIC
# MAGIC Note on structure: this block is duplicated at the top of each notebook in
# MAGIC this pipeline rather than factored into a shared `%run` include. That is a
# MAGIC deliberate trade — a shared include adds a path-resolution failure mode on
# MAGIC first run, and on Free Edition the priority is that a new user's first
# MAGIC execution succeeds. Once all three notebooks are stable, factoring this into
# MAGIC `00_config` and `%run`-ing it is the right cleanup.

# COMMAND ----------

CATALOG = "workspace"      # verify against the discovery cell below before running
BRONZE_SCHEMA = "bronze"
SILVER_SCHEMA = "silver"
GOLD_SCHEMA = "gold"

# openFDA allows limit<=1000 per request and skip<=25000 total.
PAGE_SIZE = 1000
SKIP_CEILING = 25000

# An explicit `sort` is REQUIRED, not cosmetic. openFDA does not document a
# guaranteed default ordering, and `skip`-based pagination over an unstable order
# can return one record twice while silently missing another -- with the total
# count still matching, which hides the loss completely.
#
# This was not hypothetical. The first run produced 1,651 rows but only 1,650
# distinct record hashes. Re-pulling with an explicit sort reproduced the same
# 1,650, which proved the duplicate was genuinely in the source rather than a
# paging artifact. The benign explanation turned out to be right; relying on it
# being right would still have been wrong.
SOURCES = {
    "fda_shortages": {
        "url": "https://api.fda.gov/drug/shortages.json",
        "sort": "update_date:asc",
    },
    "fda_enforcement": {
        "url": "https://api.fda.gov/drug/enforcement.json",
        "sort": "report_date:asc",
    },
}

# COMMAND ----------

# MAGIC %md
# MAGIC ## Discovery — confirm the catalog exists before writing anything
# MAGIC
# MAGIC Free Edition provisions Unity Catalog, but the default catalog name is not
# MAGIC guaranteed to be `workspace` on every account. Check rather than assume: a
# MAGIC wrong guess here fails 40 minutes later with a confusing message.

# COMMAND ----------

display(spark.sql("SHOW CATALOGS"))

# COMMAND ----------

# Index by position rather than by column name: the column SHOW CATALOGS
# returns has been named both `catalog` and `catalogName` across versions.
available = {r[0] for r in spark.sql("SHOW CATALOGS").collect()}
assert CATALOG in available, (
    f"Catalog {CATALOG!r} not found. Available: {sorted(available)}. "
    "Set CATALOG in the config cell to one of these and re-run."
)

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{BRONZE_SCHEMA}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SILVER_SCHEMA}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{GOLD_SCHEMA}")
print(f"OK: using catalog {CATALOG!r}; bronze/silver/gold schemas ready.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Fetch
# MAGIC
# MAGIC openFDA's rate limit without an API key is 240 requests/minute and 1,000
# MAGIC per day per IP. This pulls roughly 20 requests total, so no key is needed —
# MAGIC but a small pause between pages keeps us clearly inside the per-minute
# MAGIC limit and is the habit worth teaching, since the same code against a larger
# MAGIC endpoint would need it.

# COMMAND ----------

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

import requests


def fetch_all(url, label, sort, page_size=PAGE_SIZE):
    """Page through an openFDA endpoint. Returns (records, api_last_updated, total).

    Raises on any HTTP or transport failure. This is load-bearing: on Free
    Edition an unverified account cannot reach api.fda.gov, and the wrong
    behavior would be to swallow that and write an empty Bronze table.
    """
    first = requests.get(url, params={"limit": 1, "sort": sort}, timeout=60)
    first.raise_for_status()
    meta = first.json()["meta"]
    total = meta["results"]["total"]
    api_last_updated = meta.get("last_updated")

    if total > SKIP_CEILING:
        raise RuntimeError(
            f"{url} reports {total:,} records, above openFDA's skip ceiling of "
            f"{SKIP_CEILING:,}. Paging by `skip` alone cannot reach them all. "
            "Partition the pull by a search range (e.g. report_date buckets) "
            "before ingesting this source."
        )

    records = []
    for skip in range(0, total, page_size):
        resp = requests.get(
            url,
            params={"limit": page_size, "skip": skip, "sort": sort},
            timeout=120,
        )
        resp.raise_for_status()
        batch = resp.json().get("results", [])
        if not batch:
            break
        records.extend(batch)
        print(f"  {label}: {len(records):,}/{total:,}")
        time.sleep(0.3)

    if len(records) != total:
        raise RuntimeError(
            f"Short read from {url}: got {len(records):,}, expected {total:,}. "
            "Refusing to write a partial Bronze batch.\n"
            "Most likely cause: openFDA published an update between the count "
            "request and the last page, so `total` moved under us. Simply re-run "
            "-- nothing was written, and Bronze is append-only so a retry is "
            "safe. If it fails repeatedly, the endpoint is paginating "
            "inconsistently and is worth recording in the field notes."
        )

    return records, api_last_updated, total


def record_hash(record):
    """Stable identity for a record, used by Silver to deduplicate.

    Hashes the canonical JSON form (sorted keys) so key ordering in the API
    response can never change a record's identity.
    """
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

# COMMAND ----------

from pyspark.sql import types as T

BRONZE_SCHEMA_SPEC = T.StructType([
    T.StructField("_batch_id", T.StringType(), False),
    T.StructField("_source", T.StringType(), False),
    T.StructField("_source_url", T.StringType(), False),
    T.StructField("_ingested_at", T.TimestampType(), False),
    T.StructField("_api_last_updated", T.StringType(), True),
    T.StructField("_record_hash", T.StringType(), False),
    T.StructField("raw_json", T.StringType(), False),
])

# One batch id for this whole run, so a single execution is traceable across
# both tables. Timezone-aware on purpose: `datetime.utcnow()` is deprecated and
# emits a warning in this runtime, which is exactly the sort of thing that reads
# as "the platform is broken" in a learner's first notebook.
batch_id = str(uuid.uuid4())
ingested_at = datetime.now(timezone.utc)
print(f"batch_id={batch_id}  ingested_at={ingested_at.isoformat()}")

# COMMAND ----------

summary = []

for source_name, spec in SOURCES.items():
    url, sort = spec["url"], spec["sort"]
    print(f"\n=== {source_name} (sort={sort}) ===")
    records, api_last_updated, total = fetch_all(url, source_name, sort)

    rows = [
        (
            batch_id,
            source_name,
            url,
            ingested_at,
            api_last_updated,
            record_hash(rec),
            json.dumps(rec, sort_keys=True, separators=(",", ":")),
        )
        for rec in records
    ]

    df = spark.createDataFrame(rows, schema=BRONZE_SCHEMA_SPEC)
    target = f"{CATALOG}.{BRONZE_SCHEMA}.{source_name}"

    # APPEND, never overwrite. See the note at the top of this notebook.
    df.write.format("delta").mode("append").saveAsTable(target)

    distinct_hashes = df.select("_record_hash").distinct().count()
    summary.append({
        "source": source_name,
        "api_total": total,
        "rows_written": len(rows),
        "distinct_hashes_in_batch": distinct_hashes,
        "api_last_updated": api_last_updated,
        "table": target,
    })
    print(f"  wrote {len(rows):,} rows to {target}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Ingest report
# MAGIC
# MAGIC `rows_written` must equal `api_total`. If `distinct_hashes_in_batch` is
# MAGIC lower than `rows_written`, the API returned genuine duplicates within a
# MAGIC single pull — worth knowing about, and a real finding rather than a bug on
# MAGIC our side. Silver collapses them.

# COMMAND ----------

for s in summary:
    print(
        f"{s['source']:<18} api_total={s['api_total']:>7,}  "
        f"written={s['rows_written']:>7,}  "
        f"distinct={s['distinct_hashes_in_batch']:>7,}  "
        f"api_last_updated={s['api_last_updated']}"
    )

_summary_schema = T.StructType([
    T.StructField("source", T.StringType()),
    T.StructField("api_total", T.LongType()),
    T.StructField("rows_written", T.LongType()),
    T.StructField("distinct_hashes_in_batch", T.LongType()),
    T.StructField("api_last_updated", T.StringType()),
    T.StructField("table", T.StringType()),
])
display(spark.createDataFrame(
    [
        (s["source"], s["api_total"], s["rows_written"],
         s["distinct_hashes_in_batch"], s["api_last_updated"], s["table"])
        for s in summary
    ],
    schema=_summary_schema,
))

# COMMAND ----------

for s in summary:
    assert s["rows_written"] == s["api_total"], (
        f"{s['source']}: wrote {s['rows_written']} but API reported {s['api_total']}"
    )
    if s["distinct_hashes_in_batch"] != s["rows_written"]:
        dupes = s["rows_written"] - s["distinct_hashes_in_batch"]
        print(
            f"NOTE: {s['source']} contained {dupes} exact duplicate record(s) "
            "within a single API pull. Not an error; Silver will collapse them."
        )
print("\nBronze ingest verified.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cumulative table state
# MAGIC
# MAGIC Row counts here grow with every run, by design. Batch count is the number
# MAGIC of distinct ingests captured so far.

# COMMAND ----------

for source_name in SOURCES:
    target = f"{CATALOG}.{BRONZE_SCHEMA}.{source_name}"
    display(
        spark.sql(f"""
            SELECT '{source_name}' AS source,
                   COUNT(*)                      AS total_rows_all_batches,
                   COUNT(DISTINCT _batch_id)     AS batches,
                   COUNT(DISTINCT _record_hash)  AS distinct_records,
                   MIN(_ingested_at)             AS first_ingest,
                   MAX(_ingested_at)             AS latest_ingest,
                   MAX(_api_last_updated)        AS api_last_updated
            FROM {target}
        """)
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Document the tables in Unity Catalog
# MAGIC
# MAGIC Comments are not decoration. Genie reads column metadata when answering
# MAGIC questions, so undocumented columns produce worse answers in Gold. This is
# MAGIC the cheapest possible investment in the Genie space working well later.

# COMMAND ----------

for source_name, spec in SOURCES.items():
    url = spec["url"]
    target = f"{CATALOG}.{BRONZE_SCHEMA}.{source_name}"
    table_comment = (
        f"Bronze: raw append-only openFDA records from {url}. One row per record "
        "per ingest run; duplicates across runs are intentional and represent "
        "repeated observations over time. Never edited in place. Deduplicated "
        "downstream in the silver layer on _record_hash. Public FDA data, not "
        "for clinical use."
    )
    spark.sql(f"COMMENT ON TABLE {target} IS '{table_comment}'")
    for col, desc in {
        "_batch_id": "UUID identifying one execution of the bronze ingest notebook.",
        "_source": "Logical source name, e.g. fda_shortages.",
        "_source_url": "Exact endpoint the record came from.",
        "_ingested_at": "UTC timestamp when this batch was pulled.",
        "_api_last_updated": "meta.last_updated as reported by openFDA for this dataset.",
        "_record_hash": "SHA-256 of the canonical JSON form; stable record identity used for dedupe.",
        "raw_json": "The unmodified API record, canonical JSON with sorted keys.",
    }.items():
        spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} COMMENT '{desc}'")

print("Table and column comments applied.")
