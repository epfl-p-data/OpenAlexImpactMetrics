"""Shared plumbing for both build steps: DuckDB connections, atomic Parquet writes,
checkpoints and human-readable formatting.

Nothing here knows anything about citations; it is the machinery that makes a build
resumable and safe to interrupt.
"""

import json
import os
import shutil
import threading
import time
from pathlib import Path

import duckdb

from . import config


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

_read_con = None


def reset_read_connection():
    """Drop the shared connection so the next query opens one on the current data dir."""
    global _read_con
    if _read_con is not None:
        _read_con.close()
        _read_con = None


def read_connection():
    """The shared connection every query reads through, opened on first use.

    One engine for the whole process: query.py and resolver.py both answer from the
    same Parquet and would otherwise each stand up their own, with their own memory
    allowance and spill directory.
    """
    global _read_con
    if _read_con is None:
        _read_con = connect(config.DATA_DIR / "_tmp")
    return _read_con


def require_tables(tables, purpose):
    """Raise if any table `purpose` reads is missing, naming how to get it."""
    missing = [t for t in tables if table_size(config.table_dir(t)) == 0]
    if not missing:
        return
    listed = ", ".join(missing)
    raise RuntimeError(
        f"{purpose} needs {listed}, which is not in {config.DATA_DIR}. "
        f"Get it with oam.download(), or build it with oam.build()."
    )


def connect(tmp_dir, memory_limit=None, threads=None, httpfs=False):
    """A DuckDB connection configured to spill to tmp_dir instead of dying."""
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    if httpfs:
        con.execute("LOAD httpfs;")
    con.execute(f"SET memory_limit='{memory_limit or config.MEMORY_LIMIT}';")
    con.execute(f"SET threads={threads or config.THREADS};")
    con.execute(f"SET temp_directory='{tmp_dir.as_posix()}';")
    con.execute("SET preserve_insertion_order=false;")
    # DuckDB's progress bar writes carriage returns straight to the terminal, which
    # corrupts piped output and run logs. The build reports its own progress.
    con.execute("SET enable_progress_bar=false;")
    return con


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------

def _remove(path):
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink()


def copy_parquet(con, select_sql, target, partition_by=None):
    """Write a query result to `target`, publishing it only once the write succeeds.

    A target ending in .parquet becomes one file; anything else becomes a directory
    of Parquet files, so a caller can read it with a single **/*.parquet glob
    whether or not it is partitioned. Returns the number of rows written.

    The result is built at a temp path beside the target and moved into place with
    os.replace, so an interrupted run never leaves a half-written table behind.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp{os.getpid()}-{threading.get_ident()}")
    _remove(tmp)

    single_file = partition_by is None and target.suffix == ".parquet"
    options = ["FORMAT PARQUET", "COMPRESSION ZSTD"]
    if partition_by:
        options += [f"PARTITION_BY ({partition_by})", "OVERWRITE_OR_IGNORE"]
        destination = tmp
    elif single_file:
        options.append("ROW_GROUP_SIZE 1000000")
        destination = tmp
    else:
        # An unpartitioned COPY writes a single file, so give it one inside the dir.
        options.append("ROW_GROUP_SIZE 1000000")
        tmp.mkdir(parents=True, exist_ok=True)
        destination = tmp / "part-0.parquet"

    con.execute(f"COPY ({select_sql}) TO '{destination}' ({', '.join(options)})")

    read = tmp.as_posix() if single_file else (tmp / "**" / "*.parquet").as_posix()
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{read}')").fetchone()[0]

    _remove(target)
    os.replace(tmp, target)
    return rows


def clean_stale_tmp(*dirs):
    """Drop uncommitted temp outputs left behind by a killed run."""
    removed = 0
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for leftover in d.rglob(".*.tmp*"):
            _remove(leftover)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

class FileCheckpoint:
    """Append-only record of source files already processed (build step 1).

    One JSON object per line, flushed and fsynced on write, so a hard kill costs at
    most the file in flight. A truncated final line is skipped on reload.
    """

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.completed_path = self.dir / "completed.jsonl"
        self.failed_path = self.dir / "failed.jsonl"
        self.lock = threading.Lock()
        self.completed = {}
        if self.completed_path.exists():
            for line in self.completed_path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.completed[rec["key"]] = rec

    def record(self, rec):
        with self.lock:
            self.completed[rec["key"]] = rec
            self._append(self.completed_path, rec)

    def record_failure(self, key, error):
        with self.lock:
            self._append(self.failed_path, {
                "key": key,
                "error": str(error)[:500],
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })

    @staticmethod
    def _append(path, rec):
        with path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def total(self, field):
        return sum(r.get(field, 0) for r in self.completed.values())


class StageCheckpoint:
    """Which build stages are materialized, and under which configuration (step 2).

    A stage counts as done only if its recorded config matches the current one, so
    changing a threshold invalidates exactly the stages that depend on it.
    """

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "stages.json"
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def record(self, table, rows, seconds, config_):
        self.data[table] = {
            "rows": rows,
            "seconds": round(seconds, 1),
            "config": config_,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self.path.open("w") as fh:
            json.dump(self.data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())

    def done(self, table, config_):
        rec = self.data.get(table)
        if not rec or rec.get("config") != config_:
            return False
        return table_size(config.table_dir(table)) > 0


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def table_size(path):
    path = Path(path)
    return sum(f.stat().st_size for f in path.rglob("*.parquet")) if path.is_dir() else 0


def human_bytes(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


def human_time(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
