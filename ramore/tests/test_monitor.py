import dataclasses
import datetime as dt
import json
import random
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ramore import SITE_STATE, config, monitor, units


@pytest.fixture
def site(tmp_path: Path):
    """A static site on localhost; yields its base URL."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield tmp_path, f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


def test_same_site_links():
    html = (
        '<a href="./authors/a1">x</a><a href="/about">y</a><a href="#top">z</a>'
        '<a href="https://elsewhere.org/p">w</a><a href="https://www.x.org/">home</a>'
    )
    assert monitor.same_site_links("https://www.x.org/", html) == [
        "https://www.x.org/about",
        "https://www.x.org/authors/a1",
    ]


def test_checks_follow_links_and_name_the_failure(site):
    root, url = site
    (root / "index.html").write_text(
        '<a href="./ok.html"></a><a href="./gone.html"></a>'
    )
    (root / "ok.html").write_text("ok")
    mon = config.Monitor(url=url, follow=2, max_latency_s=5)
    home, links = monitor.checks(mon, random.Random(0))
    assert home.ok
    assert not links.ok and "gone.html: 404" in links.detail


def test_report_freshness(site):
    root, url = site
    now = dt.datetime.now(dt.timezone.utc)
    (root / SITE_STATE).write_text(json.dumps({"last_run": now.isoformat()}))
    assert monitor.report_fresh(url).ok
    stale = now - monitor.REPORT_STALE_AFTER - dt.timedelta(minutes=1)
    (root / SITE_STATE).write_text(json.dumps({"last_run": stale.isoformat()}))
    assert not monitor.report_fresh(url).ok
    (root / SITE_STATE).unlink()
    assert not monitor.report_fresh(url).ok


def test_units_render(cfg: config.Config):
    cfg = dataclasses.replace(cfg, name="Example App")
    files = units.render(cfg, Path("/app/ramore.toml"), "/bin/ramore", True, True)
    assert set(files) == {
        "ramore-report-example-app.service",
        "ramore-report-example-app.timer",
        "ramore-monitor-example-app.service",
    }
    report = files["ramore-report-example-app.service"]
    assert "ExecStart=/bin/ramore report --config-file /app/ramore.toml" in report
    assert f"MemoryMax={units.REPORT_MEMORY_MAX}" in report
    assert f"00/{units.REPORT_EVERY_H}:" in files["ramore-report-example-app.timer"]
    assert "Restart=always" in files["ramore-monitor-example-app.service"]
