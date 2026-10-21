import datetime as dt

import polars as pl

from ramore.report.archive import annotate_routes
from ramore.report.classify import (
    annotate_events,
    classify_sessions,
    is_bot_ua,
    is_browser_ua,
    referrer_domain,
    ua_family,
)
from ramore.report.parse import parse_lines
from ramore.report.sessions import assign_sessions

from .fixtures import ALL


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


def test_is_bot_ua():
    assert is_bot_ua("AhrefsBot/7.0")
    assert is_bot_ua("Mozilla/5.0 (compatible; GPTBot/1.0)")
    assert is_bot_ua("python-requests/2.31.0")
    assert not is_bot_ua("Mozilla/5.0 ... Chrome/124.0")


def test_is_browser_ua():
    assert is_browser_ua("Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0")
    assert is_browser_ua("Mozilla/5.0 (Macintosh) Safari/605.1.15")
    assert not is_browser_ua("AhrefsBot/7.0")
    assert not is_browser_ua("")


def test_referrer_domain():
    assert referrer_domain("https://www.google.com/search?q=x") == "google.com"
    assert referrer_domain("https://twitter.com/foo") == "twitter.com"
    assert referrer_domain("-") == ""
    assert referrer_domain("") == ""


def test_assign_sessions_basic():
    df, _ = parse_lines(ALL)
    df = assign_sessions(df)
    assert "session_id" in df.columns
    assert df["session_id"].is_not_null().all()
    assert df["session_id"].n_unique() == df.select(["addr", "ua"]).unique().height


def test_assign_sessions_idle_split():
    ts = [
        dt.datetime(2026, 4, 28, 10, 0, 0, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 4, 28, 11, 30, 0, tzinfo=dt.timezone.utc),
    ]
    df = pl.DataFrame(
        {
            "t": pl.Series(ts, dtype=pl.Datetime("us", "UTC")),
            "addr": ["1.2.3.4", "1.2.3.4"],
            "ua": ["Chrome", "Chrome"],
            "method": ["GET", "GET"],
            "path": ["/", "/"],
            "status": pl.Series([200, 200], dtype=pl.UInt16),
            "size": pl.Series([100, 100], dtype=pl.UInt32),
            "referrer": ["", ""],
            "rt": pl.Series([0.1, 0.1], dtype=pl.Float32),
            "uct": pl.Series([0.01, 0.01], dtype=pl.Float32),
            "uht": pl.Series([0.01, 0.01], dtype=pl.Float32),
            "urt": pl.Series([0.05, 0.05], dtype=pl.Float32),
            "cs": ["HIT", "HIT"],
        }
    )
    df = assign_sessions(df)
    assert df["session_id"].n_unique() == 2


def test_classify_known_bot_ua():
    df, _ = parse_lines(ALL)
    df = annotate_routes(df)
    df = assign_sessions(df)
    sessions = classify_sessions(df)
    gpt = sessions.filter(pl.col("ua").str.contains("GPTBot"))
    assert (gpt["bot_class"] == "bot_known").all()
    ahrefs = sessions.filter(pl.col("ua").str.contains("AhrefsBot"))
    assert (ahrefs["bot_class"] == "bot_known").all()
    py = sessions.filter(pl.col("ua").str.contains("python-requests"))
    assert (py["bot_class"] == "bot_known").all()


def test_classify_search_user_human_known():
    df, _ = parse_lines(ALL)
    df = annotate_routes(df)
    df = assign_sessions(df)
    sessions = classify_sessions(df)
    chrome = sessions.filter(pl.col("ua").str.contains("Chrome/124"))
    assert (chrome["bot_class"] == "human_likely").all()


def test_classify_empty_ua_is_bot():
    df, _ = parse_lines(ALL)
    df = annotate_routes(df)
    df = assign_sessions(df)
    sessions = classify_sessions(df)
    empty = sessions.filter(pl.col("ua") == "")
    assert (empty["bot_class"] == "bot_known").all()


def test_annotate_events_adds_columns():
    df, _ = parse_lines(ALL)
    df = annotate_routes(df)
    df = assign_sessions(df)
    sessions = classify_sessions(df)
    annotated = annotate_events(df, sessions)
    for c in ("ua_family", "bot_class", "referrer_domain"):
        assert c in annotated.columns
    assert (annotated["bot_class"] != "unknown").any()
