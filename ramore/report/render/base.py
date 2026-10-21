import datetime as dt
import html
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import polars as pl
from jinja2 import Environment, FileSystemLoader

from ... import config
from .. import aggregate, archive, classify, timing
from ..state import load

Mode = Literal["local", "public"]

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
ASSET_DIR = Path(__file__).parent.parent / "assets"
HOT_WINDOW_DAYS = 31
RUNS_SHOWN = 200

# The columns the pages read: the last day's events, and the `HOT_WINDOW_DAYS` window's (only the
# landing and errors pages read it). Projecting at scan time keeps the free-text columns and the
# addresses (`addr`, the only PII) out of the render, so neither site needs anonymizing.
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
WINDOW_RENDER_COLS = ["t", "status", "cs", "urt", "route_template"]
SESSION_RENDER_COLS = [
    "start",
    "end",
    "n_req",
    "ua_family",
    "bot_class",
    "score",
    *(r.name for r in classify.RULES),
]
# Run fields carrying exception text, which can echo raw log content: local site only.
_PUBLIC_RUN_DROP_FIELDS = frozenset({"error", "trace"})


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


def fmt_int(v) -> str:
    return "—" if _missing(v) else f"{int(v):,}"


def fmt_pct(v) -> str:
    return "—" if _missing(v) else f"{v * 100:.2f}%"


def fmt_ms(v) -> str:
    return "—" if _missing(v) else f"{v * 1000:.0f} ms"


def build_context(mode: Mode) -> RenderContext:
    cfg = config.get()
    now = dt.datetime.now(dt.timezone.utc)
    window_start = now.date() - dt.timedelta(days=HOT_WINDOW_DAYS)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters |= {"fmt_int": fmt_int, "fmt_pct": fmt_pct, "fmt_ms": fmt_ms}

    day_ago = now - dt.timedelta(days=1)
    with timing.timed(f"render.{mode}.read_hot"):
        events_hot = archive.read_hot(
            date_from=window_start, columns=WINDOW_RENDER_COLS
        )
        events_24h = archive.read_hot(
            date_from=day_ago.date(), columns=EVENT_RENDER_COLS
        )
    if not events_24h.is_empty():
        events_24h = events_24h.filter(pl.col("t") >= day_ago)
    with timing.timed(f"render.{mode}.load_sessions"):
        sessions_table = aggregate.load_sessions(window_start, SESSION_RENDER_COLS)
    with timing.timed(f"render.{mode}.load_aggregates"):
        hourly, daily = aggregate.load("hourly"), aggregate.load("daily")

    return RenderContext(
        mode=mode,
        out_dir=cfg.site_local_dir if mode == "local" else cfg.site_public_dir,
        env=env,
        events_hot=events_hot,
        events_24h=events_24h,
        sessions_table=sessions_table,
        hourly=hourly,
        daily=daily,
        state_snapshot=load().__dict__,
        now=now,
        runs_index=_read_runs_index(mode),
    )


def write(ctx: RenderContext, rel_path: str, text: str) -> None:
    p = ctx.out_dir / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def hint(text: str) -> str:
    return f"<p class='hint'>{html.escape(text)}</p>"


def df_to_html(df: pl.DataFrame, *, table_id: str) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in df.columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_cell(v)}</td>" for v in row) + "</tr>"
        for row in df.iter_rows()
    )
    return (
        f'<table class="dt" id="{table_id}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def render_template(ctx: RenderContext, template: str, **vars) -> str:
    base = {
        "mode": ctx.mode,
        "site_name": config.get().name,
        "now_iso": ctx.now.isoformat(timespec="seconds"),
        "state": ctx.state_snapshot,
        "active_page": "",
        "box_page": box_enabled(ctx),
    }
    return ctx.env.get_template(template).render(base | vars)


def box_enabled(ctx: RenderContext) -> bool:
    """The box page renders on the local site of an app with `[metrics]`."""
    return ctx.mode == "local" and config.get().metrics is not None


def plotly_div(
    fig_id: str, data: list, layout: dict | None = None, height: int = 320
) -> str:
    layout = {
        "margin": {"l": 40, "r": 20, "t": 30, "b": 40},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"color": "#cdd6f4", "size": 12},
        **(layout or {}),
    }
    spec = {
        "data": data,
        "layout": layout,
        "config": {"displaylogo": False, "responsive": True},
    }
    return (
        f'<div id="{fig_id}" style="height:{height}px"></div>'
        f'<script>Plotly.newPlot("{fig_id}", '
        f"{json.dumps(spec, default=str)});</script>"
    )


def traces(
    df: pl.DataFrame,
    x: str,
    y: str,
    group: str,
    style: Callable[[str], dict] = lambda _: {"type": "scatter", "mode": "lines"},
) -> list[dict]:
    """One plotly trace per value of `group`, `style(value)` merged in."""
    return [
        {
            "x": part[x].cast(pl.String).to_list(),
            "y": part[y].to_list(),
            "name": str(part[group][0]),
            **style(part[group][0]),
        }
        for part in df.sort(x).partition_by(group, maintain_order=True)
    ]


def class_style(kind: str, **extra) -> Callable[[str], dict]:
    """A trace style coloring each bot class."""

    def style(cls: str) -> dict:
        color = classify.BOT_CLASS_COLORS.get(cls)
        return {
            "type": kind,
            "marker": {"color": color},
            "line": {"color": color},
            **extra,
        }

    return style


def copy_dir(src: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return html.escape(str(v))


def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _read_runs_index(mode: Mode) -> list[dict]:
    """The last `RUNS_SHOWN` run records, newest first; a line a killed run left torn is skipped."""
    runs = []
    for f in sorted(config.get().run_logs_dir.glob("run-*.log")):
        for line in f.read_text().splitlines():
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    runs = sorted(runs, key=lambda r: r["ts"], reverse=True)[:RUNS_SHOWN]
    if mode == "public":
        runs = [
            {k: v for k, v in r.items() if k not in _PUBLIC_RUN_DROP_FIELDS}
            for r in runs
        ]
    return runs
