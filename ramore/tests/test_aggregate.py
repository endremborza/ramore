from ramore.report import aggregate, archive
from ramore.report.archive import annotate_routes
from ramore.report.classify import annotate_events, classify_sessions
from ramore.report.parse import parse_lines
from ramore.report.sessions import assign_sessions

from .fixtures import ALL


def test_aggregate_consistency():
    df, _ = parse_lines(ALL)
    df = annotate_routes(df)
    df = assign_sessions(df)
    sessions = classify_sessions(df)
    df = annotate_events(df, sessions)
    archive.write_events(df)
    result = aggregate.rebuild()
    assert result["rows"] == len(df)

    hourly = aggregate.load_hourly()
    daily = aggregate.load_daily()
    assert hourly["n"].sum() == len(df)
    assert daily["n"].sum() == len(df)


def test_aggregate_empty():
    result = aggregate.rebuild()
    assert result["rows"] == 0
    assert aggregate.load_hourly().is_empty()
