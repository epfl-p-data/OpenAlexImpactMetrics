"""OAM — are these papers cited more or less than comparable papers?

    import oam

    oam.download()
    oam.aggregate(ids, by="publication_year")       # MNCS, top 10%, top 1%

`ids` is any list of OpenAlex work ids, or the result of ``oam.resolve()``.
Building the data yourself, which is also how a different scope is published,
is ``oam.build()``.

Once the data is in place nothing here touches the network.

See README.md. docs/metrics.md explains the scores, docs/querying.md is the
query guide, and docs/building.md covers building and publishing a data release.
"""

import json

from . import config
from . import metrics as _metrics
from . import report as _report
from . import resolve_store as _resolve_store
from . import store as _store
from .bundle import download, export, installed
from .checks import report as check
from .config import set_data_dir
from .query import aggregate, explain, get_metrics, work_ids
from .report import print_profile as profile
from .resolver import dimension, resolve, resolve_dois

__version__ = "0.1.0"

__all__ = [
    "aggregate", "build", "check", "dimension", "download", "explain", "export",
    "get_metrics", "profile", "resolve", "resolve_dois", "set_data_dir", "status",
    "work_ids", "config",
]

_STEPS = ("store", "metrics", "resolve")


def build(reset=False, steps=None, workers=8, include_xpack=False,
          refresh_manifest=False, limit=None, smallest=False, stage=None):
    """Build the store (1), the metric layers (2) and the resolve warehouse (3).

    Idempotent and resumable: an interrupted run continues, and a finished build
    revalidates and returns. A new OpenAlex release, an edited scope, or a newer
    projection cannot be merged into an existing store. Pass reset=True, which
    deletes the selected steps and starts over (step 1 re-reads about 67 GiB).

    steps               ``"store"``, ``"metrics"``, ``"resolve"``, or a sequence
                        of them. None runs all three, in that order.
    workers             parallel snapshot downloads during step 1.
    limit, smallest     step 1 only, for a trial on a few files. Not a usable
                        warehouse.
    stage               step 2 only: rebuild the metric layers from this stage on.
    include_xpack       keep xpack works in the metric population.
    refresh_manifest    refetch the snapshot manifest. A new release invalidates
                        an existing store.
    """
    selected = _steps(steps)
    with _report.tee("build"):
        if reset:
            if "store" in selected:
                _store.reset()
            if "metrics" in selected:
                _metrics.reset()
            if "resolve" in selected:
                _resolve_store.reset()

        runs = {
            "store": lambda: _store.build(
                workers=workers, limit=limit, smallest=smallest,
                refresh_manifest=refresh_manifest),
            "metrics": lambda: _metrics.build(
                stage=stage, include_xpack=include_xpack),
            "resolve": lambda: _resolve_store.build(
                refresh_manifest=refresh_manifest),
        }
        first = True
        for name in _STEPS:
            if name not in selected:
                continue
            if not first:
                print()
            first = False
            if not runs[name]():
                return False
    return True


def _steps(steps):
    if steps is None:
        return _STEPS
    if isinstance(steps, str):
        steps = (steps,)
    selected = tuple(steps)
    unknown = [name for name in selected if name not in _STEPS]
    if unknown:
        raise ValueError(
            f"unknown build steps {unknown}; choose from {', '.join(_STEPS)}"
        )
    if not selected:
        raise ValueError("steps must name at least one of store, metrics, resolve")
    return selected


def status():
    """What is installed or built, how big it is, and which snapshot it is."""
    bundle = installed()
    info = {
        "data_dir": str(config.DATA_DIR),
        "bundle": bundle,
        "store": _store.status(),
        "metrics": _metrics.status(),
        "resolve": _resolve_store.status(),
    }
    print(f"data: {info['data_dir']}")
    if bundle:
        print(f"bundle: snapshot {bundle['snapshot']}\n")
    elif config.STORE_STATE_PATH.is_file():
        date = json.loads(config.STORE_STATE_PATH.read_text()).get("date")
        print(f"bundle: local build, snapshot {date}\n")
    else:
        print("bundle: none\n")
    _store.print_status()
    print()
    _metrics.print_status()
    print()
    _resolve_store.print_status()
    return info
