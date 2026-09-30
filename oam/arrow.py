"""Reads one snapshot part file into Arrow, fetching only the leaves OAM needs.

This exists because of one property of DuckDB's Parquet reader: it prunes leaf columns
inside a STRUCT but not inside a LIST(STRUCT). Asking it for authorships[].author.id
therefore downloads the whole authorships column — 14.9% of every part file, against
the ~3% its leaves actually occupy. Since the build is bandwidth-bound, that dominates
the runtime of build step 1; see finding 2 in docs/openalex-works-schema.txt.

PyArrow can address the leaves individually, which is the whole reason this module is
here. Nothing downstream changes: the table it returns keeps the nested shape the
snapshot has, so store.py registers it and runs exactly the SQL it would otherwise
have run against read_parquet(), and DuckDB still does all the computing.

The one sharp edge is that PyArrow's *named* column selection has the same blind spot
as DuckDB, and fails silently: passing columns=['authorships.author.id'] to
ParquetFile.read() drops the column from the result rather than raising. Leaves inside
a repeated group have to be selected by their physical index instead, which is what
column_indices() works out from the footer.
"""

import threading

import pyarrow.fs as pafs
import pyarrow.parquet as pq

from . import config

# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------
# Every leaf store.py's SQL reads, as logical dotted paths. This list and the SQL have
# to agree: a column missing here does not fail the read, it arrives as a column
# DuckDB has never heard of, so _indices() below insists that all of them resolve.
#
# Grouped the way docs/openalex-works-schema.txt documents them.

COLUMNS = (
    # Identity, scope and the bin dimensions.
    "id",
    "publication_year",
    "language",
    "type",
    "is_retracted",
    "is_paratext",
    "is_xpac",
    "institutions_distinct_count",
    "referenced_works",
    "primary_location.source.is_core",
    "primary_location.raw_type",
    "primary_topic.subfield.id",
    # The resolve warehouse.
    "doi",
    "primary_location.source.id",
    "open_access.oa_status",
    "open_access.is_oa",
    "funders.id",
    "authorships.author.id",
    "authorships.author.orcid",
    "authorships.is_corresponding",
    "authorships.institutions.id",
    "authorships.institutions.lineage",
)

# Parquet represents a repeated field as a synthetic group wrapping the real element,
# so the physical path of authorships[].author.id is
# authorships.list.element.author.id. These are the synthetic names to drop when
# comparing a physical path against the logical ones above.
_REPETITION_NODES = frozenset({"list", "element", "item"})


def _logical(physical_path):
    """'authorships.list.element.author.id' -> 'authorships.author.id'"""
    return ".".join(part for part in physical_path.split(".")
                    if part not in _REPETITION_NODES)


def _indices(parquet_file, source):
    """Physical leaf indices for COLUMNS, in file order.

    One logical path can cover several leaves — authorships.institutions.lineage is a
    list of strings and so is one leaf, but asking for a struct would be more — so
    this collects every leaf under each requested path.
    """
    schema = parquet_file.schema
    by_path = {}
    for index in range(len(schema)):
        by_path.setdefault(_logical(schema.column(index).path), []).append(index)

    missing = [path for path in COLUMNS if path not in by_path]
    if missing:
        raise RuntimeError(
            f"{source}: the snapshot has no {', '.join(missing)}. The release schema "
            f"has changed, so oam/arrow.py and the SQL in oam/store.py no longer "
            f"match it; see docs/openalex-works-schema.txt."
        )
    return sorted(index for path in COLUMNS for index in by_path[path])


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
# The snapshot bucket is public, so the client is anonymous: signing it would make
# PyArrow pick up whatever local AWS profile happens to exist, the same reason the
# DuckDB path reads over plain https.
#
# Building the client costs a few seconds, which is why it is made once and shared.
# S3FileSystem is thread-safe, and the store build runs one worker per thread.

_filesystem = None
_filesystem_lock = threading.Lock()


def s3():
    global _filesystem
    if _filesystem is None:
        with _filesystem_lock:
            if _filesystem is None:
                _filesystem = pafs.S3FileSystem(anonymous=True,
                                                region=config.S3_REGION)
    return _filesystem


def read_part(source):
    """One part file's projection as an Arrow table.

    `source` is either a snapshot URL in any of its forms or a local path, so the
    retry in store.py can fall back to reading a downloaded copy.

    pre_buffer coalesces the selected column chunks into few large range requests
    instead of one per chunk, which matters over S3: there are 22 leaves across every
    row group, and asking for them one at a time spends the whole time in latency.
    """
    if source.startswith(("http://", "https://", "s3://")):
        filesystem, path = s3(), config.s3_path(source)
    else:
        filesystem, path = pafs.LocalFileSystem(), source

    with filesystem.open_input_file(path) as handle:
        parquet_file = pq.ParquetFile(handle, pre_buffer=True)
        return parquet_file.reader.read_all(
            column_indices=_indices(parquet_file, source), use_threads=True)


def projected_fraction(source):
    """What share of a part file the projection reads, from its footer alone.

    Used by the byte accounting in docs/ rather than by the build.
    """
    if source.startswith(("http://", "https://", "s3://")):
        filesystem, path = s3(), config.s3_path(source)
    else:
        filesystem, path = pafs.LocalFileSystem(), source

    with filesystem.open_input_file(path) as handle:
        parquet_file = pq.ParquetFile(handle)
        wanted = set(_indices(parquet_file, source))
        metadata = parquet_file.metadata
        total = selected = 0
        for group_index in range(metadata.num_row_groups):
            group = metadata.row_group(group_index)
            for column in range(group.num_columns):
                size = group.column(column).total_compressed_size
                total += size
                if column in wanted:
                    selected += size
        return selected, total
