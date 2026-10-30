import datetime as dt

from ramore.report import aggregate, archive

from .conftest import aged, annotated
from .fixtures import ALL


def test_aggregate_consistency():
    df = annotated(ALL)
    archive.write_events(df)
    assert aggregate.rebuild()["rows"] == len(df)
    for level in aggregate.LEVELS:
        assert aggregate.load(level)["n"].sum() == len(df)


def test_update_reads_a_day_already_cold():
    df = aged(ALL, archive.COLD_AFTER_DAYS + 10)
    written = archive.write_events(df)
    aggregate.rebuild()
    archive.compress_cold(today=dt.date.today())
    aggregate.update([dt.date.fromisoformat(d) for d in written])
    for level in aggregate.LEVELS:
        assert aggregate.load(level)["n"].sum() == len(df)


def test_aggregate_empty():
    assert aggregate.rebuild()["rows"] == 0
    assert aggregate.load("hourly").is_empty()
