import datetime as dt

import polars as pl

from ramore import config
from ramore.report import archive

from .conftest import aged, annotated
from .fixtures import ALL


def test_write_events_idempotent():
    df = annotated(ALL)
    assert sum(archive.write_events(df).values()) == len(df)
    assert sum(archive.write_events(df).values()) == 0
    back = archive.read_hot()
    assert len(back) == len(df)
    assert set(archive.HOT_COLUMNS) <= set(back.columns)


def test_write_events_dedupes_within_batch():
    archive.write_events(annotated(ALL + ALL))
    assert len(archive.read_hot()) == len(ALL)


def test_compress_cold_moves_old_days():
    df = aged(ALL, archive.COLD_AFTER_DAYS + 10)
    archive.write_events(df)
    assert sum(archive.compress_cold(today=dt.date.today()).values()) == 1
    assert archive.read_hot().is_empty()
    cold = pl.concat(archive.read_days())
    assert len(cold) == len(df)
    assert not set(archive.FREE_TEXT_COLUMNS) & set(cold.columns)


def test_compress_cold_leaves_moved_days_alone():
    archive.write_events(aged(ALL, archive.COLD_AFTER_DAYS + 2))
    archive.compress_cold(today=dt.date.today())
    (moved,) = config.get().archive_cold_dir.rglob("*.parquet")
    inode = moved.stat().st_ino
    archive.write_events(aged(ALL, archive.COLD_AFTER_DAYS + 1))
    assert sum(archive.compress_cold(today=dt.date.today()).values()) == 1
    assert moved.stat().st_ino == inode


def test_compress_cold_rerun_after_interrupted_move():
    df = aged(ALL, archive.COLD_AFTER_DAYS + 10)
    archive.write_events(df)
    archive.compress_cold(today=dt.date.today())
    archive.write_events(df)
    archive.compress_cold(today=dt.date.today())
    assert len(pl.concat(archive.read_days())) == len(df)


def test_compress_cold_keeps_a_moved_day_s_rows():
    df = aged(ALL, archive.COLD_AFTER_DAYS + 10)
    half = len(df) // 2
    for part in (df.head(half), df.tail(len(df) - half)):
        archive.write_events(part)
        archive.compress_cold(today=dt.date.today())
    assert len(pl.concat(archive.read_days())) == len(df)
