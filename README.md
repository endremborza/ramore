# ramore

ramore monitoring and reporting. `ramore report` pulls a site's nginx access log over ssh, archives every request in a partitioned parquet store, sorts sessions into bots and humans, and publishes an anonymized static report site to the app repo's GitHub Pages branch. Extracted from the reporting pipeline of [rankless](https://github.com/endremborza/rankless).

## Install

```bash
uv tool install -e /path/to/ramore   # or: uv tool install git+https://github.com/endremborza/ramore
```

## Use

The app's repo commits a `ramore.toml`; every command reads the one in the working directory.

```bash
ramore report                          # pull → archive → aggregate → render → publish
ramore report --no-publish --mode local
ramore report --dry-run                # pull and parse, no writes
ramore reset                           # wipe the store and the published history
```

## `ramore.toml`

Relative paths resolve against the file's directory.

```toml
name = "Example"                       # the site title: "Example reports"
store = "reports"                      # archive, aggregates, rendered sites, run logs, state

[source]
ssh = "example-live"                   # ssh host holding the access log
log = "/var/log/nginx/access.log"      # default
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

[site]
include = { poster = "docs/poster" }   # directories published under the site as-is

[publish]
worktree = "/tmp/example-ghpages"      # where the pages branch is checked out
branch = "gh-pages"                    # default
remote = "origin"                      # default
```

How the report works, what it stores and the log format it parses: [docs/report.md](docs/report.md).
