from ramore.report import aggregate, archive

from .conftest import annotated
from .fixtures import ALL


def test_aggregate_consistency():
    df = annotated(ALL)
    archive.write_events(df)
    assert aggregate.rebuild()["rows"] == len(df)
    for level in aggregate.LEVELS:
        assert aggregate.load(level)["n"].sum() == len(df)


def test_aggregate_empty():
    assert aggregate.rebuild()["rows"] == 0
    assert aggregate.load("hourly").is_empty()
