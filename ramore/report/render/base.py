import datetime as dt
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import polars as pl
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ... import config
from .. import aggregate, archive, timing
from ..state import load

Mode = Literal["local", "public"]

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
ASSET_DIR = Path(__file__).parent.parent / "assets"
HOT_WINDOW_DAYS = 31
IP_HASH_LEN = 10

# Columns the render actually consumes from the hot window. Projecting to these
# at scan time keeps the ~40M-row window from materializing its large free-text
# fields, and drops `addr` (the only PII) so events need no anonymization.
EVENT_RENDER_COLS = [
    "t",
    "session_id",
    "status",
    "cs",
    "urt",
    "bot_class",
    "route_template",
    "referrer_domain",
    "size",
]


@dataclass
class RenderContext:
    mode: Mode
    out_dir: Path
    env: Environment
    events_hot: pl.DataFrame
    events_24h: pl.DataFrame
    sessions_table: pl.DataFrame
    hourly: pl.DataFrame
    daily: pl.DataFrame
    state_snapshot: dict
    now: dt.datetime
    runs_index: list[dict]


def build_context(mode: Mode) -> RenderContext:
    cfg = config.get()
    out_dir = cfg.site_local_dir if mode == "local" else cfg.site_public_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sessions").mkdir(exist_ok=True)
    (out_dir / "runs").mkdir(exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["fmt_int"] = lambda v: (
        f"{int(v):,}" if v is not None and not _isnan(v) else "—"
    )
    env.filters["fmt_pct"] = lambda v: (
        f"{v * 100:.2f}%" if v is not None and not _isnan(v) else "—"
    )
    env.filters["fmt_ms"] = lambda v: (
        f"{v * 1000:.0f} ms" if v is not None and not _isnan(v) else "—"
    )
    env.filters["fmt_dt"] = lambda v: _parse_ts(v).strftime("%Y-%m-%d %H:%M UTC")

    today = dt.date.today()

    with timing.timed(f"render.{mode}.read_hot"):
        events_hot = archive.read_hot(
            date_from=today - dt.timedelta(days=HOT_WINDOW_DAYS),
            columns=EVENT_RENDER_COLS,
        )

    cutoff_24h = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    events_24h = (
        events_hot.filter(pl.col("t") >= cutoff_24h)
        if not events_hot.is_empty()
        else events_hot
    )

    with timing.timed(f"render.{mode}.load_sessions"):
        sessions_table = aggregate.load_sessions(
            date_from=today - dt.timedelta(days=HOT_WINDOW_DAYS),
            exclude=["ua"] if mode == "public" else None,
        )
        if mode == "public" and not sessions_table.is_empty():
            sessions_table = _anonymize_sessions(sessions_table)

    with timing.timed(f"render.{mode}.load_aggregates"):
        hourly = aggregate.load_hourly()
        daily = aggregate.load_daily()

    state = load()
    runs_idx = _read_runs_index(mode)

    return RenderContext(
        mode=mode,
        out_dir=out_dir,
        env=env,
        events_hot=events_hot,
        events_24h=events_24h,
        sessions_table=sessions_table,
        hourly=hourly,
        daily=daily,
        state_snapshot=state.__dict__,
        now=dt.datetime.now(dt.timezone.utc),
        runs_index=runs_idx,
    )


def write(ctx: RenderContext, rel_path: str, html: str) -> None:
    p = ctx.out_dir / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html)


def hint(text: str) -> str:
    return f"<p class='hint'>{text}</p>"


def df_to_html(df: pl.DataFrame, *, table_id: str, escape: bool = True) -> str:
    return df.to_pandas().to_html(
        classes="dt",
        index=False,
        table_id=table_id,
        border=0,
        escape=escape,
    )


def render_template(ctx: RenderContext, template: str, **vars) -> str:
    tpl = ctx.env.get_template(template)
    base_vars = {
        "mode": ctx.mode,
        "site_name": config.get().name,
        "now_iso": ctx.now.isoformat(timespec="seconds"),
        "state": ctx.state_snapshot,
        "active_page": "",
        "box_page": box_enabled(ctx),
    }
    base_vars.update(vars)
    return tpl.render(**base_vars)


def box_enabled(ctx: RenderContext) -> bool:
    """The box page renders on the local site of an app with `[metrics]`."""
    return ctx.mode == "local" and config.get().metrics is not None


def plotly_div(
    fig_id: str, data: list, layout: dict | None = None, height: int = 320
) -> str:
    layout = layout or {}
    layout.setdefault("margin", {"l": 40, "r": 20, "t": 30, "b": 40})
    layout.setdefault("paper_bgcolor", "rgba(0,0,0,0)")
    layout.setdefault("plot_bgcolor", "rgba(0,0,0,0)")
    layout.setdefault("font", {"color": "#cdd6f4", "size": 12})
    spec = {
        "data": data,
        "layout": layout,
        "config": {"displaylogo": False, "responsive": True},
    }
    return (
        f'<div id="{fig_id}" style="height:{height}px"></div>'
        f'<script>Plotly.newPlot("{fig_id}", '
        f"{json.dumps(spec, default=_json_default)});</script>"
    )


def time_series_traces(df: pl.DataFrame, x: str, y: str, group: str) -> list[dict]:
    out = []
    for part in df.sort(x).partition_by(group, maintain_order=True):
        name = part[group][0]
        out.append(
            {
                "x": part[x].cast(pl.String).to_list(),
                "y": part[y].to_list(),
                "name": str(name),
                "type": "scatter",
                "mode": "lines",
            }
        )
    return out


def copy_assets(ctx: RenderContext) -> None:
    if not ASSET_DIR.exists():
        return
    target = ctx.out_dir / "assets"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ASSET_DIR, target)


def copy_includes(ctx: RenderContext) -> None:
    """Publish each of the app's `[site] include` directories under its name."""
    for name, src in config.get().site_include.items():
        target = ctx.out_dir / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)


def _anon_addr(ts_col: str) -> pl.Expr:
    """Day-salted hash of the address; same salt as truncating ``ts_col`` to a day."""
    return (
        pl.concat_str(
            [pl.col(ts_col).dt.strftime("%Y-%m-%d"), pl.lit("|"), pl.col("addr")]
        )
        .hash(742)
        .cast(pl.String)
        .str.slice(0, IP_HASH_LEN)
        .alias("addr")
    )


def _anonymize_sessions(df: pl.DataFrame) -> pl.DataFrame:
    if "addr" in df.columns:
        df = df.with_columns(_anon_addr("start"))
    return df.drop([c for c in ("ua",) if c in df.columns])


# Free-text run fields carrying exception messages / tracebacks. They can echo raw
# log content (client IPs, local filesystem paths) and are stripped from the public
# site; the local site keeps them for debugging.
_PUBLIC_RUN_DROP_FIELDS = frozenset({"error", "trace", "traceback"})


def _read_runs_index(mode: Mode) -> list[dict]:
    out = []
    logs = config.get().run_logs_dir
    if not logs.exists():
        return out
    for f in sorted(logs.glob("run-*.log")):
        for line in f.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    out.sort(key=lambda r: r.get("ts", ""), reverse=True)
    out = out[:200]
    if mode == "public":
        out = [
            {k: v for k, v in r.items() if k not in _PUBLIC_RUN_DROP_FIELDS}
            for r in out
        ]
    return out


def _isnan(v) -> bool:
    try:
        import math

        return math.isnan(v)
    except (TypeError, ValueError):
        return False


def _parse_ts(v) -> dt.datetime:
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, str):
        return dt.datetime.fromisoformat(v)
    return dt.datetime.fromtimestamp(float(v), tz=dt.timezone.utc)


def _json_default(o):
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    if hasattr(o, "tolist"):
        return o.tolist()
    if isinstance(o, set):
        return list(o)
    return str(o)
