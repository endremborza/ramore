import datetime as dt

import polars as pl

from .base import RenderContext, df_to_html, hint, render_template, write

_STATUS = pl.col("status")
ERROR_TABLES = [
    ("err-5xx", _STATUS >= 500),
    ("err-4xx", (_STATUS >= 400) & (_STATUS < 500) & (_STATUS != 404)),
    ("err-429", _STATUS == 429),
]
ERROR_DAYS = 7


def render(ctx: RenderContext) -> None:
    per_route = (
        None
        if ctx.events_hot is None
        else _errors_per_route(ctx.events_hot, ctx.now - dt.timedelta(days=ERROR_DAYS))
    )
    tables = {tid: _err_table(per_route, mask, tid) for tid, mask in ERROR_TABLES}

    write(
        ctx,
        "errors.html",
        render_template(
            ctx,
            "errors.html.j2",
            active_page="errors",
            depth=0,
            table_5xx=tables["err-5xx"],
            table_4xx=tables["err-4xx"],
            table_429=tables["err-429"],
        ),
    )


def _errors_per_route(events: pl.LazyFrame, cutoff: dt.datetime) -> pl.DataFrame:
    """Each (route, error status) since `cutoff`: its count and last time."""
    return (
        events.filter((pl.col("t") >= cutoff) & (_STATUS >= 400))
        .group_by("route_template", "status")
        .agg(pl.len().alias("n"), pl.col("t").max().alias("last_seen"))
        .collect(engine="streaming")
    )


def _err_table(per_route: pl.DataFrame | None, mask: pl.Expr, table_id: str) -> str:
    if per_route is None:
        return hint("No data.")
    s = per_route.filter(mask)
    if s.is_empty():
        return hint("None.")
    result = (
        s.sort(["n", "route_template", "status"], descending=[True, False, False])
        .head(50)
        .with_columns(pl.col("last_seen").dt.strftime("%Y-%m-%d %H:%M UTC"))
    )
    return df_to_html(result, table_id=table_id)
