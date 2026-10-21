"""Bot/human session classification.

Each rule aggregates one session's requests to a bool, kept as a column of the sessions table: a
hard rule makes the session `bot_known`; otherwise the soft rules' scores add up to `score`, which
`SOFT_HUMAN_THRESHOLD` and `SOFT_BOT_THRESHOLD` split into `human_likely`, `unknown` and
`bot_likely`. The classification page reads the rules (`HARD_RULES`, `SOFT_RULES`) and the UA
families (`UA_FAMILIES`) to explain them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Callable, Iterable
from urllib.parse import urlsplit

import polars as pl

from .. import MONITOR_PRODUCT, config
from . import routes
from .parse import map_distinct

BOT_ONLY_ROUTES = ("/robots.txt", "/sitemap*.xml")
SOFT_HUMAN_THRESHOLD = 3
SOFT_BOT_THRESHOLD = 0


class BotClass:
    BOT_KNOWN = "bot_known"
    BOT_LIKELY = "bot_likely"
    UNKNOWN = "unknown"
    HUMAN_LIKELY = "human_likely"


BOT_CLASS_ORDER: list[str] = [
    BotClass.BOT_KNOWN,
    BotClass.BOT_LIKELY,
    BotClass.UNKNOWN,
    BotClass.HUMAN_LIKELY,
]
HUMAN_CLASSES: set[str] = {BotClass.HUMAN_LIKELY}

BOT_CLASS_COLORS: dict[str, str] = {
    BotClass.BOT_KNOWN: "#f38ba8",
    BotClass.BOT_LIKELY: "#eba0ac",
    BotClass.UNKNOWN: "#9399b2",
    BotClass.HUMAN_LIKELY: "#94e2d5",
}


@dataclass(frozen=True)
class UAFamily:
    name: str
    pattern: re.Pattern
    is_bot: bool


def _rx(s: str) -> re.Pattern:
    return re.compile(s, re.IGNORECASE)


UA_FAMILIES: list[UAFamily] = [
    UAFamily(MONITOR_PRODUCT, _rx(re.escape(MONITOR_PRODUCT)), True),
    UAFamily("gptbot", _rx(r"GPTBot"), True),
    UAFamily("claudebot", _rx(r"ClaudeBot"), True),
    UAFamily("perplexitybot", _rx(r"PerplexityBot"), True),
    UAFamily("bytespider", _rx(r"Bytespider"), True),
    UAFamily("googlebot", _rx(r"Googlebot"), True),
    UAFamily("bingbot", _rx(r"Bingbot"), True),
    UAFamily("ahrefsbot", _rx(r"AhrefsBot"), True),
    UAFamily("semrushbot", _rx(r"SemrushBot"), True),
    UAFamily("mj12bot", _rx(r"MJ12bot"), True),
    UAFamily("duckduckbot", _rx(r"DuckDuckBot"), True),
    UAFamily("yandexbot", _rx(r"YandexBot"), True),
    UAFamily("facebookbot", _rx(r"facebookexternalhit"), True),
    UAFamily("whatsapp", _rx(r"whatsapp"), True),
    UAFamily("telegrambot", _rx(r"telegram"), True),
    UAFamily("python-script", _rx(r"python-(?:requests|urllib)"), True),
    UAFamily("curl", _rx(r"\bcurl\b"), True),
    UAFamily("wget", _rx(r"\bwget\b"), True),
    UAFamily("go-script", _rx(r"Go-http-client"), True),
    UAFamily("java-script", _rx(r"okhttp|Apache-HttpClient|java/"), True),
    UAFamily("libwww", _rx(r"libwww"), True),
    UAFamily("other-bot", _rx(r"\bbot\b|crawler|spider|slurp|mediapartners"), True),
    UAFamily("edge", _rx(r"Edg/"), False),
    UAFamily("chrome-mobile", _rx(r"Chrome/.*Mobile"), False),
    UAFamily("chrome", _rx(r"Chrome/"), False),
    UAFamily("firefox", _rx(r"Firefox/"), False),
    UAFamily("safari", _rx(r"Safari/.*Version/"), False),
    UAFamily("safari-webkit", _rx(r"Safari/"), False),
    UAFamily("opera", _rx(r"Opera|OPR/"), False),
]

_BOT_UA_RE = (
    "(?i)(" + "|".join(f.pattern.pattern for f in UA_FAMILIES if f.is_bot) + ")"
)
_BROWSER_TOKEN_RE = "(?i)(?:Chrome|Safari|Firefox|Edge|Opera|Vivaldi)"


def ua_family(ua: str) -> str:
    if not ua:
        return "empty"
    for fam in UA_FAMILIES:
        if fam.pattern.search(ua):
            return fam.name
    return "unknown"


def referrer_domain(ref: str) -> str:
    if not ref or ref == "-":
        return ""
    try:
        host = urlsplit(ref).netloc.lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def bot_routes() -> frozenset[str]:
    """The app's templates only crawlers request: those matching `BOT_ONLY_ROUTES`."""
    return frozenset(
        t
        for t in routes.table().templates()
        if any(fnmatchcase(t, g) for g in BOT_ONLY_ROUTES)
    )


@dataclass(frozen=True)
class Rule:
    """A session signal: `expr()` aggregates one session's requests to a bool. A soft rule adds
    `score`; a hard one (no score) makes the session `bot_known`."""

    name: str
    description: str
    expr: Callable[[], pl.Expr]
    score: int | None = None


def _ua() -> pl.Expr:
    return pl.col("ua").first().fill_null("")


def _hits(templates: Iterable[str]) -> pl.Expr:
    return pl.col("route_template").is_in(list(templates)).any()


def _searched() -> pl.Expr:
    search = config.get().search
    if search is None:
        return pl.lit(False)
    query = rf"[?&]{re.escape(search.param)}=[^&]"
    hit = (pl.col("route_template") == search.route) & pl.col("path").str.contains(
        query
    )
    return hit.any()


HARD_RULES: list[Rule] = [
    Rule("empty_ua", "User-Agent header missing or empty.", lambda: _ua() == ""),
    Rule(
        "bot_ua",
        "User-Agent matches a known bot/crawler/script family.",
        lambda: _ua().str.contains(_BOT_UA_RE),
    ),
    Rule(
        "robots_or_sitemap",
        f"Hit a bot-only route: {', '.join(BOT_ONLY_ROUTES)}.",
        lambda: _hits(bot_routes()),
    ),
    Rule(
        "all_HEAD",
        "Every request in the session uses the HEAD method.",
        lambda: (pl.col("method") == "HEAD").all(),
    ),
]

SOFT_RULES: list[Rule] = [
    Rule(
        "search_used",
        "Used the search route with a non-empty query parameter.",
        _searched,
        +2,
    ),
    Rule(
        "browser_ua",
        "User-Agent looks like a real browser (`Mozilla` + Chrome/Safari/Firefox/etc.).",
        lambda: (
            _ua().str.contains("Mozilla", literal=True)
            & _ua().str.contains(_BROWSER_TOKEN_RE)
        ),
        +1,
    ),
    Rule(
        "sveltekit_prefetch",
        "Session contains SvelteKit __data.json requests, indicating browser hover-prefetch activity.",
        lambda: _hits(routes.table().of_kind(routes.Kind.DATA)),
        +2,
    ),
    Rule(
        "referrer_present",
        "At least one request carries a non-empty Referer.",
        lambda: (pl.col("referrer").fill_null("") != "").any(),
        +1,
    ),
    Rule(
        "www_no_api",
        "The session made no request to an API route.",
        lambda: ~_hits(routes.table().of_kind(routes.Kind.API)),
        -2,
    ),
]

RULES = HARD_RULES + SOFT_RULES

SESSION_SCHEMA = {
    "session_id": pl.String,
    "addr": pl.String,
    "ua": pl.String,
    "ua_family": pl.String,
    "start": pl.Datetime("us", "UTC"),
    "end": pl.Datetime("us", "UTC"),
    "n_req": pl.Int64,
    "bot_class": pl.String,
    "score": pl.Int32,
    **{r.name: pl.Boolean for r in RULES},
}


def classify_sessions(df: pl.DataFrame) -> pl.DataFrame:
    """One row per session (`SESSION_SCHEMA`): its span and size, each rule, `score`, `bot_class`."""
    sess = df.group_by("session_id").agg(
        pl.col("addr").first(),
        pl.col("ua").first(),
        pl.col("t").min().alias("start"),
        pl.col("t").max().alias("end"),
        pl.len().cast(pl.Int64).alias("n_req"),
        *(r.expr().alias(r.name) for r in RULES),
    )
    score = pl.sum_horizontal(
        pl.when(pl.col(r.name)).then(r.score).otherwise(0) for r in SOFT_RULES
    ).cast(pl.Int32)
    bot_class = (
        pl.when(pl.any_horizontal(r.name for r in HARD_RULES))
        .then(pl.lit(BotClass.BOT_KNOWN))
        .when(pl.col("score") >= SOFT_HUMAN_THRESHOLD)
        .then(pl.lit(BotClass.HUMAN_LIKELY))
        .when(pl.col("score") <= SOFT_BOT_THRESHOLD)
        .then(pl.lit(BotClass.BOT_LIKELY))
        .otherwise(pl.lit(BotClass.UNKNOWN))
    )
    return (
        map_distinct(sess, "ua", ua_family, "ua_family")
        .with_columns(score.alias("score"))
        .with_columns(bot_class.alias("bot_class"))
        .select(pl.col(c).cast(t) for c, t in SESSION_SCHEMA.items())
    )


def annotate_events(df: pl.DataFrame, sessions: pl.DataFrame) -> pl.DataFrame:
    """`df` with each request's `ua_family`, `referrer_domain` and its session's `bot_class`."""
    df = df.drop("ua_family", "referrer_domain", "bot_class", strict=False)
    df = map_distinct(df, "ua", ua_family, "ua_family")
    df = map_distinct(df, "referrer", referrer_domain, "referrer_domain")
    return df.join(
        sessions.select("session_id", "bot_class"), on="session_id", how="left"
    ).with_columns(pl.col("bot_class").fill_null(BotClass.UNKNOWN))
