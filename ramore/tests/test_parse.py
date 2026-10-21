import math

import polars as pl

from ramore.report.parse import keep_live_hosts, parse_lines

from .fixtures import (
    ALL,
    LINE_429,
    LINE_BOT,
    LINE_EMPTY_CS,
    LINE_EMPTY_UA,
    LINE_GARBAGE,
    LINE_HOST_ALPHA,
    LINE_HOST_LIVE,
    LINE_HOST_RAWIP,
    LINE_HUMAN,
    LINE_MISSING_KEY,
    LINE_NO_UPSTREAM,
    LINE_RETRY,
    LINE_STATUS_000,
    LINE_TORN,
)


def test_parse_human():
    df, fail = parse_lines([LINE_HUMAN])
    assert fail == 0
    r = df.row(0, named=True)
    assert r["addr"] == "203.0.113.42"
    assert r["method"] == "GET"
    assert r["path"] == "/v1/names/authors?q=darwin"
    assert r["status"] == 200
    assert r["size"] == 4321
    assert r["cs"] == "MISS"
    assert r["host"] == "www.example.org"
    assert "Chrome" in r["ua"]
    assert r["referrer"] == "https://www.example.org/"
    assert math.isclose(r["urt"], 0.040, rel_tol=1e-3)


def test_parse_bot():
    df, fail = parse_lines([LINE_BOT])
    assert fail == 0
    assert df.row(0, named=True)["path"] == "/sitemap.xml"
    assert "GPTBot" in df.row(0, named=True)["ua"]
    assert df.row(0, named=True)["cs"] == "HIT"


def test_parse_empty_cs():
    df, fail = parse_lines([LINE_EMPTY_CS])
    assert fail == 0
    assert df.row(0, named=True)["cs"] == ""
    assert df.row(0, named=True)["path"] == "/favicon.ico"


def test_parse_no_upstream():
    df, fail = parse_lines([LINE_NO_UPSTREAM])
    assert fail == 0
    r = df.row(0, named=True)
    assert r["urt"] is None
    assert r["uct"] is None
    assert r["cs"] == "BYPASS"


def test_parse_429():
    df, _ = parse_lines([LINE_429])
    assert df.row(0, named=True)["status"] == 429
    assert df.row(0, named=True)["size"] == 0


def test_parse_empty_ua_referrer():
    df, _ = parse_lines([LINE_EMPTY_UA])
    r = df.row(0, named=True)
    assert r["ua"] == ""
    assert r["referrer"] == ""


def test_parse_upstream_retry_is_null():
    df, fail = parse_lines([LINE_RETRY])
    assert fail == 0
    assert df.row(0, named=True)["urt"] is None


def test_parse_status_000():
    df, fail = parse_lines([LINE_STATUS_000])
    assert fail == 0
    assert df.row(0, named=True)["status"] == 0


def test_parse_other_format_counted():
    df, fail = parse_lines([LINE_MISSING_KEY])
    assert fail == 1
    assert len(df) == 0


def test_parse_garbage_counted():
    df, fail = parse_lines([LINE_GARBAGE])
    assert fail == 1
    assert len(df) == 0


def test_parse_batch():
    df, fail = parse_lines([*ALL, LINE_GARBAGE, ""])
    assert fail == 1
    assert len(df) == len(ALL)
    assert df.schema["t"] == pl.Datetime("us", "UTC")


def test_parse_torn_line_dropped_without_leak():
    # A torn line with a client IP spliced into its time bracket must be counted as
    # a failure and dropped — not crash the batch, not leak the raw IP downstream.
    df, fail = parse_lines([LINE_HUMAN, LINE_TORN])
    assert fail == 1
    assert len(df) == 1
    assert "162.158.162.77" not in "".join(df["addr"].to_list())
    assert df.row(0, named=True)["path"] == "/v1/names/authors?q=darwin"


def test_parse_host_field():
    df, fail = parse_lines([LINE_HOST_LIVE, LINE_HOST_ALPHA])
    assert fail == 0
    assert df["host"].to_list() == ["www.example.org", "alpha.example.org"]


def test_keep_live_hosts():
    df, _ = parse_lines([LINE_HOST_LIVE, LINE_HOST_ALPHA, LINE_HOST_RAWIP, LINE_BOT])
    kept, n = keep_live_hosts(df)
    assert n == 2  # alpha vhost + raw-IP scanner both dropped
    assert "host" not in kept.columns  # not persisted into the archive
    assert kept["path"].to_list() == ["/v1/names/authors?q=darwin", "/sitemap.xml"]
