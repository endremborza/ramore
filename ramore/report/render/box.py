"""The box page, local site only: the metrics log's memory, disks and units over `BOX_DAYS`."""

import datetime as dt

import polars as pl

from .. import metrics
from ..metrics import GB
from .base import (
    RenderContext,
    box_enabled,
    df_to_html,
    hint,
    plotly_div,
    render_template,
    traces,
    write,
)

BOX_DAYS = 7
CHART_BUCKET = "10m"


def render(ctx: RenderContext) -> None:
    if not box_enabled(ctx):
        return
    df = metrics.read(ctx.now - dt.timedelta(days=BOX_DAYS))
    if df.is_empty():
        charts = {k: hint("No samples yet.") for k in ("fill", "anon", "mem", "fds")}
        latest = hint("No samples yet.")
    else:
        b = _bucketed(df)
        units = b.filter(pl.col("scope") == "unit")
        charts = {
            "fill": _chart(
                "box-fill", b.filter(pl.col("scope") != "unit"), "fill_pct", "%"
            ),
            "anon": _chart("unit-anon", units, "anon_gb", "GB"),
            "mem": _chart("unit-mem", units, "used_gb", "GB"),
            "fds": _chart("unit-fds", units, "fds", "open files"),
        }
        latest = _latest_table(df)
    write(
        ctx,
        "box.html",
        render_template(
            ctx,
            "box.html.j2",
            active_page="box",
            depth=0,
            days=BOX_DAYS,
            bucket=CHART_BUCKET,
            fill_chart=charts["fill"],
            anon_chart=charts["anon"],
            mem_chart=charts["mem"],
            fds_chart=charts["fds"],
            latest_table=latest,
        ),
    )


def _series(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.when(pl.col("scope") == "host")
        .then(pl.lit("memory"))
        .when(pl.col("scope") == "mount")
        .then(pl.lit("disk ") + pl.col("name"))
        .otherwise(pl.col("name"))
        .alias("series"),
        (pl.col("used") / pl.col("limit") * 100).round(2).alias("fill_pct"),
        (pl.col("used") / GB).round(3).alias("used_gb"),
        (pl.col("anon") / GB).round(3).alias("anon_gb"),
    )


def _bucketed(df: pl.DataFrame) -> pl.DataFrame:
    """Each series' maximum per `CHART_BUCKET`, so a week stays a light page."""
    return (
        _series(df)
        .group_by_dynamic("t", every=CHART_BUCKET, group_by=["scope", "series"])
        .agg(pl.col("fill_pct", "used_gb", "anon_gb", "fds").max())
    )


def _chart(fig_id: str, df: pl.DataFrame, y: str, unit: str) -> str:
    if df.is_empty():
        return hint("No data.")
    return plotly_div(
        fig_id,
        traces(df, "t", y, "series"),
        {"yaxis": {"title": unit, "rangemode": "tozero"}},
    )


def _latest_table(df: pl.DataFrame) -> str:
    last = _series(df.filter(pl.col("t") == pl.col("t").max()))
    return df_to_html(
        last.select(
            "series",
            "used_gb",
            (pl.col("limit") / GB).round(3).alias("limit_gb"),
            "fill_pct",
            "anon_gb",
            "fds",
            "fd_limit",
            "procs",
        ).sort("series"),
        table_id="box-latest",
    )
