# The report

`ramore report` turns a site's nginx access log into a static report site with two pillars: **traffic** (bot-vs-human estimation) and **performance** (per-endpoint latency, cache hit rate, error catalog). It runs from any host with ssh to the box serving the site, archives parsed events forever (cold-compressed past `COLD_AFTER_DAYS`), and publishes an anonymized site to the app repo's pages branch.

## Run

One run brings ramore's side of the box up to date (The box, below), pulls the lines appended to its two logs since the last run, archives the metrics, parses the requests and keeps the app's hosts, templates each path, sessionizes and classifies, writes the hot archive, compacts old days into the cold archive, updates the hourly and daily aggregates, renders the local and/or public site and publishes the public one. Missed runs recover on their own through the per-log byte offsets in `state.json`, rotation included: a changed inode pulls the rotated `<log>.1` tail first. Each read stops at the size stat'd before it, so a line written meanwhile waits for the next run. A first read of a log takes only its last `FIRST_RUN_TAIL_BYTES`. The request log is pulled in chunks of at most `pull.PULL_CHUNK_BYTES`, cut at line ends: each chunk is archived, its days re-aggregated and its offset saved before the next is read, so a backlog after downtime costs time rather than memory, and a killed run resumes after its last saved chunk. A session spanning two chunks counts as two, as one spanning two runs does.

Every run appends a JSON record (counts, timings, errors) to `logs/run-<date>.log`; the runs page lists them. Error text can echo raw log content, so the public site drops `error`, `trace` and `alert_error` and only the local one keeps them.

## The box

ramore owns everything it reads from the box (`ramore/box/`), rendered from the app's config: `box.files` is every file the box should hold, `box.STAMP` on the box the hash of the last install. A report run (or `ramore box`) compares the two and, when they differ, runs one install script with `sudo -n` over ssh; the stamp is written last, so a failed install is retried by the next run. Without `[metrics]` the install removes the sampler.

**Request log.** `/etc/nginx/conf.d/ramore.conf` defines `log_format ramore escape=json` from `box.REQUEST_FIELDS` (each key and the nginx variable it carries, every value a JSON string) and an http-level `access_log` to `box.REQUEST_LOG`. Every server block without an `access_log` of its own inherits it, so the app's nginx config must set none; the distro's own http-level log keeps logging too. The install tests the config with `nginx -t` and restores the previous file when it fails. The distro logrotate (`/var/log/nginx/*.log`) rotates the log. A line that is not a JSON object with exactly the request keys counts as a parse failure in the run record. `cs` gives the cache hit rate and `host` lets the report keep only the app's vhosts, as an allowlist (`[source] hosts`), since the box's log can mix in other vhosts and junk hitting it by raw IP or spoofed Host. `host` is never persisted.

**Metrics.** With `[metrics]`, a root timer runs `box/sample.py` (stdlib only, on the box's `python3`) every `interval_s`, appending one JSON line per scope to `box.METRICS_LOG`, rotated by a logrotate drop-in. Scopes: `host` (memory), each of `mounts` (disk), each unit whose cgroup directory matches a `units` glob. `used`/`limit` is each scope's fill (a unit's `memory.current` against `memory.max`, page cache included); a unit adds `anon` memory (where a leak shows), `fds` summed over its processes, `fd_limit` (the lowest soft limit among them) and `procs`. The report archives them in `metrics/` and renders the local site's box page.

## Alerts

Each caller steps its own incident table (`alert.py`, persisted under the store's `incidents/`): a failing check fires once it has failed for the caller's sustain time, passing again resolves it, and each step's events go out as one mail; a failed send fires again on the next step. The report run checks the latest box sample against `[alerts]` and fires at once; a mail that does not go out (`alert.MailError`: `alert.SMTP_ENV` unset, or the SMTP exchange failing) goes into the run record as `alert_error` and the run carries on. `ramore monitor` probes `[monitor] url` and a few same-site links from it every `PROBE_EVERY_S`, firing after `SUSTAIN_S`, and checks the age of the published report: each rendered site carries `SITE_STATE` with its last pull, stale after `REPORT_STALE_AFTER` (two missed runs). The monitor's requests carry the `MONITOR_PRODUCT` User-Agent, a bot UA family of its own in the report.

`ramore units` installs a host's systemd user units: the report timer (every `REPORT_EVERY_H` hours, `Persistent=true`, under `REPORT_MEMORY_MAX`) and the monitor service (restarted at least daily). A failed or OOM-killed run shows in `systemctl --user --failed`; the alert for it is the monitor's freshness check.

## Routes

Every path is counted under a route template (`routes.py`): `{x}` is one segment, `{...x}` the rest of the path, zero or more segments. The table comes from the app's `[routes]`:

- `api`: a literal list of paths assigned in a Python file (`file.py:NAME`), in axum syntax (`:x`, `*x`) or ramore's; their templates are the API routes.
- `sveltekit`: a SvelteKit project. Each `+page` directory of `src/routes` is a page and also answers `<page>/__data.json` (a data route); each `+server` directory is an endpoint; `/_app/{...path}` is the build's assets. Route groups `(x)` drop out, `[x]` becomes `{x}`, `[...x]` becomes `{...x}`; optional params `[[x]]` are refused.

The first template that matches wins, in specificity order per segment: literal, literal with a param, a plain param, a param with a matcher, the rest. A matcher (`[x=m]`) is app code ramore cannot run, so its param matches like a plain one, ranked after every alternative without one. A path no template matches is `_unknown`; the run record lists the most frequent ones.

## Sessions and classification

A session is one `(ip, ua)` pair split by a `SESSION_IDLE_MIN` idle gap; its id hashes the pair with the day's salt (`salts.json`, never published). Classification (`classify.py`) is rule-based: a hard bot rule (empty or known-bot UA, a bot-only route of `BOT_ONLY_ROUTES`, every request a HEAD) makes a session `bot_known`; otherwise soft rules add up a score (search use, a browser UA, SvelteKit data prefetches, a referrer, no API call at all) and the thresholds split it into `human_likely`, `unknown` and `bot_likely`. Each rule is a bool column of the sessions table, next to `score`, so the classification page reads the share of sessions each rule fired on and the score distribution straight from them.

## Landing page

KPIs per window (requests, sessions, 5xx rate, p99 upstream time, cache hit rate, sessions by class), requests per day and sessions per class. With a `[login]` section it also counts logins from the log alone: hits on the login route answered with `ok_status`, a 4xx answer counting as failed, per window and per day.

## Storage

Under `store` (keep it out of git):

- `archive/year_month=YYYY-MM/day=DD/`: hot, one row per request, within `COLD_AFTER_DAYS`.
- `archive-cold/year_month=YYYY-MM/day=DD/`: the hot days past `COLD_AFTER_DAYS`, one partition per day, heavier zstd, without `archive.FREE_TEXT_COLUMNS`. A run's compaction reads only the days it moves, whatever the cold archive already holds; a day already cold (a request arriving after its move) gains the hot rows it lacks, and the aggregates read a day wherever it is stored.
- `aggregates/{hourly,daily,sessions}/`: per-bucket counts and latency quantiles by route, status family, bot class and cache status (`aggregate.LEVELS`); one row per session (`classify.SESSION_SCHEMA`).
- `metrics/year_month=YYYY-MM/`: the box samples, one row per scope and sample.
- `site/` (raw values, local only), `site-public/` (anonymized, published), `logs/`, `state.json`, `salts.json`.

The hot archive's columns are `archive.HOT_COLUMNS`. `route_template`, `ua_family`, `bot_class` and `session_id` are written at archive time, so a change to the route table or the rules applies to new rows; `ramore report --reclassify-all` re-classifies the hot days one at a time (not their templates; a session crossing midnight is classified per day) and rebuilds the aggregates. A rebuild aggregates the archive one day at a time, hot and cold alike.

## Anonymization

The public site carries no IP and no raw UA: the render reads neither (`render/base.EVENT_RENDER_COLS`, `WINDOW_RENDER_COLS`, `SESSION_RENDER_COLS`), so UAs show as a family and a bot class, referrers as their registered domain. Every template autoescapes, so a request path someone sent (the runs pages list unmatched ones) cannot inject markup.

## Publish

`publish.py` commits `site-public/` onto the pages branch with git plumbing in the app's repo, without checking anything out: it fetches the branch's remote head, stages the site into a temporary index, carries `publish.CARRIED` (`CNAME`) over from the head, writes the tree and pushes a commit of it on top of the head; an unchanged tree pushes nothing, and a remote without the branch gets a root commit. One-time GitHub setup: Settings → Pages → source = the branch, root; a custom domain set there writes the `CNAME` that every publish keeps.

## Reset

`ramore reset` wipes the store and force-pushes a pages branch of one commit holding only `publish.CARRIED`; `--local-only` keeps the published site. Run it before pointing the report at a new box, so nothing from the old one lingers: the next run then starts from the new log's tail, as a first run.

## Demo

`ramore demo` (`demo.py`) writes an example app (a `ramore.toml`, an API route list, a SvelteKit route tree) and generates `DEMO_DAYS` of its traffic and box samples in the box's two log formats: human visits with pages, prefetches, API calls, searches, logins and referrers; crawlers, link-preview bots, scripts, scanners (some by raw IP), an uptime checker, ramore's monitor, a few torn lines and one 5xx incident. It runs them through the report's ingest, aggregation and render into a fresh store and prints the local site's path; `--visitors` scales the traffic. The repo's Pages site is this demo, deployed by `.github/workflows/demo.yml`.

The tests load the same app. `test_e2e.py` serves the demo's logs from a fake box (`conftest.fake_box`: an `ssh` on PATH that runs each command under a temporary root, with `nginx` and `systemctl` stubs) and drives `report`, `units` and `reset` through the CLI against a bare git remote.
