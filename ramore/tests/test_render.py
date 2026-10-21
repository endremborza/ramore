import dataclasses
import datetime as dt
import json
import re

import polars as pl

from ramore import SITE_STATE, config
from ramore.report import aggregate, archive, metrics
from ramore.report.archive import annotate_routes
from ramore.report.classify import annotate_events, classify_sessions
from ramore.report.parse import parse_lines
from ramore.report.render import render_all
from ramore.report.render.landing import _card, _kpi_block
from ramore.report.sessions import assign_sessions

from .conftest import LOGIN
from .fixtures import ALL


def _populate() -> None:
    df, _ = parse_lines(ALL)
    now_utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    base = now_utc.replace(minute=0, second=0) - dt.timedelta(hours=1)
    new_ts = [base + dt.timedelta(seconds=i) for i in range(len(df))]
    df = df.with_columns(pl.Series("t", new_ts, dtype=pl.Datetime("us", "UTC")))
    df = annotate_routes(df)
    df = assign_sessions(df)
    sessions = classify_sessions(df)
    aggregate.write_sessions(sessions)
    df = annotate_events(df, sessions)
    archive.write_events(df)
    aggregate.rebuild()


def test_render_local_mode():
    _populate()
    ctx = render_all("local")
    assert (ctx.out_dir / "index.html").exists()
    assert (ctx.out_dir / "traffic.html").exists()
    assert (ctx.out_dir / "performance.html").exists()
    assert (ctx.out_dir / "errors.html").exists()
    assert (ctx.out_dir / "sessions" / "index.html").exists()
    assert (ctx.out_dir / "classification.html").exists()
    assert (ctx.out_dir / "runs" / "index.html").exists()
    assert (ctx.out_dir / "assets" / "style.css").exists()
    landing = (ctx.out_dir / "index.html").read_text()
    assert "<title>" in landing
    site_state = json.loads((ctx.out_dir / SITE_STATE).read_text())
    assert set(site_state) == {"last_run", "last_event_ts"}


def test_box_page_is_local_only():
    config.use(
        dataclasses.replace(
            config.get(), metrics=config.Metrics(60, ("/",), ("app.service",))
        )
    )
    _populate()
    t = dt.datetime.now(dt.timezone.utc).timestamp()
    lines = [
        json.dumps({"t": t, "scope": "host", "name": "", "used": 6, "limit": 10}),
        json.dumps(
            {
                "t": t,
                "scope": "unit",
                "name": "app.service",
                "used": 5,
                "anon": 3,
                "fds": 9,
            }
        ),
    ]
    metrics.write(metrics.parse_lines(lines)[0])
    local = render_all("local").out_dir
    assert "app.service" in (local / "box.html").read_text()
    assert 'href="box.html"' in (local / "index.html").read_text()
    public = render_all("public").out_dir
    assert not (public / "box.html").exists()
    assert 'href="box.html"' not in (public / "index.html").read_text()


def _write_failed_run(ts: str, ip: str) -> None:
    rec = {
        "ts": ts,
        "status": "error",
        "error": f"InvalidOperationError('... {ip} - - [05/Jul/2026:01:51:14 ...')",
        "trace": f'File ".../parse.py", line 35\n... {ip} ...',
    }
    log = config.get().run_logs_dir / "run-2026-07-05.log"
    log.write_text(json.dumps(rec, default=str) + "\n")


def test_render_public_mode_anonymizes():
    _populate()
    # A prior run that failed on a torn line, echoing a raw IP into its error
    # text — the exact leak that surfaced on the public site.
    _write_failed_run("2026-07-05T02-10-01", "162.158.162.77")
    ctx = render_all("public")
    ip_re = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
    for f in ctx.out_dir.rglob("*.html"):
        text = f.read_text()
        assert not ip_re.search(text), (
            f"raw IP leaked in {f}: {ip_re.search(text).group(0)}"
        )


def test_render_local_keeps_run_errors():
    _populate()
    _write_failed_run("2026-07-05T02-10-01", "162.158.162.77")
    ctx = render_all("local")
    detail = (ctx.out_dir / "runs" / "2026-07-05T02-10-01.html").read_text()
    assert "162.158.162.77" in detail  # full detail retained locally


def _kpi_events(rows: list[tuple[str, int]], t: dt.datetime) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "t": [t] * len(rows),
            "route_template": [r for r, _ in rows],
            "status": pl.Series([s for _, s in rows], dtype=pl.UInt16),
            "cs": ["-"] * len(rows),
            "urt": pl.Series([0.1] * len(rows), dtype=pl.Float32),
        }
    )


def test_kpi_block_counts_callback_outcomes():
    rows = [
        (LOGIN.route, LOGIN.ok_status),
        (LOGIN.route, LOGIN.ok_status),
        (LOGIN.route, 400),
        (LOGIN.route, 308),
        ("/about", LOGIN.ok_status),
    ]
    events = _kpi_events(rows, dt.datetime.now(dt.timezone.utc))
    html = _kpi_block("1h", events, pl.DataFrame(), 1)["html"]
    assert _card(LOGIN.label, "2", "1 failed") in html


def test_kpi_block_counts_sessions_active_in_window():
    now = dt.datetime.now(dt.timezone.utc)
    events = _kpi_events([("/", 200)], now)
    sessions = pl.DataFrame(
        {
            "end": [now, now - dt.timedelta(minutes=30), now - dt.timedelta(hours=2)],
            "bot_class": ["human_likely", "bot_likely", "bot_likely"],
        }
    )
    assert _card("Sessions", "2") in _kpi_block("1h", events, sessions, 1)["html"]
    assert _card("Sessions", "3") in _kpi_block("24h", events, sessions, 24)["html"]
