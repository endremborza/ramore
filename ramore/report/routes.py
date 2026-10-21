"""Route templates: the shape a request path is counted under.

A template marks one path segment `{x}` and the rest of the path, zero or more segments, `{...x}`.
The table comes from the app: its API routes (`:x` / `*x` accepted) and its SvelteKit project,
whose pages also answer `__data.json` and whose build serves `/_app/`. A param with a matcher
(`[x=m]`) matches like a plain one, since the matcher is app code, but ranks after every
alternative without one.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from .. import config

UNKNOWN = "_unknown"
DATA_SUFFIX = "__data.json"
APP_ASSETS = "/_app/{...path}"

_PAGE_FILES = (
    "+page.svelte",
    "+page.ts",
    "+page.js",
    "+page.server.ts",
    "+page.server.js",
)
_ENDPOINT_FILES = ("+server.ts", "+server.js")
_SVELTE_PARAM_RE = re.compile(r"\[(\.\.\.)?(\w+)(=\w+)?\]")
_AXUM_PARAM_RE = re.compile(r"^([:*])(\w+)$")
_PARAM_RE = re.compile(r"\{(\.\.\.)?(\w+)(=\w+)?\}")
# Per-segment specificity, most specific first. A template's end ranks before a rest segment, so
# `/a` wins `/a` over `/a/{...x}`, and after every other segment, so `/{...x}/b` wins over `/{...x}`.
_LITERAL, _MIXED, _PARAMS, _PARAM, _MATCHER, _END, _REST = range(7)


class Kind(StrEnum):
    API = "api"
    PAGE = "page"
    DATA = "data"
    ENDPOINT = "endpoint"
    ASSET = "asset"


@dataclass(frozen=True)
class Route:
    template: str
    kind: Kind
    regex: re.Pattern
    rank: tuple


@dataclass(frozen=True)
class RouteTable:
    routes: tuple[Route, ...]

    def template(self, path: str) -> str:
        """The template a request path (with or without its query) is counted under."""
        if path.startswith(("http://", "https://")):
            path = urlsplit(path).path or "/"
        path = path.split("?", 1)[0]
        if not path.startswith("/"):
            return UNKNOWN
        for route in self.routes:
            if route.regex.match(path):
                return route.template
        return UNKNOWN

    def of_kind(self, kind: Kind) -> frozenset[str]:
        return frozenset(r.template for r in self.routes if r.kind == kind)

    def templates(self) -> frozenset[str]:
        return frozenset(r.template for r in self.routes)


_table: tuple[config.Config, RouteTable] | None = None


def table() -> RouteTable:
    """The loaded app's route table, built once per config."""
    global _table
    cfg = config.get()
    if _table is None or _table[0] is not cfg:
        _table = (cfg, build(cfg.api_routes, cfg.sveltekit))
        _check_named(_table[1], cfg)
    return _table[1]


def template(path: str) -> str:
    return table().template(path)


def collect_unmatched(paths) -> Counter:
    t = table()
    return Counter(p for p in paths if t.template(p) == UNKNOWN)


def build(api: tuple[str, ...], sveltekit: Path | None) -> RouteTable:
    kinds = {_from_axum(p): Kind.API for p in api}
    if sveltekit is not None:
        kinds |= sveltekit_routes(sveltekit)
    routes = [_route(t, k) for t, k in kinds.items()]
    return RouteTable(tuple(sorted(routes, key=lambda r: r.rank)))


def sveltekit_routes(project: Path) -> dict[str, Kind]:
    """Every template a SvelteKit project's `src/routes` tree and build answer, by kind."""
    routes_dir = project / "src" / "routes"
    out: dict[str, Kind] = {APP_ASSETS: Kind.ASSET}
    for kind, names in ((Kind.ENDPOINT, _ENDPOINT_FILES), (Kind.PAGE, _PAGE_FILES)):
        for name in names:
            for f in routes_dir.rglob(name):
                t = _from_sveltekit(f.parent.relative_to(routes_dir))
                out[t] = kind
                if kind == Kind.PAGE:
                    out[t.rstrip("/") + "/" + DATA_SUFFIX] = Kind.DATA
    return out


def _from_sveltekit(rel: Path) -> str:
    segs = [s for s in rel.parts if not (s.startswith("(") and s.endswith(")"))]
    for s in segs:
        if "[[" in s:
            raise ValueError(f"{rel}: optional params are not supported")
    return "/" + "/".join(_SVELTE_PARAM_RE.sub(r"{\1\2\3}", s) for s in segs)


def _from_axum(path: str) -> str:
    segs = []
    for s in path.split("/"):
        m = _AXUM_PARAM_RE.match(s)
        segs.append(s if m is None else f"{{{'...' if m[1] == '*' else ''}{m[2]}}}")
    return "/".join(segs)


def _route(template: str, kind: Kind) -> Route:
    pattern, rank = "", []
    for seg in template.strip("/").split("/") if template != "/" else []:
        params = list(_PARAM_RE.finditer(seg))
        if len(params) == 1 and params[0][1] and params[0][0] == seg:
            pattern += "(?:/.*)?"
            rank.append(_REST)
            continue
        if not params:
            rank.append(_LITERAL)
        elif any(p[3] for p in params):
            rank.append(_MATCHER)
        elif len(params) == 1 and params[0][0] == seg:
            rank.append(_PARAM)
        else:
            rank.append(
                _MIXED if sum(len(p[0]) for p in params) < len(seg) else _PARAMS
            )
        pattern += "/" + _segment_regex(seg, params)
    literal = len(_PARAM_RE.sub("", template))
    regex = re.compile("^" + (pattern or "/") + "/?$")
    rank_key = (*rank, _END, -literal, template)
    return Route(_PARAM_RE.sub(r"{\1\2}", template), kind, regex, rank_key)


def _segment_regex(seg: str, params: list[re.Match]) -> str:
    out, pos = "", 0
    for p in params:
        out += re.escape(seg[pos : p.start()]) + "[^/]+?"
        pos = p.end()
    return out + re.escape(seg[pos:])


def _check_named(t: RouteTable, cfg: config.Config) -> None:
    named = {"search": cfg.search, "login": cfg.login}
    for key, entry in named.items():
        if entry is not None and entry.route not in t.templates():
            raise ValueError(
                f"[{key}] route {entry.route!r} is not in the app's route table"
            )
