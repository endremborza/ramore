"""Fixture log lines in the current nginx log_format (cs= and host= always present)."""

LINE_HUMAN = (
    "203.0.113.42 - - [28/Apr/2026:13:14:15 +0000] "
    '"GET /v1/names/authors?q=darwin HTTP/2.0" 200 4321 '
    '"https://www.example.org/" '
    '"Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0"'
    'rt=0.045 uct="0.001" uht="0.040" urt="0.040" cs=MISS host=www.example.org'
)

LINE_BOT = (
    "198.51.100.7 - - [28/Apr/2026:13:14:16 +0000] "
    '"GET /sitemap.xml HTTP/1.1" 200 1024 '
    '"-" '
    '"Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)"'
    'rt=0.012 uct="0.000" uht="0.011" urt="0.011" cs=HIT host=www.example.org'
)

# Hyphenated upstream timings (no upstream involvement).
LINE_NO_UPSTREAM = (
    "198.51.100.99 - - [28/Apr/2026:13:14:18 +0000] "
    '"GET /robots.txt HTTP/1.1" 200 56 '
    '"-" "AhrefsBot/7.0"'
    'rt=0.001 uct="-" uht="-" urt="-" cs=BYPASS host=www.example.org'
)

# 429 rate-limited.
LINE_429 = (
    "203.0.113.99 - - [28/Apr/2026:13:14:19 +0000] "
    '"GET /v1/trees/authors/asem-foo HTTP/2.0" 429 0 '
    '"-" "python-requests/2.31.0"'
    'rt=0.000 uct="-" uht="-" urt="-" cs=BYPASS host=www.example.org'
)

# Empty UA.
LINE_EMPTY_UA = (
    "203.0.113.5 - - [28/Apr/2026:13:14:20 +0000] "
    '"GET / HTTP/1.1" 200 1234 '
    '"-" "-"'
    'rt=0.005 uct="-" uht="-" urt="-" cs=HIT host=www.example.org'
)

# Empty cache status (`cs=` with no value, no `-`).
LINE_EMPTY_CS = (
    "203.0.113.6 - - [28/Apr/2026:13:14:21 +0000] "
    '"GET /favicon.ico HTTP/1.1" 302 0 '
    '"-" "curl/8.0"'
    'rt=0.002 uct="-" uht="-" urt="-" cs= host=www.example.org'
)

# Non-live lines (dropped by keep_live_hosts): the alpha vhost, and a raw-IP Host
# (a scanner, or the box's own public IP).
LINE_HOST_LIVE = LINE_HUMAN
LINE_HOST_ALPHA = (
    "203.0.113.43 - - [29/Jun/2026:10:14:15 +0000] "
    '"GET /authors/darwin HTTP/2.0" 502 166 '
    '"-" '
    '"Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0"'
    'rt=0.000 uct="-" uht="-" urt="-" cs=MISS host=alpha.example.org'
)
LINE_HOST_RAWIP = (
    "203.0.113.44 - - [29/Jun/2026:10:14:16 +0000] "
    '"GET /authors/darwin HTTP/2.0" 502 166 '
    '"-" '
    '"Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0"'
    'rt=0.000 uct="-" uht="-" urt="-" cs=MISS host=3.69.38.142'
)

# Garbage line for failure counting.
LINE_GARBAGE = "this is not an nginx log line at all"

# Interleaved/torn line: a second request's prefix was spliced into the middle of
# this line's [time] bracket, so `time` captures an embedded client IP. It must be
# dropped as a parse failure — never crash the batch, never surface the raw IP.
LINE_TORN = (
    "203.0.113.7 - - [28/Apr/2026:162.158.162.77 - - [28/Apr/2026:13:14:22 +0000] "
    '"GET / HTTP/1.1" 200 10 '
    '"-" "-"'
    'rt=0.001 uct="-" uht="-" urt="-" cs=HIT host=www.example.org'
)

ALL = [
    LINE_HUMAN,
    LINE_BOT,
    LINE_NO_UPSTREAM,
    LINE_429,
    LINE_EMPTY_UA,
    LINE_EMPTY_CS,
]
