"""Where the data lives, what counts as in scope, and the constants defining the metrics.

Everything tunable is in this module: no other file hard-codes a path, a threshold, a
DuckDB setting or a scope criterion. SCOPE is the one most worth reading, since it
decides which works the metrics are normalised against.
"""

import hashlib
import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # optional: the build itself needs no credentials
    load_dotenv = None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Everything the build produces, or a downloaded bundle, goes under one directory.
# Resolution order:
#   1. OAM_DATA_DIR, when set
#   2. <repo>/data, when this file is a source checkout (so a local build is found)
#   3. ~/oam-data, when the package was installed with pip and has no checkout
# set_data_dir() moves it for the rest of the process. Nothing else caches these
# paths: every reader looks them up on the config module when it needs them.

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent

if load_dotenv is not None:
    load_dotenv(REPO_DIR / ".env")


def _default_data_dir():
    env = os.environ.get("OAM_DATA_DIR")
    if env:
        return Path(env).expanduser()
    checkout = (REPO_DIR / "pyproject.toml").is_file() and (REPO_DIR / "oam").is_dir()
    if checkout:
        return REPO_DIR / "data"
    return Path.home() / "oam-data"


def _apply_data_dir(path):
    """Point every derived path at `path`. Called once at import and by set_data_dir."""
    global DATA_DIR, SNAPSHOT_DIR, STORE_DIR, METRICS_DIR, RESOLVE_DIR, LOGS_DIR
    global MANIFEST_PATH, ENTITY_MANIFEST_PATH, STORE_STATE_PATH, RESOLVE_RAW_DIR
    global BUNDLE_PATH
    DATA_DIR = Path(path).expanduser().resolve()
    SNAPSHOT_DIR = DATA_DIR / "snapshot"   # cached OpenAlex manifests
    STORE_DIR = DATA_DIR / "store"         # build step 1 output
    METRICS_DIR = DATA_DIR / "metrics"     # build step 2 output
    RESOLVE_DIR = DATA_DIR / "resolve"     # build step 3 output
    LOGS_DIR = DATA_DIR / "logs"           # one timestamped file per build
    MANIFEST_PATH = SNAPSHOT_DIR / "works_manifest.json"
    ENTITY_MANIFEST_PATH = SNAPSHOT_DIR / "entities_manifest.json"
    # What the store was built from: the snapshot release, the scope, and the
    # projection version that produced it. A downloaded bundle ships this too.
    STORE_STATE_PATH = STORE_DIR / "_state" / "snapshot.json"
    # Step 1 writes the resolve rows out per source file, unsorted; step 3 sorts
    # them into RESOLVE_DIR and deletes them. They live inside the store because
    # they are pure intermediates that no query ever reads.
    RESOLVE_RAW_DIR = STORE_DIR / "_resolve_raw"
    BUNDLE_PATH = DATA_DIR / "bundle.json"


def set_data_dir(path):
    """Use `path` as the data directory for the rest of this process.

    Closes the shared query connection, which was spilling into the old directory.
    """
    _apply_data_dir(path)
    from . import duck
    duck.reset_read_connection()


_apply_data_dir(_default_data_dir())

STORE_TABLES = ("works_status", "works_scope", "edges")
METRIC_TABLES = ("citation_counts", "bin_stats", "work_metrics")

# The resolve warehouse: query -> work ids without the OpenAlex API. The fact tables
# cover every work in the corpus, in scope or not, and each is sorted by the key it is
# looked up on so a lookup reads a handful of row groups instead of the whole table.
RESOLVE_TABLES = ("works_attrs", "work_institutions", "work_authors",
                  "work_funders", "work_doi", "author_orcid")

# Names for the numeric ids the fact tables store. Tiny, and rebuilt from the entity
# snapshots rather than the works files.
DIM_TABLES = ("dim_topics", "dim_institutions", "dim_sources", "dim_funders")

# Bumped whenever step 1 starts writing a table or column it did not write before. An
# existing store cannot be topped up across such a change, so the build refuses to
# resume and asks for a reset, exactly as it does for a new snapshot or a new scope.
# Downloaded bundles carry the same number: a bundle from an older projection is
# refused rather than queried as if it had the current columns.
PROJECTION_VERSION = 2
BUNDLE_FORMAT = PROJECTION_VERSION

# Numeric id of the Zenodo record that holds the published data release. None until
# the first release exists; download() then needs an explicit file or URL.
ZENODO_RECORD = None
ZENODO_API = "https://zenodo.org/api/records/"


def table_dir(table):
    """Directory holding one table's Parquet files."""
    if table in STORE_TABLES:
        base = STORE_DIR
    elif table in RESOLVE_TABLES or table in DIM_TABLES:
        base = RESOLVE_DIR
    else:
        base = METRICS_DIR
    return base / table


def raw_table_dir(table):
    """Directory holding one resolve table's unsorted per-source-file output."""
    return RESOLVE_RAW_DIR / table


def table_glob(table):
    """Glob a table's Parquet files. Every table is a directory, partitioned or not."""
    return (table_dir(table) / "**" / "*.parquet").as_posix()


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------
# The snapshot bucket is read over plain https so requests stay unsigned. The s3://
# form would make DuckDB sign with whatever local AWS profile happens to exist.

S3_PREFIX = "s3://openalex/"
HTTPS_PREFIX = "https://openalex.s3.amazonaws.com/"

# PyArrow reads the works files instead (see oam/arrow.py) and addresses the bucket
# directly rather than over https, so it needs the name and region separately.
S3_BUCKET = "openalex"
S3_REGION = "us-east-1"


def s3_path(url):
    """A snapshot URL in any of its forms as the bucket-relative path PyArrow wants."""
    for prefix in (HTTPS_PREFIX, S3_PREFIX):
        if url.startswith(prefix):
            return f"{S3_BUCKET}/{url[len(prefix):]}"
    return url
MANIFEST_URL = HTTPS_PREFIX + "data/parquet/works/manifest.json"

# Covers every entity type, not just works. Step 3 reads the institutions, sources,
# funders and subfields file lists out of it to build the dimension tables.
ENTITY_MANIFEST_URL = HTTPS_PREFIX + "data/parquet/manifest.json"


def numeric_id_sql(expr):
    """SQL turning an OpenAlex entity URL into the number it ends with.

    'https://openalex.org/' is 21 characters for every entity type, so dropping the
    first 22 drops the type letter with the prefix: W2741809807 -> 2741809807,
    I5124864 -> 5124864. Subfields and other nested ids do not follow this shape and
    use str_split instead (see SUBFIELD_SQL).
    """
    return f"TRY_CAST(substr({expr}, 23) AS BIGINT)"


# The two identifiers that are strings rather than numbers. Both are stored bare and
# lowercased so a caller can hand over whatever shape they have — a bare DOI, a
# doi.org URL, an ORCID with or without its prefix — and still match.
DOI_SQL = "nullif(regexp_replace(lower({}), '^https?://(dx\\.)?doi\\.org/', ''), '')"
ORCID_SQL = "nullif(regexp_replace(lower({}), '^https?://orcid\\.org/', ''), '')"


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------
# Which works join the metric population. Each entry is a criterion name, the SQL that
# decides it, and the label the profile funnel prints for the works it rejects; a work
# is in scope when every criterion holds. This dict is the whole definition — store.py
# generates the build SQL from it, report.py and query.py read the criteria back out of
# the store — so changing the scope means editing here and rebuilding. See docs/scope.md.
#
# The SQL is evaluated against one row of the OpenAlex works Parquet, so any column in
# docs/openalex-works-schema.txt is available and nothing needs to declare which ones
# are read: DuckDB works that out from the SQL. A criterion touching a wide column such
# as authorships or abstract_inverted_index would still be a bad idea, because the build
# is fast only by reading 2-4% of each file.
#
# Two criteria read columns that differ from the obvious choice, both verified against
# that schema:
#   * there is no type_crossref column; the crossref string lives in
#     primary_location.raw_type, so 'proceedings-article' is read from there.
#   * institutions_distinct_count equals COUNT(DISTINCT authorships[].institutions[].id),
#     which supplies both has_authorship and the fractional-counting denominator without
#     ever reading the large authorships column.

# Shared with the bin dimensions store.py derives, so that a criterion can never
# disagree with the column built from the same field. has_subfield in particular has to
# test the *parsed* subfield: a work whose subfield id failed TRY_CAST would otherwise
# be in scope carrying a NULL bin dimension.
SUBFIELD_SQL = "TRY_CAST(str_split(primary_topic.subfield.id, '/')[-1] AS INTEGER)"
IS_CORE_SQL = "COALESCE(primary_location.source.is_core, FALSE)"
IS_PROCEEDINGS_SQL = "COALESCE(primary_location.raw_type = 'proceedings-article', FALSE)"

SCOPE = {
    "is_english": (
        "lower(COALESCE(language, '')) = 'en'",
        "not English"),
    "has_authorship": (
        "COALESCE(institutions_distinct_count, 0) > 0",
        "no institutional affiliation"),
    "has_subfield": (
        f"{SUBFIELD_SQL} IS NOT NULL",
        "no subfield"),
    "has_referenced_works": (
        "COALESCE(len(referenced_works), 0) > 0",
        "no outgoing references"),
    "is_core_or_proceedings": (
        f"{IS_CORE_SQL} OR {IS_PROCEEDINGS_SQL}",
        "not a core journal or conference paper"),
    "is_not_retracted": (
        "NOT COALESCE(is_retracted, FALSE)",
        "retracted"),
    "is_not_paratext": (
        "NOT COALESCE(is_paratext, FALSE)",
        "paratext"),
}


def scope_sql(scope=None):
    """The scope as {criterion: sql}, whitespace normalised, labels dropped.

    This is the form that gets recorded in the store and hashed, so reformatting a
    criterion's SQL does not read as a change while rewriting it does.
    """
    scope = SCOPE if scope is None else scope
    return {name: " ".join(sql.split()) for name, (sql, _) in scope.items()}


def scope_hash(criteria=None):
    """Short fingerprint of a {criterion: sql} mapping, as recorded by scope_sql()."""
    criteria = scope_sql() if criteria is None else criteria
    payload = json.dumps(criteria, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def built_scope():
    """The {criterion: sql} the store on disk was actually built with.

    Readers use this rather than SCOPE so that editing the dict before rebuilding
    cannot make them ask works_status for a column that was never written. Falls back
    to the current definition when there is no store yet.
    """
    if STORE_STATE_PATH.exists():
        recorded = json.loads(STORE_STATE_PATH.read_text()).get("scope")
        if recorded:
            return recorded
    return scope_sql()


def built_criteria():
    """(name, funnel label) for each criterion the store was built with."""
    labels = {name: label for name, (_, label) in SCOPE.items()}
    return [(name, labels.get(name, name)) for name in built_scope()]


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

# Bins below this many works give unstable means and percentiles. OAM computes the
# metrics anyway and flags the bin, so a caller can decide; filter on
# bin_is_reliable to drop them.
RELIABILITY_THRESHOLD = 500

# Citations accrued within this many years of publication, for the fixed-window
# variant. Counted during the build because revisiting the edge list is expensive.
WINDOW_YEARS = 3


# ---------------------------------------------------------------------------
# DuckDB
# ---------------------------------------------------------------------------

# Single-connection work: the metrics build and every query.
MEMORY_LIMIT = os.environ.get("OAM_MEMORY_LIMIT", "20GB")
THREADS = int(os.environ.get("OAM_THREADS", "8"))

# The store build runs many connections at once and is network-bound, so each one
# gets a modest allowance.
WORKER_MEMORY_LIMIT = "3GB"
WORKER_THREADS = 2
