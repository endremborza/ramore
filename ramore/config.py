"""The app ramore reports on, read from the app's `ramore.toml`.

Relative paths in the file resolve against the file's directory, the app's repo. A file with
`extends = "<file>"` takes that file's keys and tables, each of its own replacing one whole; a key
set to `false` drops it (`publish = false`: a local-only variant). A command loads the file once
(`load`, then `use`); every module reads the loaded config through `get`.
"""

from __future__ import annotations

import ast
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "ramore.toml"
DEFAULT_BRANCH = "gh-pages"
DEFAULT_REMOTE = "origin"
DEFAULT_METRICS_INTERVAL_S = 60
DEFAULT_METRICS_MOUNTS = ("/",)


@dataclass(frozen=True)
class Search:
    """A search request: a hit on `route` with a non-empty `param` query parameter."""

    route: str
    param: str


@dataclass(frozen=True)
class Login:
    """A login: a hit on `route` answered with `ok_status`; a 4xx answer is a failed login."""

    route: str
    ok_status: int
    label: str


@dataclass(frozen=True)
class Metrics:
    """The box sampler: every `interval_s`, the box's memory, each mount's disk, and each unit
    whose name matches one of the `units` globs."""

    interval_s: int
    mounts: tuple[str, ...]
    units: tuple[str, ...]


@dataclass(frozen=True)
class Monitor:
    """`ramore monitor`'s probe: `url` and `follow` random same-site links found on it, each
    answered with a 2xx within `max_latency_s`; with `report_url`, the published report's age."""

    url: str
    follow: int = 2
    max_latency_s: float = 1.2
    report_url: str | None = None


@dataclass(frozen=True)
class Alerts:
    """Report-time thresholds on the latest box sample, each in percent of the scope's limit:
    the box's memory, a mount's disk, a unit's anon memory and its open files."""

    memory_pct: float = 95
    disk_pct: float = 90
    unit_anon_pct: float = 90
    unit_fds_pct: float = 80


@dataclass(frozen=True)
class Publish:
    """Where the public site is pushed: `branch` of the app repo's `remote`."""

    branch: str = DEFAULT_BRANCH
    remote: str = DEFAULT_REMOTE


@dataclass(frozen=True)
class Config:
    name: str
    root: Path
    store: Path
    ssh_host: str
    hosts: frozenset[str]
    api_routes: tuple[str, ...]
    sveltekit: Path | None
    search: Search | None
    login: Login | None
    metrics: Metrics | None
    monitor: Monitor | None
    alerts: Alerts
    site_include: dict[str, Path]
    publish: Publish | None

    @property
    def archive_dir(self) -> Path:
        return self.store / "archive"

    @property
    def archive_cold_dir(self) -> Path:
        return self.store / "archive-cold"

    @property
    def aggregates_dir(self) -> Path:
        return self.store / "aggregates"

    @property
    def site_local_dir(self) -> Path:
        return self.store / "site"

    @property
    def site_public_dir(self) -> Path:
        return self.store / "site-public"

    @property
    def metrics_dir(self) -> Path:
        return self.store / "metrics"

    @property
    def incidents_dir(self) -> Path:
        return self.store / "incidents"

    @property
    def run_logs_dir(self) -> Path:
        return self.store / "logs"

    @property
    def state_path(self) -> Path:
        return self.store / "state.json"

    @property
    def salts_path(self) -> Path:
        return self.store / "salts.json"

    @property
    def store_dirs(self) -> tuple[Path, ...]:
        return (
            self.archive_dir,
            self.archive_cold_dir,
            self.aggregates_dir,
            self.site_local_dir,
            self.site_public_dir,
            self.metrics_dir,
            self.incidents_dir,
            self.run_logs_dir,
        )

    def ensure_dirs(self) -> None:
        for d in self.store_dirs:
            d.mkdir(parents=True, exist_ok=True)


_current: Config | None = None


def load(path: Path | str = CONFIG_NAME) -> Config:
    path = Path(path).resolve()
    raw = tomllib.loads(path.read_text())
    if "extends" in raw:
        base = tomllib.loads((path.parent / raw.pop("extends")).read_text())
        raw = {k: v for k, v in (base | raw).items() if v is not False}
    root = path.parent
    source, routes = raw["source"], raw.get("routes", {})
    return Config(
        name=raw["name"],
        root=root,
        store=root / raw["store"],
        ssh_host=source["ssh"],
        hosts=frozenset(h.lower() for h in source["hosts"]),
        api_routes=_python_const(root, routes["api"]) if "api" in routes else (),
        sveltekit=root / routes["sveltekit"] if "sveltekit" in routes else None,
        search=Search(**raw["search"]) if "search" in raw else None,
        login=Login(**raw["login"]) if "login" in raw else None,
        metrics=_metrics(raw["metrics"]) if "metrics" in raw else None,
        monitor=Monitor(**raw["monitor"]) if "monitor" in raw else None,
        alerts=Alerts(**raw.get("alerts", {})),
        site_include={
            name: root / rel
            for name, rel in raw.get("site", {}).get("include", {}).items()
        },
        publish=Publish(**raw["publish"]) if "publish" in raw else None,
    )


def use(cfg: Config) -> Config:
    global _current
    _current = cfg
    return cfg


def get() -> Config:
    if _current is None:
        raise RuntimeError(
            "no ramore config loaded: `config.use(config.load(path))` first"
        )
    return _current


def _metrics(raw: dict) -> Metrics:
    return Metrics(
        interval_s=raw.get("interval_s", DEFAULT_METRICS_INTERVAL_S),
        mounts=tuple(raw.get("mounts", DEFAULT_METRICS_MOUNTS)),
        units=tuple(raw["units"]),
    )


def _python_const(root: Path, spec: str) -> tuple[str, ...]:
    """`file.py:NAME`: the literal tuple or list `NAME` is assigned in `file.py`."""
    rel, name = spec.split(":")
    tree = ast.parse((root / rel).read_text())
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == name
            and node.value is not None
        ):
            return tuple(ast.literal_eval(node.value))
    raise KeyError(f"{spec}: no literal assignment to {name}")
