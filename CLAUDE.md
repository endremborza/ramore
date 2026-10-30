# ramore — agent guide

ramore monitoring and reporting: `ramore report` turns a site's nginx request log and box metrics into a static report site (README.md for use and the `ramore.toml` keys, docs/report.md for how it works). rankless is the first app it serves; nothing in ramore names an app.

## Where to look

- `ramore/config.py` — the app's `ramore.toml` as a frozen `Config`; commands `config.use(config.load())` once and every module reads `config.get()`, never a module-level path.
- `ramore/__main__.py` — the protocli dispatcher: `report` → `ramore/report/run.py`, `reset` → `ramore/report/reset.py`, `box` → `ramore/box/__init__.py`, `monitor`, `units`, `demo`.
- `ramore/monitor.py` (the outside probe), `ramore/alert.py` (incident tables and their mail), `ramore/units.py` (a host's systemd user units).
- `ramore/box/` — what ramore installs on the app's box: the request log format, the sampler (`sample.py`, copied to the box as is), the install script and its stamp. `ramore/ssh.py` runs commands there.
- `ramore/report/` — `run` (`fetch` over ssh, then `ingest`, which the demo shares): `pull` (byte offsets per log) → `parse` (requests) / `metrics` → `routes` (the template table from the app's API list and SvelteKit tree) → `sessions` → `classify` (each rule a sessions-table column) → `archive` (shackleton hot/cold) → `aggregate` → `render/` (one module per page, jinja2 `templates/`, autoescaped) → `publish` (git plumbing onto the pages branch, no checkout).
- `ramore/demo.py` — the example app and its synthetic traffic and box samples, in the box's log formats; `ramore demo` renders them, the tests load the same app.
- `ramore/tests/` — pytest; `conftest.py` loads the demo app into every test and `fake_box` serves a box from a temp dir (an `ssh` shim on PATH); `test_e2e.py` drives the CLI against it and a bare git remote.

## Rules

- `branb test` (ruff format + lint, then pytest with doctests and coverage, as CI runs it) before every change.
- A release is `branb tag "notes" minor|bug|major` on main; the tag push publishes to PyPI once the tests pass.
- `box/sample.py` runs on the box: stdlib only, Python 3.10+ (Ubuntu 22.04), no import from ramore.
- `make install` puts the `ramore` command on PATH with the locked versions; a bare `uv tool install` reads no lockfile.
- One literal per value: a constant is written once, docs name it.
- The render works on tens of millions of rows: the hot window and its sessions stay `LazyFrame` scans (`archive.scan_hot`, `aggregate.scan_sessions`) projected at scan time, and each page aggregates them with `collect(engine="streaming")`, a quantile in a query of its own; only the last day is collected. A rebuild or reclassify goes one day at a time.
