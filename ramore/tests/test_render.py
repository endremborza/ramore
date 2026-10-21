import dataclasses
import datetime as dt
import json
import re

import polars as pl

from ramore import SITE_STATE, config, demo
from ramore.demo import LOGIN
from ramore.report import aggregate, metrics, run, state
from ramore.report.render import PAGES, render_all
from ramore.report.render.landing import Card, kpis

IP = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
SCRIPT_PATH = "/<script>alert(1)</script>"


def _populate() -> None:
    """The last day of the demo's traffic and box, with a scanner's script tag for a path."""
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=1)
    lines = demo.traffic(start, end, visitors=20)
    lines.append(json.dumps({**json.loads(lines[-1]), "path": SCRIPT_PATH}))
    with run.run_record() as rec:
        run.ingest(lines, state.load(), rec)
    metrics.write(metrics.parse_lines(demo.samples(start, end))[0])
    aggregate.rebuild()


def _write_failed_run(ts: str, ip: str) -> None:
    rec = {
        "ts": ts,
        "status": "error",
        "error": f"InvalidOperationError('... {ip} - - [05/Jul/2026:01:51:14 ...')",
        "trace": f'File ".../parse.py", line 35\n... {ip} ...',
    }
    (config.get().run_logs_dir / "run-2026-07-05.log").write_text(
        json.dumps(rec) + "\n"
    )


def test_render_local_mode():
    _populate()
    ctx = render_all("local")
    for page in ("index", "traffic", "performance", "errors", "classification", "box"):
        assert "<title>" in (ctx.out_dir / f"{page}.html").read_text()
    for page in ("sessions/index", "runs/index"):
        assert (ctx.out_dir / f"{page}.html").exists()
    assert (ctx.out_dir / "assets" / "style.css").exists()
    assert set(json.loads((ctx.out_dir / SITE_STATE).read_text())) == {
        "last_run",
        "last_event_ts",
    }


def test_box_page_is_local_only():
    _populate()
    local = render_all("local").out_dir
    assert demo.UNITS[0] in (local / "box.html").read_text()
    assert 'href="box.html"' in (local / "index.html").read_text()
    public = render_all("public").out_dir
    assert not (public / "box.html").exists()
    assert 'href="box.html"' not in (public / "index.html").read_text()
    config.use(dataclasses.replace(config.get(), metrics=None))
    assert (
        'href="box.html"'
        not in (render_all("local").out_dir / "index.html").read_text()
    )


def test_public_site_has_no_address_and_escapes_paths():
    _populate()
    _write_failed_run("2026-07-05T02-10-01", "162.158.162.77")
    ctx = render_all("public")
    for f in ctx.out_dir.rglob("*.html"):
        text = f.read_text()
        assert not IP.search(text), f"address in {f}"
        assert "<script>alert" not in text, f"unescaped path in {f}"
    assert len(PAGES) <= len(list(ctx.out_dir.rglob("*.html")))


def test_local_site_keeps_run_errors():
    _populate()
    _write_failed_run("2026-07-05T02-10-01", "162.158.162.77")
    detail = render_all("local").out_dir / "runs" / "2026-07-05T02-10-01.html"
    assert "162.158.162.77" in detail.read_text()


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


def test_kpis_count_login_outcomes():
    rows = [
        (LOGIN.route, LOGIN.ok_status),
        (LOGIN.route, LOGIN.ok_status),
        (LOGIN.route, 400),
        (LOGIN.route, 308),
        ("/about", LOGIN.ok_status),
    ]
    k = kpis(_kpi_events(rows, dt.datetime.now(dt.timezone.utc)), pl.DataFrame(), 1)
    assert Card(LOGIN.label, "2", "1 failed") in k.cards


def test_kpis_count_sessions_active_in_window():
    now = dt.datetime.now(dt.timezone.utc)
    events = _kpi_events([("/", 200)], now)
    sessions = pl.DataFrame(
        {
            "end": [now, now - dt.timedelta(minutes=30), now - dt.timedelta(hours=2)],
            "bot_class": ["human_likely", "bot_likely", "bot_likely"],
        }
    )
    assert Card("Sessions", "2") in kpis(events, sessions, 1).cards
    day = kpis(events, sessions, 24)
    assert Card("Sessions", "3") in day.cards
    assert {s.cls: s.n for s in day.classes} == {"bot_likely": 2, "human_likely": 1}
