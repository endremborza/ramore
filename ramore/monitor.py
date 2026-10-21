"""`ramore monitor`: the site probed from outside every `PROBE_EVERY_S`, an incident mailed once
it lasts `SUSTAIN_S`, and its resolve; plus the published report's age, when `[monitor]` names the
report. Box-side numbers are the report's (`[alerts]`), not the monitor's."""

import datetime as dt
import json
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from . import MONITOR_PRODUCT, SITE_STATE, __version__, alert, config
from .units import REPORT_EVERY_H

PROBE_EVERY_S = 15
SUSTAIN_S = 120
PROBE_TIMEOUT_S = 10
REPORT_STALE_AFTER = dt.timedelta(hours=2 * REPORT_EVERY_H + 1)
USER_AGENT = f"{MONITOR_PRODUCT}/{__version__}"

_HREF = re.compile(r'href="([^"#]+)"')


def checks(mon: config.Monitor, rng: random.Random) -> list[alert.Check]:
    out = []
    home = _get(mon.url, mon.max_latency_s)
    out.append(alert.Check("home", home.ok, home.detail))
    links = same_site_links(mon.url, home.text)
    picked = rng.sample(links, min(mon.follow, len(links)))
    bad = [r.detail for r in (_get(u, mon.max_latency_s) for u in picked) if not r.ok]
    if home.ok and not links:
        bad.append(f"no same-site links on {mon.url}")
    out.append(alert.Check("links", not bad, "; ".join(bad) or f"{len(picked)} ok"))
    if mon.report_url:
        out.append(report_fresh(mon.report_url))
    return out


def same_site_links(base: str, html: str) -> list[str]:
    host = urlsplit(base).netloc
    urls = {urljoin(base, h) for h in _HREF.findall(html)}
    return sorted(u for u in urls if urlsplit(u).netloc == host and u != base)


def report_fresh(report_url: str) -> alert.Check:
    url = urljoin(report_url, SITE_STATE)
    r = _get(url, PROBE_TIMEOUT_S)
    try:
        last = dt.datetime.fromisoformat(json.loads(r.text)["last_run"])
    except (ValueError, KeyError) as e:
        return alert.Check("report", False, f"{r.detail}: {e!r}")
    age = dt.datetime.now(dt.timezone.utc) - last
    return alert.Check(
        "report", age <= REPORT_STALE_AFTER, f"last pull {last.isoformat()}, {age} ago"
    )


@dataclass(frozen=True)
class _Probe:
    ok: bool
    detail: str
    text: str = ""


def _get(url: str, max_latency_s: float) -> _Probe:
    """A 2xx whose headers arrive within `max_latency_s`."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as r:
            s = time.monotonic() - t0
            status, text = r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        s, status, text = time.monotonic() - t0, e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return _Probe(False, f"{url}: {e}")
    ok = 200 <= status < 300 and s <= max_latency_s
    return _Probe(ok, f"{url}: {status} in {s:.2f} s", text)


def main(*, once: bool = False, config_file: str = config.CONFIG_NAME) -> None:
    """Probe the site every PROBE_EVERY_S and mail incidents; --once probes once, prints the
    checks and mails nothing."""
    cfg = config.use(config.load(config_file))
    if cfg.monitor is None:
        raise SystemExit("the app's ramore.toml has no [monitor] section")
    rng = random.Random()
    if once:
        for c in checks(cfg.monitor, rng):
            print(f"{'ok  ' if c.ok else 'FAIL'} {c.key}: {c.detail}")
        return
    alert.mail_env()
    while True:
        started = time.time()
        events = alert.run_step("monitor", checks(cfg.monitor, rng), started, SUSTAIN_S)
        for e in events:
            print(f"{e.kind} {e.key}: {e.detail}")
        time.sleep(max(0.0, PROBE_EVERY_S - (time.time() - started)))
