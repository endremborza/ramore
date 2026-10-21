import datetime as dt

import polars as pl

from ramore.report import archive

from .conftest import annotated
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
    df = annotated(ALL).with_columns(
        pl.col("t") - pl.duration(days=archive.COLD_AFTER_DAYS + 10)
    )
    archive.write_events(df)
    assert sum(archive.compress_cold(today=dt.date.today()).values()) == 1
    assert archive.read_hot().is_empty()
    cold = archive.read_cold(archive.COLD_COLUMNS)
    assert len(cold) == len(df)
    assert not set(archive.FREE_TEXT_COLUMNS) & set(cold.columns)
