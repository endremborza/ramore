import polars as pl

from ..classify import HUMAN_CLASSES
from .base import (
    RenderContext,
    class_style,
    df_to_html,
    hint,
    plotly_div,
    render_template,
    traces,
    write,
)

TOP_N = 30
STATUS_DAYS = 7


def render(ctx: RenderContext) -> None:
    write(
        ctx,
        "traffic.html",
        render_template(
            ctx,
            "traffic.html.j2",
            active_page="traffic",
            depth=0,
            bot_area_chart=_bot_area(ctx.daily),
            top_uas_table=_top_uas(ctx.sessions_table),
            top_refs_table=_top_referrers(ctx.events_24h),
            top_paths_table=_top_paths(ctx.events_24h),
            status_chart=_status_donut(ctx.daily),
        ),
    )


def _bot_area(daily: pl.DataFrame) -> str:
    if daily.is_empty():
        return hint("No data.")
    g = daily.group_by("bucket", "bot_class").agg(pl.col("n").sum())
    return plotly_div(
        "bot-area",
        traces(g, "bucket", "n", "bot_class", class_style("scatter", stackgroup="one")),
        layout={"yaxis": {"title": "requests"}},
    )


def _top_uas(sessions: pl.DataFrame) -> str:
    if sessions.is_empty():
        return hint("No sessions.")
    per_class = (
        sessions.lazy()
        .group_by("ua_family", "bot_class")
        .len()
        .collect(engine="streaming")
    )
    top = (
        per_class.group_by("ua_family")
        .agg(
            pl.col("len").sum().alias("sessions"),
            pl.col("bot_class")
            .sort_by("len", "bot_class")
            .last()
            .alias("dominant_class"),
        )
        .sort(["sessions", "ua_family"], descending=[True, False])
        .head(TOP_N)
    )
    return df_to_html(top, table_id="top-uas")


def _top_referrers(events: pl.DataFrame) -> str:
    return _top(
        events.filter(pl.col("referrer_domain").fill_null("") != ""),
        "referrer_domain",
        "top-refs",
        "No referrer hits.",
    )


def _top_paths(events: pl.DataFrame) -> str:
    return _top(
        events.filter(pl.col("bot_class").is_in(list(HUMAN_CLASSES))),
        "route_template",
        "top-paths",
        "No human sessions yet.",
    )


def _top(events: pl.DataFrame, by: str, table_id: str, none: str) -> str:
    if events.is_empty():
        return hint(none)
    return df_to_html(
        events.group_by(by)
        .len("requests")
        .sort(["requests", by], descending=[True, False])
        .head(TOP_N),
        table_id=table_id,
    )


def _status_donut(daily: pl.DataFrame) -> str:
    if daily.is_empty():
        return hint("No data.")
    cutoff = daily["bucket"].max() - pl.duration(days=STATUS_DAYS)
    g = (
        daily.filter(pl.col("bucket") >= cutoff)
        .group_by("status_family")
        .agg(pl.col("n").sum())
        .sort("status_family")
    )
    return plotly_div(
        "status-donut",
        [
            {
                "labels": g["status_family"].to_list(),
                "values": g["n"].to_list(),
                "type": "pie",
                "hole": 0.5,
            }
        ],
    )
