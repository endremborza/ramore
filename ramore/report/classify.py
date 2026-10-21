"""Bot/human session classification.

Public surface (also used by the explainer page):
- `BotClass` constants and ordered classes.
- `UA_FAMILIES`, `HARD_RULES`, `SOFT_RULES` — introspectable rule data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from urllib.parse import urlsplit

import polars as pl

from .. import MONITOR_PRODUCT, config
from . import routes

# --- bot-class labels ----------------------------------------------------


class BotClass:
    BOT_KNOWN = "bot_known"
    BOT_LIKELY = "bot_likely"
    UNKNOWN = "unknown"
    HUMAN_LIKELY = "human_likely"
    HUMAN_KNOWN = "human_known"


BOT_CLASS_ORDER: list[str] = [
    BotClass.BOT_KNOWN,
    BotClass.BOT_LIKELY,
    BotClass.UNKNOWN,
    BotClass.HUMAN_LIKELY,
    BotClass.HUMAN_KNOWN,
]
HUMAN_CLASSES: set[str] = {BotClass.HUMAN_KNOWN, BotClass.HUMAN_LIKELY}
BOT_CLASSES: set[str] = {BotClass.BOT_KNOWN, BotClass.BOT_LIKELY}

BOT_CLASS_COLORS: dict[str, str] = {
    BotClass.BOT_KNOWN: "#f38ba8",
    BotClass.BOT_LIKELY: "#eba0ac",
    BotClass.UNKNOWN: "#9399b2",
    BotClass.HUMAN_LIKELY: "#94e2d5",
    BotClass.HUMAN_KNOWN: "#a6e3a1",
}


# --- UA family bucketing -------------------------------------------------


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

_UA_BROWSER_RE = re.compile(r"Chrome|Safari|Firefox|Edge|Opera|Vivaldi", re.IGNORECASE)

# Combined patterns for vectorized Polars matching.
_BOT_UA_RE = (
    "(?i)(" + "|".join(fam.pattern.pattern for fam in UA_FAMILIES if fam.is_bot) + ")"
)
_BROWSER_TOKEN_RE = "(?i)(?:" + _UA_BROWSER_RE.pattern + ")"


def ua_family(ua: str) -> str:
    if not ua:
        return "empty"
    for fam in UA_FAMILIES:
        if fam.pattern.search(ua):
            return fam.name
    return "unknown"


def is_bot_ua(ua: str) -> bool:
    if not ua:
        return False
    return any(fam.is_bot and fam.pattern.search(ua) for fam in UA_FAMILIES)


def is_browser_ua(ua: str) -> bool:
    if not ua:
        return False
    return bool(_UA_BROWSER_RE.search(ua)) and "Mozilla" in ua


# --- referrer ------------------------------------------------------------


def referrer_domain(ref: str) -> str:
    if not ref or ref == "-":
        return ""
    try:
        host = urlsplit(ref).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# --- route categories ---------------------------------------------------

BOT_ONLY_ROUTES = ("/robots.txt", "/sitemap*.xml")


def bot_routes() -> frozenset[str]:
    """The app's templates only crawlers request: those matching `BOT_ONLY_ROUTES`."""
    return frozenset(
        t
        for t in routes.table().templates()
        if any(fnmatchcase(t, g) for g in BOT_ONLY_ROUTES)
    )


def _is_search() -> pl.Expr:
    search = config.get().search
    if search is None:
        return pl.lit(False)
    query = rf"[?&]{re.escape(search.param)}=[^&]"
    return (pl.col("route_template") == search.route) & pl.col("path").str.contains(
        query
    )


# --- rule data structures -----------------------------------------------


@dataclass(frozen=True)
class HardRule:
    name: str
    side: str  # "bot" or "human"
    description: str
    expr: pl.Expr


@dataclass(frozen=True)
class SoftRule:
    name: str
    score: int
    description: str
    expr: pl.Expr


HARD_RULES: list[HardRule] = [
    HardRule(
        "empty_ua",
        "bot",
        "User-Agent header missing or empty.",
        pl.col("_empty_ua"),  # empty_ua
    ),
    HardRule(
        "bot_ua",
        "bot",
        "User-Agent matches a known bot/crawler/script family.",
        pl.col("_is_bot_ua"),  # bot_ua
    ),
    HardRule(
        "robots_or_sitemap",
        "bot",
        f"Hit a bot-only route: {', '.join(BOT_ONLY_ROUTES)}.",
        pl.col("_is_bot_route"),  # robots_or_sitemap
    ),
    HardRule(
        "all_HEAD",
        "bot",
        "Every request in the session uses the HEAD method.",
        pl.col("_all_head"),  # all_HEAD
    ),
]

SOFT_RULES: list[SoftRule] = [
    SoftRule(
        "search_used",
        +2,
        "Used the search route with a non-empty query parameter.",
        pl.col("_is_search"),  # search_used
    ),
    SoftRule(
        "browser_ua",
        +1,
        "User-Agent looks like a real browser (`Mozilla` + Chrome/Safari/Firefox/etc.).",
        pl.col("_is_browser_ua"),  # browser_ua
    ),
    SoftRule(
        "sveltekit_prefetch",
        +2,
        "Session contains SvelteKit __data.json requests, indicating browser hover-prefetch activity.",
        pl.col("_has_data"),  # sveltekit_prefetch
    ),
    SoftRule(
        "referrer_present",
        +1,
        "At least one request carries a non-empty Referer.",
        pl.col("_has_referrer"),  # referrer_present
    ),
    SoftRule(
        "www_no_api",
        -2,
        "The session made no request to an API route.",
        ~pl.col("_has_api"),  # www_no_api
    ),
]

SOFT_HUMAN_THRESHOLD = 3
SOFT_BOT_THRESHOLD = 0

# --- session classification (fully vectorized) ---------------------------

_SESSION_OUT_COLS = [
    "session_id",
    "addr",
    "ua",
    "ua_family",
    "start",
    "end",
    "n_req",
    "bot_class",
    "signals_json",
]

_SESSION_SCHEMA = {
    "session_id": pl.String,
    "addr": pl.String,
    "ua": pl.String,
    "ua_family": pl.String,
    "start": pl.Datetime("us", "UTC"),
    "end": pl.Datetime("us", "UTC"),
    "n_req": pl.Int64,
    "bot_class": pl.String,
    "signals_json": pl.String,
}


def classify_sessions(df: pl.DataFrame) -> pl.DataFrame:
    """One row per session_id with bot_class + signals_json."""
    if df.is_empty():
        return pl.DataFrame(schema=_SESSION_SCHEMA)

    # Per-event boolean flags.
    table = routes.table()
    route = pl.col("route_template")
    enriched = df.with_columns(
        [
            _is_search().alias("_is_search"),
            route.is_in(list(table.of_kind(routes.Kind.DATA))).alias("_is_data"),
            route.is_in(list(bot_routes())).alias("_is_bot_route"),
            route.is_in(list(table.of_kind(routes.Kind.API))).alias("_is_api"),
            pl.col("referrer").fill_null("").ne("").alias("_has_referrer"),
        ]
    )

    # One group_by pass for all session-level aggregates.
    sess = enriched.group_by("session_id").agg(
        [
            pl.col("ua").first(),
            pl.col("addr").first(),
            pl.col("t").min().alias("start"),
            pl.col("t").max().alias("end"),
            pl.len().cast(pl.Int64).alias("n_req"),
            pl.col("method").eq("HEAD").all().alias("_all_head"),
            pl.col("_is_bot_route").any(),
            pl.col("_is_search").any(),
            pl.col("_is_data").any().alias("_has_data"),
            pl.col("_has_referrer").any(),
            pl.col("_is_api").any().alias("_has_api"),
        ]
    )

    # Session-level UA flags (vectorized regex, one per session).
    ua = pl.col("ua").fill_null("")
    sess = sess.with_columns(
        [
            ua.eq("").alias("_empty_ua"),
            ua.str.contains(_BOT_UA_RE).alias("_is_bot_ua"),
            (
                ua.str.contains("Mozilla", literal=True)
                & ua.str.contains(_BROWSER_TOKEN_RE)
            ).alias("_is_browser_ua"),
            ua.map_elements(ua_family, return_dtype=pl.String).alias("ua_family"),
        ]
    )

    # Named boolean columns for each rule (drives score, bot_class, and signals).
    sess = sess.with_columns(
        [rule.expr.alias(rule.name) for rule in HARD_RULES]
        + [rule.expr.alias(rule.name) for rule in SOFT_RULES]
    )

    # Score and bot_class.
    score = pl.sum_horizontal(
        [pl.when(pl.col(r.name)).then(r.score).otherwise(0) for r in SOFT_RULES]
    )
    _bot_cols = [pl.col(r.name) for r in HARD_RULES if r.side == "bot"]
    hard_bot = pl.any_horizontal(_bot_cols) if _bot_cols else pl.lit(False)
    bot_class = (
        pl.when(hard_bot)
        .then(pl.lit(BotClass.BOT_KNOWN))
        .when(score >= SOFT_HUMAN_THRESHOLD)
        .then(pl.lit(BotClass.HUMAN_LIKELY))
        .when(score <= SOFT_BOT_THRESHOLD)
        .then(pl.lit(BotClass.BOT_LIKELY))
        .otherwise(pl.lit(BotClass.UNKNOWN))
    )
    sess = sess.with_columns([bot_class.alias("bot_class"), score.alias("_score")])

    # signals_json: lightweight per-session dict lookup from pre-computed booleans.
    _rule_names = [r.name for r in HARD_RULES + SOFT_RULES]
    sess = sess.with_columns(
        pl.struct(_rule_names + ["_score"])
        .map_elements(_signals_from_struct, return_dtype=pl.String)
        .alias("signals_json")
    )

    return sess.select(_SESSION_OUT_COLS)


def _signals_from_struct(s: dict) -> str:
    sigs = []
    for rule in HARD_RULES:
        if s[rule.name]:
            sigs.append(f"hard:{rule.name}")
    for rule in SOFT_RULES:
        if s[rule.name]:
            sigs.append(f"soft:{'+' if rule.score > 0 else ''}{rule.score} {rule.name}")
    sigs.append(f"soft:score={s['_score']}")
    return json.dumps(sigs)


def annotate_events(df: pl.DataFrame, sessions: pl.DataFrame) -> pl.DataFrame:
    """Add ua_family, bot_class, referrer_domain to event rows."""
    if df.is_empty():
        return df.with_columns(
            [
                pl.lit(None).cast(pl.String).alias("ua_family"),
                pl.lit(None).cast(pl.String).alias("bot_class"),
                pl.lit(None).cast(pl.String).alias("referrer_domain"),
            ]
        )

    clean = df.drop(
        [c for c in ["ua_family", "bot_class", "referrer_domain"] if c in df.columns]
    )

    # Compute per-unique-value lookups to avoid per-row Python calls.
    ua_lookup = (
        clean.select("ua")
        .unique()
        .with_columns(
            pl.col("ua")
            .map_elements(ua_family, return_dtype=pl.String)
            .alias("ua_family")
        )
    )
    ref_lookup = (
        clean.select("referrer")
        .unique()
        .with_columns(
            pl.col("referrer")
            .map_elements(referrer_domain, return_dtype=pl.String)
            .alias("referrer_domain")
        )
    )

    return (
        clean.join(ua_lookup, on="ua", how="left")
        .join(ref_lookup, on="referrer", how="left")
        .join(sessions.select(["session_id", "bot_class"]), on="session_id", how="left")
        .with_columns(pl.col("bot_class").fill_null(BotClass.UNKNOWN))
    )
