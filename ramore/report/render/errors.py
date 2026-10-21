import datetime as dt

import polars as pl

from .base import RenderContext, df_to_html, hint, render_template, write

ERROR_TABLES = [
    ("err-5xx", lambda d: d["status"] >= 500),
    (
        "err-4xx",
        lambda d: (d["status"] >= 400) & (d["status"] < 500) & (d["status"] != 404),
    ),
    ("err-429", lambda d: d["status"] == 429),
]
ERROR_DAYS = 7


def render(ctx: RenderContext) -> None:
    cutoff = ctx.now - dt.timedelta(days=ERROR_DAYS)
    events = (
        ctx.events_hot.filter(pl.col("t") >= cutoff)
        if not ctx.events_hot.is_empty()
        else ctx.events_hot
    )

    tables = {tid: _err_table(events, mask, tid) for tid, mask in ERROR_TABLES}

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


def _err_table(events: pl.DataFrame, mask_fn, table_id: str) -> str:
    if events.is_empty():
        return hint("No data.")
    s = events.filter(mask_fn(events))
    if s.is_empty():
        return hint("None.")
    result = (
        s.group_by(["route_template", "status"])
        .agg(
            [
                pl.len().alias("n"),
                pl.col("t").max().alias("last_seen"),
            ]
        )
        .sort("n", descending=True)
        .head(50)
        .with_columns(pl.col("last_seen").dt.strftime("%Y-%m-%d %H:%M UTC"))
    )
    return df_to_html(result, table_id=table_id)
