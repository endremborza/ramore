import datetime as dt

import polars as pl

from ramore.report import archive
from ramore.report.archive import COLD_COLUMNS
from ramore.report.parse import parse_lines

from .fixtures import ALL


def test_write_events_idempotent():
    df, _ = parse_lines(ALL)
    df = archive.annotate_routes(df)
    n1 = archive.write_events(df)
    n2 = archive.write_events(df)
    assert sum(n1.values()) == len(df)
    assert sum(n2.values()) == 0

    all_back = archive.read_hot()
    assert len(all_back) == len(df)
    assert (all_back["route_template"] != "_unknown").any()


def test_write_events_dedupes_within_batch():
    df, _ = parse_lines(ALL + ALL)
    df = archive.annotate_routes(df)
    archive.write_events(df)
    all_back = archive.read_hot()
    assert len(all_back) == len(ALL)


def test_compress_cold_skips_unannotated():
    df, _ = parse_lines(ALL)
    df = archive.annotate_routes(df)
    df = df.with_columns((pl.col("t") - pl.duration(days=100)).alias("t"))
    archive.write_events(df)
    result = archive.compress_cold(today=dt.date.today())
    assert any("skipped" in k for k in result)
    assert len(archive.read_hot()) == len(df)


def test_compress_cold_compacts_when_annotated():
    df, _ = parse_lines(ALL)
    df = archive.annotate_routes(df)
    df = df.with_columns((pl.col("t") - pl.duration(days=100)).alias("t"))
    df = df.with_columns(
        [
            pl.lit("s0").alias("session_id"),
            pl.lit("test").alias("ua_family"),
            pl.lit("unknown").alias("bot_class"),
            pl.lit("").alias("referrer_domain"),
        ]
    )
    archive.write_events(df)
    n_before = len(archive.read_hot())
    result = archive.compress_cold(today=dt.date.today())
    assert sum(v for k, v in result.items() if "skipped" not in k) >= 1
    assert len(archive.read_hot()) == 0
    cold = archive.read_cold()
    assert set(COLD_COLUMNS).issubset(set(cold.columns))
    assert len(cold) == n_before
