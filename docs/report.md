# The report

`ramore report` turns a site's nginx access log into a static report site with two pillars: **traffic** (bot-vs-human estimation) and **performance** (per-endpoint latency, cache hit rate, error catalog). It runs from any host with ssh to the box serving the site, archives parsed events forever (cold-compressed past `COLD_AFTER_DAYS`), and publishes an anonymized site to the app repo's pages branch.

## Run

One run pulls the log lines appended since the last run, parses and keeps the app's hosts, templates each path, sessionizes and classifies, writes the hot archive, compacts old days into the cold archive, updates the hourly and daily aggregates, renders the local and/or public site and publishes the public one. Missed runs recover on their own through the byte-offset bookkeeping in `state.json`, rotation included: a changed inode pulls the rotated `<log>.1` tail first. A first run reads only the last `FIRST_RUN_TAIL_LINES` lines.

Every run appends a JSON record (counts, timings, errors) to `logs/run-<date>.log`; the runs page lists them. Error text can echo raw log content, so the public site drops `error` and `trace` and only the local one keeps them.

## Log format

The parser (`parse.LINE_RE`) reads this nginx `log_format`; `cs` gives the cache hit rate and `host` lets the report keep only the app's vhosts, as an allowlist (`[source] hosts`), since the box's log can mix in other vhosts and junk hitting it by raw IP or spoofed Host. `host` is never persisted.

```nginx
log_format upstream_time '$remote_addr - $remote_user [$time_local] '
                         '"$request" $status $body_bytes_sent '
                         '"$http_referer" "$http_user_agent"'
                         'rt=$request_time uct="$upstream_connect_time" uht="$upstream_header_time" urt="$upstream_response_time" cs=$upstream_cache_status host=$host';
```

A line in another format counts as a parse failure in the run record.

## Routes

Every path is counted under a route template (`routes.py`): `{x}` is one segment, `{...x}` the rest of the path, zero or more segments. The table comes from the app's `[routes]`:

- `api`: a literal list of paths assigned in a Python file (`file.py:NAME`), in axum syntax (`:x`, `*x`) or ramore's; their templates are the API routes.
- `sveltekit`: a SvelteKit project. Each `+page` directory of `src/routes` is a page and also answers `<page>/__data.json` (a data route); each `+server` directory is an endpoint; `/_app/{...path}` is the build's assets. Route groups `(x)` drop out, `[x]` becomes `{x}`, `[...x]` becomes `{...x}`; optional params `[[x]]` are refused.

The first template that matches wins, in specificity order per segment: literal, literal with a param, a plain param, a param with a matcher, the rest. A matcher (`[x=m]`) is app code ramore cannot run, so its param matches like a plain one, ranked after every alternative without one. A path no template matches is `_unknown`; the run record lists the most frequent ones.

## Sessions and classification

A session is one `(ip, ua)` pair split by a `SESSION_IDLE_MIN` idle gap; its id hashes the pair with the day's salt (`salts.json`, never published). Classification (`classify.py`) is rule-based: a hard bot rule (empty or known-bot UA, a bot-only route of `BOT_ONLY_ROUTES`, every request a HEAD) makes a session `bot_known`; otherwise soft rules add up a score (search use, a browser UA, SvelteKit data prefetches, a referrer, no API call at all) and the thresholds split it into `human_likely`, `unknown` and `bot_likely`. The classification page lists every rule with the share of sessions it fired on.

## Landing page

KPIs per window (requests, sessions, 5xx rate, p99 upstream time, cache hit rate, sessions by class), requests per day and sessions per class. With a `[login]` section it also counts logins from the log alone: hits on the login route answered with `ok_status`, a 4xx answer counting as failed, per window and per day.

## Storage

Under `store` (keep it out of git):

- `archive/year_month=YYYY-MM/day=DD/`: hot, one row per request, within `COLD_AFTER_DAYS`.
- `archive-cold/year=YYYY/month=M/`: older days merged per month, heavier zstd, without the free-text columns (`path`, `ua`, `referrer`).
- `aggregates/{hourly,daily,sessions}/`: per-bucket counts and latency quantiles by route, status family, bot class and cache status; one row per session.
- `site/` (raw values, local only), `site-public/` (anonymized, published), `logs/`, `state.json`, `salts.json`.

Hot archive columns: `t`, `addr`, `method`, `path`, `route_template`, `status`, `size`, `referrer`, `ua`, `ua_family`, `bot_class`, `rt`, `uct`, `uht`, `urt`, `cs`, `session_id`, `referrer_domain`. `route_template`, `ua_family`, `bot_class` and `session_id` are written at archive time, so a change to the route table or the rules applies to new rows; `ramore report --reclassify-all` re-classifies the hot days (not their templates) and rebuilds the aggregates.

## Anonymization

The public site carries no IP and no raw UA: UAs become a family and a bot class, referrers their registered domain, session addresses a day-salted hash cut to `IP_HASH_LEN`.

## Publish

`publish.py` checks the pages branch out into the `[publish] worktree`, replaces its content with `site-public/` and pushes. The first run creates the orphan branch when the remote has none. One-time GitHub setup: Settings → Pages → source = the branch, root; a custom domain via a `CNAME` file.

## Reset

`ramore reset` wipes the store and force-pushes an empty pages branch (keeping `CNAME`); `--local-only` keeps the published site. Run it before pointing the report at a new box, so nothing from the old one lingers: the next run then starts from the new log's tail, as a first run.
