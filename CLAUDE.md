# ramore — agent guide

ramore monitoring and reporting: `ramore report` turns a site's nginx access log into a static report site (README.md for use and the `ramore.toml` keys, docs/report.md for how it works). rankless is the first app it serves; nothing in ramore names an app.

## Where to look

- `ramore/config.py` — the app's `ramore.toml` as a frozen `Config`; commands `config.use(config.load())` once and every module reads `config.get()`, never a module-level path.
- `ramore/__main__.py` — the protocli dispatcher: `report` → `ramore/report/run.py`, `reset` → `ramore/report/reset.py`.
- `ramore/report/` — `pull` (ssh, byte offsets) → `parse` → `routes` (the template table from the app's API list and SvelteKit tree) → `sessions` → `classify` → `archive` (shackleton hot/cold) → `aggregate` → `render/` (one module per page, jinja2 `templates/`) → `publish` (pages-branch worktree).
- `ramore/tests/` — pytest; `conftest.py` loads a throwaway app config into every test.

## Rules

- `make check` (ruff format + lint) and `make test` before every change.
- polars stays below 2 (`pyproject.toml`): the suite breaks on 2.0. `make install` puts the `ramore` command on PATH with the locked versions; a bare `uv tool install` reads no lockfile.
- One literal per value: a constant is written once, docs name it.
- The render works on tens of millions of rows: project columns at scan time, aggregate with `collect(streaming=True)`, never an eager `group_by` over a long-string column of the hot window.
