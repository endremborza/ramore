"""`ramore demo`: an example app, synthetic days of its traffic and box samples, and its report.

The traffic is what a public site draws: human visits (pages, SvelteKit prefetches, API calls,
searches, logins, referrers), crawlers, link-preview bots, scripts, scanners, an uptime checker
and ramore's own monitor, with one 5xx incident. `traffic` and `samples` write the box's two logs
line for line, so the end-to-end test serves the same lines from a fake box.
"""

import datetime as dt
import json
import math
import random
from pathlib import Path

from . import MONITOR_PRODUCT, __version__, config
from .box import REQUEST_FIELDS
from .report import aggregate, metrics, reset, run, state

NAME = "Example"
SSH_HOST = "example-live"
HOSTS = ("www.example.org", "example.org", "api.example.org")
API_ROUTES = (
    "/v1/names/:etype",
    "/v1/trees/:root_type/:semantic_id",
    "/v1/works-intersect/*spec",
    "/v1/specs",
)
SVELTEKIT_DIRS = {
    "(stat)": "+page.svelte",
    "(stat)/about": "+page.svelte",
    "(stat)/[rootType]/table": "+page.svelte",
    "(stat)/[rootType]/[...semanticId]": "+page.svelte",
    "(game)/[game=quest]": "+page.svelte",
    "callback": "+server.ts",
    "robots.txt": "+server.ts",
    "sitemap.xml": "+server.ts",
    "sitemap-[n].xml": "+server.ts",
    "card/[rootType]/[...semanticId]/[kind].[ext=cardext]": "+server.ts",
}
SEARCH = config.Search(route="/v1/names/{etype}", param="q")
LOGIN = config.Login(route="/callback", ok_status=302, label="Logins")
UNITS = ("app-api.service", "app-web@1.service", "app-web@2.service", "nginx.service")
SAMPLE_EVERY_S = 300
PROBE_EVERY = dt.timedelta(minutes=5)
UPTIME_EVERY = dt.timedelta(minutes=5)
INCIDENT = dt.timedelta(minutes=25)

DEMO_DAYS = 14
DEMO_VISITORS = 300
# Other sessions per human visit.
CRAWLS, PREVIEWS, SCRIPTS, SCANS = 0.12, 0.05, 0.03, 0.08

_GB = 2**30
_ROOT_TYPES = ("authors", "institutions", "works", "sources", "topics")
_WORDS = (
    "darwin",
    "curie",
    "turing",
    "noether",
    "lovelace",
    "bohr",
    "hopper",
    "ramanujan",
)
_BROWSERS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
)
_REFERRERS = (
    "https://www.google.com/",
    "https://duckduckgo.com/",
    "https://news.ycombinator.com/",
    "https://www.reddit.com/r/AskAcademia/",
    "https://scholar.example.edu/links",
)
_CRAWLERS = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
)
_PREVIEWS = ("facebookexternalhit/1.1", "Twitterbot/1.0", "WhatsApp/2.25.1.1 A")
_SCRIPTS = ("python-requests/2.32.3", "curl/8.5.0", "Go-http-client/2.0")
_SCANS = (
    "/wp-login.php",
    "/.env",
    "/xmlrpc.php",
    "/phpmyadmin/",
    "/.git/config",
    "/admin",
)
_UPTIME = "Mozilla/5.0 (compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)"
_BOX_IP = "192.0.2.10"


def main(
    *,
    out: str = "ramore-demo",
    days: int = DEMO_DAYS,
    visitors: int = DEMO_VISITORS,
    seed: int = 0,
) -> None:
    """Write the example app into --out, --days of its traffic (--visitors human visits a day)
    and box samples up to now, and render its local and public report sites from a fresh store."""
    cfg = config.use(config.load(write_app(Path(out).resolve())))
    reset.wipe_local()
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days)
    with run.run_record() as rec:
        s = state.load()
        run.ingest(traffic(start, end, visitors, seed), s, rec)
        rec["metrics_rows"] = metrics.write(metrics.parse_lines(samples(start, end))[0])
        s.last_run = state.now_iso()
        state.save(s)
        rec["aggregates"] = aggregate.rebuild()
        run.do_render("both", rec, push=False)
    print(cfg.site_local_dir / "index.html")


def write_app(root: Path) -> Path:
    """The example app's repo at `root`: its routes and its `ramore.toml`; the config's path."""
    for rel, name in SVELTEKIT_DIRS.items():
        d = root / "src" / "routes" / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / name).touch()
    (root / "routes.py").write_text(f"API_ROUTES = {API_ROUTES!r}\n")
    path = root / config.CONFIG_NAME
    path.write_text(f"""name = "{NAME}"
store = "store"

[source]
ssh = "{SSH_HOST}"
hosts = {json.dumps(HOSTS)}

[routes]
sveltekit = "."
api = "routes.py:API_ROUTES"

[search]
route = "{SEARCH.route}"
param = "{SEARCH.param}"

[login]
route = "{LOGIN.route}"
ok_status = {LOGIN.ok_status}
label = "{LOGIN.label}"

[metrics]
units = {json.dumps(UNITS)}
interval_s = {SAMPLE_EVERY_S}

[publish]
""")
    return path


def traffic(
    start: dt.datetime, end: dt.datetime, visitors: int, seed: int = 0
) -> list[str]:
    """Request-log lines in [start, end), in time order, about `visitors` human visits a day."""
    rng = random.Random(seed)
    n_visits = round(visitors * (end - start) / dt.timedelta(days=1))
    sessions = [(_visit, n_visits)] + [
        (fn, round(n_visits * share))
        for fn, share in (
            (_crawl, CRAWLS),
            (_preview, PREVIEWS),
            (_script, SCRIPTS),
            (_scan, SCANS),
        )
    ]
    reqs = [
        r
        for fn, n in sessions
        for _ in range(n)
        for r in fn(rng, _when(rng, start, end))
    ]
    reqs += _every(
        start,
        end,
        UPTIME_EVERY,
        lambda t: [
            _req(t, "/", ua=_UPTIME, addr="203.0.113.250", method="HEAD", cs="HIT")
        ],
    )
    reqs += _every(start, end, PROBE_EVERY, lambda t: _probe(rng, t))
    _incident(reqs, end - dt.timedelta(days=1, hours=rng.randrange(12)))
    lines = [
        _line(r) for r in sorted(reqs, key=lambda r: r["t"]) if r["t"] < end.timestamp()
    ]
    for i in range(round((end - start) / dt.timedelta(days=1))):
        lines.insert(rng.randrange(len(lines) + 1), lines[i][: len(lines[i]) // 2])
    return lines


def samples(start: dt.datetime, end: dt.datetime) -> list[str]:
    """Metrics-log lines every `SAMPLE_EVERY_S` in [start, end): the box's memory and root disk,
    an API whose anon memory grows until its daily restart, two web workers and nginx."""
    out = []
    t = start.timestamp()
    span = end.timestamp() - t
    while t < end.timestamp():
        day = (t % 86400) / 86400
        api_anon = round((0.6 + 3.6 * ((day - 4 / 24) % 1)) * _GB)
        web_anon = round((0.3 + 0.05 * math.sin(t / 3600)) * _GB)
        units = {
            "app-api.service": (api_anon + _GB // 4, 6 * _GB, api_anon, 140, 1),
            "app-web@1.service": (web_anon + _GB // 8, None, web_anon, 60, 3),
            "app-web@2.service": (web_anon + _GB // 9, None, web_anon, 58, 3),
            "nginx.service": (_GB // 8, None, _GB // 32, 90, 5),
        }
        rows = [
            {
                "scope": "host",
                "name": "",
                "used": 5 * _GB + api_anon,
                "limit": 16 * _GB,
            },
            {
                "scope": "mount",
                "name": "/",
                "used": round((61 + 3 * (t - start.timestamp()) / span) * _GB),
                "limit": 100 * _GB,
            },
            *(
                {
                    "scope": "unit",
                    "name": name,
                    "used": used,
                    "limit": limit,
                    "anon": anon,
                    "fds": fds,
                    "fd_limit": 1024,
                    "procs": procs,
                }
                for name, (used, limit, anon, fds, procs) in units.items()
            ),
        ]
        out += [json.dumps({"t": round(t, 3), **r}) for r in rows]
        t += SAMPLE_EVERY_S
    return out


def _visit(rng: random.Random, t: float) -> list[dict]:
    ua, addr = rng.choice(_BROWSERS), _addr(rng, "203.0.113")
    page = rng.choices(("/", _deep(rng), "/about", "/quest"), (4, 5, 1, 0.5))[0]
    ref = rng.choice(_REFERRERS) if rng.random() < 0.5 else ""
    reqs = [_page(rng, t, page, ua, addr, ref)]
    t += 0.2
    for i in range(rng.randint(2, 4)):
        reqs.append(
            _req(
                t + 0.05 * i,
                f"/_app/immutable/chunks/{rng.getrandbits(32):08x}.js",
                ua=ua,
                addr=addr,
                referrer=_www(page),
                status=rng.choice((200, 200, 304)),
                size=rng.randint(3_000, 400_000),
            )
        )
    reqs.append(_api(rng, t + 0.3, "/v1/specs", ua, addr, page))
    for _ in range(min(int(rng.expovariate(0.3)), 12)):
        t += rng.uniform(5, 90)
        nxt = _deep(rng)
        reqs.append(
            _req(
                t,
                f"{nxt}/__data.json",
                ua=ua,
                addr=addr,
                referrer=_www(page),
                **_upstream(rng, 0.04, cache=0.2),
                size=rng.randint(1_000, 15_000),
            )
        )
        reqs.append(
            _api(
                rng,
                t + 0.1,
                f"/v1/trees{nxt[: nxt.index('/', 1)]}/{rng.getrandbits(24):06x}",
                ua,
                addr,
                nxt,
            )
        )
        if rng.random() < 0.15:
            reqs.append(
                _api(
                    rng,
                    t + 0.5,
                    f"/v1/works-intersect/{rng.getrandbits(16):04x}/{rng.getrandbits(16):04x}",
                    ua,
                    addr,
                    nxt,
                    median=0.9,
                )
            )
        page = nxt
    if rng.random() < 0.4:
        word = rng.choice(_WORDS)
        for k in range(3, len(word) + 1, 2):
            reqs.append(
                _api(
                    rng,
                    t + k,
                    f"/v1/names/{rng.choice(_ROOT_TYPES)}?q={word[:k]}",
                    ua,
                    addr,
                    page,
                    median=0.02,
                )
            )
    if rng.random() < 0.05:
        ok = rng.random() < 0.9
        reqs.append(
            _req(
                t + 20,
                f"{LOGIN.route}?code={rng.getrandbits(64):016x}",
                ua=ua,
                addr=addr,
                referrer="https://auth.example.org/",
                status=LOGIN.ok_status if ok else 400,
                **_upstream(rng, 0.12, cache=0),
            )
        )
    return reqs


def _crawl(rng: random.Random, t: float) -> list[dict]:
    ua, addr = rng.choice(_CRAWLERS), _addr(rng, "198.51.100")
    reqs = [
        _req(
            t,
            p,
            ua=ua,
            addr=addr,
            size=rng.randint(50, 30_000),
            **_upstream(rng, 0.01, cache=0.8),
        )
        for p in ("/robots.txt", "/sitemap.xml", f"/sitemap-{rng.randrange(8)}.xml")[
            : rng.randint(0, 3)
        ]
    ]
    for i in range(rng.randint(3, 40)):
        reqs.append(
            _page(rng, t + 2 + i * rng.uniform(1, 10), _deep(rng), ua, addr, "")
        )
    return reqs


def _preview(rng: random.Random, t: float) -> list[dict]:
    ua, addr, page = rng.choice(_PREVIEWS), _addr(rng, "198.51.100"), _deep(rng)
    return [
        _page(rng, t, page, ua, addr, ""),
        _req(
            t + 0.5,
            f"/card{page}/share.png",
            ua=ua,
            addr=addr,
            size=rng.randint(40_000, 120_000),
            **_upstream(rng, 0.3, cache=0.5),
        ),
    ]


def _script(rng: random.Random, t: float) -> list[dict]:
    ua, addr = rng.choice(_SCRIPTS), _addr(rng, "192.0.2")
    reqs = []
    for i in range(rng.randint(10, 60)):
        path = f"/v1/trees/{rng.choice(_ROOT_TYPES)}/{rng.getrandbits(24):06x}"
        if i > 20 and rng.random() < 0.5:
            reqs.append(
                _req(
                    t + i * 0.2,
                    path,
                    host="api.example.org",
                    ua=ua,
                    addr=addr,
                    status=429,
                    rt=0.0,
                )
            )
        else:
            reqs.append(_api(rng, t + i * 0.2, path, ua, addr, ""))
    return reqs


def _scan(rng: random.Random, t: float) -> list[dict]:
    ua, addr = rng.choice(("", "Mozilla/5.0 zgrab/0.x")), _addr(rng, "192.0.2")
    host = _BOX_IP if rng.random() < 0.3 else "www.example.org"
    return [
        _req(
            t + i,
            p,
            host=host,
            ua=ua,
            addr=addr,
            status=404,
            **_upstream(rng, 0.005, cache=0),
        )
        for i, p in enumerate(rng.sample(_SCANS, rng.randint(1, len(_SCANS))))
    ]


def _probe(rng: random.Random, t: float) -> list[dict]:
    ua, addr = f"{MONITOR_PRODUCT}/{__version__}", "203.0.113.251"
    return [
        _page(rng, t + i * 0.3, p, ua, addr, "")
        for i, p in enumerate(("/", _deep(rng), "/about"))
    ]


def _page(
    rng: random.Random, t: float, path: str, ua: str, addr: str, ref: str
) -> dict:
    return _req(
        t,
        path,
        ua=ua,
        addr=addr,
        referrer=ref,
        size=rng.randint(20_000, 90_000),
        **_upstream(rng, 0.08, cache=0.3),
    )


def _api(
    rng: random.Random,
    t: float,
    path: str,
    ua: str,
    addr: str,
    page: str,
    median: float = 0.15,
) -> dict:
    return _req(
        t,
        path,
        host="api.example.org",
        ua=ua,
        addr=addr,
        referrer=_www(page) if page else "",
        size=rng.randint(1_000, 300_000),
        **_upstream(rng, median, cache=0.4),
    )


def _upstream(rng: random.Random, median: float, cache: float) -> dict:
    """The timing fields of a proxied request: a cache hit has no upstream."""
    if rng.random() < cache:
        return {"cs": "HIT", "rt": rng.uniform(0.0005, 0.003)}
    urt = rng.lognormvariate(math.log(median), 0.6)
    return {
        "cs": "MISS",
        "rt": urt + rng.uniform(0.001, 0.01),
        "uct": 0.001,
        "uht": urt * 0.9,
        "urt": urt,
    }


def _req(t: float, path: str, **fields) -> dict:
    return {
        "t": t,
        "path": path,
        "method": "GET",
        "status": 200,
        "size": 0,
        "referrer": "",
        "host": "www.example.org",
        "rt": 0.001,
        **fields,
    }


def _incident(reqs: list[dict], at: dt.datetime) -> None:
    """The API answers 502 for `INCIDENT` from `at`: its upstream is down."""
    t0, t1 = at.timestamp(), (at + INCIDENT).timestamp()
    for r in reqs:
        if t0 <= r["t"] < t1 and r["path"].startswith("/v1/"):
            r |= {
                "status": 502,
                "size": 0,
                "cs": "",
                "urt": None,
                "uht": None,
                "uct": 0.001,
            }


def _every(start: dt.datetime, end: dt.datetime, step: dt.timedelta, fn) -> list[dict]:
    n = int((end - start) / step)
    return [r for i in range(n) for r in fn((start + i * step).timestamp())]


def _when(rng: random.Random, start: dt.datetime, end: dt.datetime) -> float:
    """A time in [start, end), busier in the European afternoon."""
    while True:
        t = rng.uniform(start.timestamp(), end.timestamp())
        hour = (t % 86400) / 3600
        if (
            rng.random()
            < 0.3 + 0.7 * (1 + math.cos(2 * math.pi * (hour - 14) / 24)) / 2
        ):
            return t


def _deep(rng: random.Random) -> str:
    return f"/{rng.choice(_ROOT_TYPES)}/{rng.getrandbits(24):06x}"


def _www(path: str) -> str:
    return f"https://www.example.org{path}"


def _addr(rng: random.Random, net: str) -> str:
    return f"{net}.{rng.randrange(1, 250)}"


def _line(r: dict) -> str:
    """`r` as nginx writes it in ramore's format: every value a string, an unset one empty."""
    values = {
        "addr": "",
        "ua": "",
        "uct": None,
        "uht": None,
        "urt": None,
        "cs": "",
        **r,
    }
    out = {}
    for k in REQUEST_FIELDS:
        v = values[k]
        out[k] = "" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)
    return json.dumps(out)
