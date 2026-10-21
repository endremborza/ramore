import datetime as dt

import polars as pl

from ramore.report.archive import annotate_routes
from ramore.report.classify import (
    RULES,
    SESSION_SCHEMA,
    classify_sessions,
    referrer_domain,
    ua_family,
)
from ramore.report.parse import parse_lines
from ramore.report.sessions import SESSION_IDLE_MIN, assign_sessions

from .conftest import annotated
from .fixtures import ALL


def _sessions() -> pl.DataFrame:
    return classify_sessions(assign_sessions(annotate_routes(parse_lines(ALL)[0])))


def _by_ua(sessions: pl.DataFrame, fragment: str) -> dict:
    return sessions.filter(pl.col("ua").str.contains(fragment, literal=True)).row(
        0, named=True
    )


def test_ua_family_buckets():
    assert ua_family("") == "empty"
    assert ua_family("Mozilla/5.0 ... Chrome/124.0") == "chrome"
    assert (
        ua_family("Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)")
        == "gptbot"
    )
    assert ua_family("AhrefsBot/7.0") == "ahrefsbot"
    assert ua_family("python-requests/2.31.0") == "python-script"
    assert ua_family("Mozilla/5.0 ... Firefox/124.0") == "firefox"


def test_referrer_domain():
    assert referrer_domain("https://www.google.com/search?q=x") == "google.com"
    assert referrer_domain("https://twitter.com/foo") == "twitter.com"
    assert referrer_domain("-") == ""
    assert referrer_domain("") == ""


def test_assign_sessions_per_address_and_ua():
    df = assign_sessions(parse_lines(ALL)[0])
    assert df["session_id"].is_not_null().all()
    assert df["session_id"].n_unique() == df.select("addr", "ua").n_unique()


def test_assign_sessions_idle_split():
    t0 = dt.datetime(2026, 4, 28, 10, tzinfo=dt.timezone.utc)
    gap = dt.timedelta(minutes=SESSION_IDLE_MIN + 1)
    df = pl.DataFrame(
        {
            "t": pl.Series(
                [t0, t0 + dt.timedelta(minutes=1), t0 + gap * 2],
                dtype=pl.Datetime("us", "UTC"),
            ),
            "addr": ["1.2.3.4"] * 3,
            "ua": ["Chrome"] * 3,
        }
    )
    assert assign_sessions(df)["session_id"].n_unique() == 2


def test_rules_are_columns():
    sessions = _sessions()
    assert sessions.schema == pl.Schema(SESSION_SCHEMA)
    chrome = _by_ua(sessions, "Chrome/124")
    assert chrome["browser_ua"] and chrome["search_used"] and chrome["referrer_present"]
    assert not chrome["www_no_api"] and not any(
        chrome[r.name] for r in RULES if r.score is None
    )
    assert chrome["score"] == sum(r.score for r in RULES if r.score and chrome[r.name])


def test_hard_rules_make_known_bots():
    sessions = _sessions()
    for fragment, rule in (
        ("GPTBot", "bot_ua"),
        ("AhrefsBot", "bot_ua"),
        ("python-requests", "bot_ua"),
    ):
        s = _by_ua(sessions, fragment)
        assert s[rule] and s["bot_class"] == "bot_known"
    empty = sessions.filter(pl.col("ua") == "").row(0, named=True)
    assert empty["empty_ua"] and empty["bot_class"] == "bot_known"


def test_search_user_is_human_likely():
    assert _by_ua(_sessions(), "Chrome/124")["bot_class"] == "human_likely"


def test_annotate_events_adds_columns():
    df = annotated(ALL)
    assert {"ua_family", "bot_class", "referrer_domain"} <= set(df.columns)
    assert (df["bot_class"] != "unknown").any()
