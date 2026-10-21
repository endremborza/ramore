"""The app ramore reports on, read from the app's `ramore.toml`.

Relative paths in the file resolve against the file's directory, the app's repo. A command loads
the file once (`load`, then `use`); every module reads the loaded config through `get`.
"""

from __future__ import annotations

import ast
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "ramore.toml"
DEFAULT_LOG = "/var/log/nginx/access.log"
DEFAULT_BRANCH = "gh-pages"
DEFAULT_REMOTE = "origin"


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
class Config:
    name: str
    root: Path
    store: Path
    ssh_host: str
    log_path: str
    hosts: frozenset[str]
    api_routes: tuple[str, ...]
    sveltekit: Path | None
    search: Search | None
    login: Login | None
    site_include: dict[str, Path]
    ghpages_worktree: Path
    ghpages_branch: str
    ghpages_remote: str

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
            self.run_logs_dir,
        )

    def ensure_dirs(self) -> None:
        for d in self.store_dirs:
            d.mkdir(parents=True, exist_ok=True)


_current: Config | None = None


def load(path: Path | str = CONFIG_NAME) -> Config:
    path = Path(path).resolve()
    raw = tomllib.loads(path.read_text())
    root = path.parent
    source, routes, publish = raw["source"], raw.get("routes", {}), raw["publish"]
    return Config(
        name=raw["name"],
        root=root,
        store=root / raw["store"],
        ssh_host=source["ssh"],
        log_path=source.get("log", DEFAULT_LOG),
        hosts=frozenset(h.lower() for h in source["hosts"]),
        api_routes=_python_const(root, routes["api"]) if "api" in routes else (),
        sveltekit=root / routes["sveltekit"] if "sveltekit" in routes else None,
        search=Search(**raw["search"]) if "search" in raw else None,
        login=Login(**raw["login"]) if "login" in raw else None,
        site_include={
            name: root / rel
            for name, rel in raw.get("site", {}).get("include", {}).items()
        },
        ghpages_worktree=Path(publish["worktree"]),
        ghpages_branch=publish.get("branch", DEFAULT_BRANCH),
        ghpages_remote=publish.get("remote", DEFAULT_REMOTE),
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
