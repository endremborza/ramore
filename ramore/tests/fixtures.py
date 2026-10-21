"""Fixture request-log lines, as nginx writes `box.REQUEST_FIELDS`: every value a JSON string,
an unset variable (no upstream, no header) an empty one."""

import datetime as dt
import json

_BASE = {
    "addr": "203.0.113.42",
    "method": "GET",
    "path": "/",
    "status": "200",
    "size": "0",
    "referrer": "",
    "ua": "",
    "rt": "0.001",
    "uct": "",
    "uht": "",
    "urt": "",
    "cs": "",
    "host": "www.example.org",
}


def line(at: str, **fields: str) -> str:
    """A log line at UTC time `at` (ISO), `fields` over the defaults."""
    t = dt.datetime.fromisoformat(at).replace(tzinfo=dt.timezone.utc).timestamp()
    return json.dumps({"t": f"{t:.3f}", **_BASE, **fields})


LINE_HUMAN = line(
    "2026-04-28T13:14:15",
    path="/v1/names/authors?q=darwin",
    size="4321",
    referrer="https://www.example.org/",
    ua="Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0",
    rt="0.045",
    uct="0.001",
    uht="0.040",
    urt="0.040",
    cs="MISS",
)

LINE_BOT = line(
    "2026-04-28T13:14:16",
    addr="198.51.100.7",
    path="/sitemap.xml",
    size="1024",
    ua="Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)",
    rt="0.012",
    uct="0.000",
    uht="0.011",
    urt="0.011",
    cs="HIT",
)

# No upstream involvement: the upstream variables are unset.
LINE_NO_UPSTREAM = line(
    "2026-04-28T13:14:18",
    addr="198.51.100.99",
    path="/robots.txt",
    size="56",
    ua="AhrefsBot/7.0",
    cs="BYPASS",
)

# 429 rate-limited.
LINE_429 = line(
    "2026-04-28T13:14:19",
    addr="203.0.113.99",
    path="/v1/trees/authors/asem-foo",
    status="429",
    rt="0.000",
    ua="python-requests/2.31.0",
    cs="BYPASS",
)

LINE_EMPTY_UA = line(
    "2026-04-28T13:14:20", addr="203.0.113.5", size="1234", rt="0.005", cs="HIT"
)

LINE_EMPTY_CS = line(
    "2026-04-28T13:14:21",
    addr="203.0.113.6",
    path="/favicon.ico",
    status="302",
    rt="0.002",
    ua="curl/8.0",
)

# A retried upstream logs one time per attempt; no single number.
LINE_RETRY = line(
    "2026-04-28T13:14:22",
    path="/authors/darwin",
    status="502",
    urt="0.004, 0.002",
    ua="curl/8.0",
)

# A request nginx never answered logs status `000`.
LINE_STATUS_000 = line("2026-04-28T13:14:23", status="000", ua="curl/8.0")

# Non-live lines (dropped by keep_live_hosts): the alpha vhost, and a raw-IP Host
# (a scanner, or the box's own public IP).
LINE_HOST_LIVE = LINE_HUMAN
LINE_HOST_ALPHA = line(
    "2026-06-29T10:14:15",
    addr="203.0.113.43",
    path="/authors/darwin",
    status="502",
    host="alpha.example.org",
)
LINE_HOST_RAWIP = line(
    "2026-06-29T10:14:16",
    addr="203.0.113.44",
    path="/authors/darwin",
    status="502",
    host="3.69.38.142",
)

LINE_GARBAGE = "this is not a log line at all"

# A line missing a request key: another format, so a failure.
LINE_MISSING_KEY = json.dumps(
    {k: v for k, v in json.loads(LINE_HUMAN).items() if k != "cs"}
)

# Interleaved/torn: a second request's line spliced into the middle of this one. It must be
# dropped as a parse failure, never crash the batch, never surface the spliced address.
LINE_TORN = LINE_EMPTY_UA[:40] + line("2026-04-28T13:14:22", addr="162.158.162.77")

ALL = [
    LINE_HUMAN,
    LINE_BOT,
    LINE_NO_UPSTREAM,
    LINE_429,
    LINE_EMPTY_UA,
    LINE_EMPTY_CS,
]
