# ramore — agent guide

ramore monitoring and reporting: `ramore report` turns a site's nginx request log and box metrics into a static report site (README.md for use and the `ramore.toml` keys, docs/report.md for how it works). rankless is the first app it serves; nothing in ramore names an app.

## Where to look

- `ramore/config.py` — the app's `ramore.toml` as a frozen `Config`; commands `config.use(config.load())` once and every module reads `config.get()`, never a module-level path.
- `ramore/__main__.py` — the protocli dispatcher: `report` → `ramore/report/run.py`, `reset` → `ramore/report/reset.py`, `box` → `ramore/box/__init__.py`, `monitor`, `units`.
- `ramore/monitor.py` (the outside probe), `ramore/alert.py` (incident tables and their mail), `ramore/units.py` (a host's systemd user units).
- `ramore/box/` — what ramore installs on the app's box: the request log format, the sampler (`sample.py`, copied to the box as is), the install script and its stamp. `ramore/ssh.py` runs commands there.
- `ramore/report/` — `pull` (ssh, byte offsets per log) → `parse` (requests) / `metrics` → `routes` (the template table from the app's API list and SvelteKit tree) → `sessions` → `classify` → `archive` (shackleton hot/cold) → `aggregate` → `render/` (one module per page, jinja2 `templates/`) → `publish` (pages-branch worktree).
- `ramore/tests/` — pytest; `conftest.py` loads a throwaway app config into every test.

## Rules

- `make check` (ruff format + lint) and `make test` before every change.
- `box/sample.py` runs on the box: stdlib only, Python 3.10+ (Ubuntu 22.04), no import from ramore.
- polars stays below 2 (`pyproject.toml`): the suite breaks on 2.0. `make install` puts the `ramore` command on PATH with the locked versions; a bare `uv tool install` reads no lockfile.
- One literal per value: a constant is written once, docs name it.
- The render works on tens of millions of rows: project columns at scan time, aggregate with `collect(streaming=True)`, never an eager `group_by` over a long-string column of the hot window.
