# ramore

[![pypi](https://img.shields.io/pypi/v/ramore.svg)](https://pypi.org/project/ramore/)

ramore monitoring and reporting. ramore installs its own side on the box serving a site (an nginx request log in its JSON format, and optionally a sampler of the box's memory, disks and units). `ramore report` pulls both logs over ssh, archives every request in a partitioned parquet store, sorts sessions into bots and humans, and publishes an anonymized static report site to the app repo's GitHub Pages branch. Extracted from the reporting pipeline of [rankless](https://github.com/endremborza/rankless).

[The demo report](https://endremborza.github.io/ramore/) renders an example site's synthetic traffic; `uvx ramore demo` builds the same locally.

## Install

```bash
uv tool install ramore   # or, from a checkout with the locked versions: make install
```

## Use

The app's repo commits a `ramore.toml`; every command reads the one in the working directory, or the one `--config-file` names.

```bash
ramore report                          # box check → pull → archive → aggregate → render → publish
ramore report --no-publish --mode local
ramore report --dry-run                # pull and parse, no writes, the box included
ramore box                             # install or update ramore's side of the box (sudo over ssh)
ramore box --check                     # current or outdated, exit 1 when outdated
ramore monitor                         # probe the site, mail incidents (--once: print one round)
ramore units --report --monitor        # this host's systemd user units (--remove to drop them)
ramore reset                           # wipe the store and the published history
ramore demo --out ramore-demo          # an example app and its synthetic traffic, rendered
```

Mail goes through SMTP over SSL with `RAMORE_SMTP_HOST`, `RAMORE_SMTP_USER` and `RAMORE_SMTP_PASSWORD` from the environment, to `RAMORE_MAIL_TO` (default: the user). The units read them from `~/.config/environment.d/`.

## `ramore.toml`

Relative paths resolve against the file's directory. A variant (another box, say) starts with `extends = "ramore.toml"` and replaces whole tables; `publish = false` keeps it local.

```toml
name = "Example"                       # the site title: "Example reports"
store = "reports"                      # archive, aggregates, rendered sites, run logs, state

[source]
ssh = "example-live"                   # the box serving the site; its user needs passwordless sudo
hosts = ["www.example.org", "api.example.org"]  # the vhosts reported on; every other row is dropped

[routes]
sveltekit = "."                        # a SvelteKit project: its src/routes pages, endpoints and data routes
api = "gen/routes.py:API_ROUTES"       # a literal list of API paths assigned in a Python file

[search]                               # optional: the search route, a strong human signal
route = "/v1/search/{kind}"
param = "q"

[login]                                # optional: a login card and chart on the landing page
route = "/callback"
ok_status = 302
label = "Logins"

[monitor]                              # optional: `ramore monitor`; defaults in `config.Monitor`
url = "https://www.example.org/"       # probed with `follow` random same-site links from it
max_latency_s = 2                      # a slower answer fails
report_url = "https://example.github.io/example/"  # optional: alert when the published report goes stale

[alerts]                               # optional: report-time thresholds on the box sample, in % of
disk_pct = 85                          # each limit (memory_pct, disk_pct, unit_anon_pct, unit_fds_pct);
                                       # defaults in `config.Alerts`

[metrics]                              # optional: the box sampler and the local site's box page
units = ["app-*.service", "nginx.service"]  # unit-name globs
interval_s = 30                        # default DEFAULT_METRICS_INTERVAL_S
mounts = ["/", "/data"]                # default DEFAULT_METRICS_MOUNTS

[site]
include = { poster = "docs/poster" }   # directories published under the site as-is

[publish]                              # optional: without it the site stays local
branch = "gh-pages"                    # default DEFAULT_BRANCH
remote = "origin"                      # the app repo's remote; default DEFAULT_REMOTE
```

How the report works, what it stores and what it installs on the box: [docs/report.md](docs/report.md).
