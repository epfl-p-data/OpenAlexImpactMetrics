"""OAM — are these papers cited more or less than comparable papers?

    import oam

    oam.download()
    oam.aggregate(ids, by="publication_year")       # MNCS, top 10%, top 1%

`ids` is any list of OpenAlex work ids, or the result of ``oam.resolve()``.
Building the data yourself, which is also how a different scope is published,
is ``oam.build()``.

Once the data is in place nothing here touches the network.

See https://github.com/epfl-p-data/OpenAlexImpactMetrics#readme.
https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/metrics.md
explains the scores,
https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/querying.md
is the query guide, and
https://github.com/epfl-p-data/OpenAlexImpactMetrics/blob/main/docs/building.md
covers building and publishing a data release.
"""

import json
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from . import config
from . import metrics as _metrics
from . import report as _report
from . import resolve_store as _resolve_store
from . import store as _store
from .bundle import citation_links, download, export, export_edges, installed
from .checks import report as check
from .config import set_data_dir
from .query import aggregate, explain, get_metrics, work_ids
from .report import print_profile as profile
from .resolver import dimension, resolve, resolve_dois

def _package_version():
    """The version in pyproject.toml, via the installed metadata when there is one."""
    try:
        return version("openalex-metrics")
    except PackageNotFoundError:
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        match = re.search(r'(?m)^version = "([^"]+)"', text)
        if match is None:
            raise RuntimeError("pyproject.toml has no version")
        return match.group(1)


__version__ = _package_version()

__all__ = [
    "aggregate", "build", "check", "citation_links", "dimension", "download",
    "explain", "export", "export_edges", "get_metrics", "installed", "profile",
    "resolve", "resolve_dois",
    "set_data_dir", "status", "work_ids", "config",
]


def __dir__():
    return sorted(__all__ + ["__version__"])


_STEPS = ("store", "metrics", "resolve")


def build(reset=False, steps=None, workers=8, include_xpack=False,
          citation_window=None, exclude_self_citations=False,
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
    citation_window     step 2 only. None counts every citation up to the snapshot.
                        An integer n counts the publication year and the n - 1
                        years after it, so 4 is the publication year plus three.
    exclude_self_citations
                        step 2 only. Drop citations between papers that share an
                        OpenAlex author. The default keeps them.
    refresh_manifest    refetch the snapshot manifest. A new release invalidates
                        an existing store.
    """
    _metrics.validate_analysis(citation_window, exclude_self_citations)
    if installed():
        print("A downloaded archive holds one citation count and no store, "
              "so it cannot be rebuilt in place.")
        print("  Build into another directory with oam.set_data_dir(...).")
        return False
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
                stage=stage, include_xpack=include_xpack,
                citation_window=citation_window,
                exclude_self_citations=exclude_self_citations),
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
    """Print what is installed or built, how big it is, and which snapshot it is."""
    bundle = installed()
    print(f"data: {config.DATA_DIR}")
    if bundle:
        print(f"bundle: snapshot {bundle['snapshot']}, "
              f"format {bundle['format']}, readable by OAM {__version__}")
    elif config.STORE_STATE_PATH.is_file():
        date = json.loads(config.STORE_STATE_PATH.read_text()).get("date")
        print(f"bundle: local build, snapshot {date}")
    else:
        print("bundle: none")
    analysis = (bundle or {}).get("analysis") or _metrics.recorded_analysis()
    described = _metrics.describe_analysis(analysis)
    print(f"analysis: {described}\n" if described else "analysis: not recorded\n")
    _store.print_status()
    print()
    _metrics.print_status()
    print()
    _resolve_store.print_status()
