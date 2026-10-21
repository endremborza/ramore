import polars as pl

from ..classify import BOT_CLASS_COLORS, HUMAN_CLASSES
from .base import RenderContext, df_to_html, hint, plotly_div, render_template, write

TOP_N = 30


def render(ctx: RenderContext) -> None:
    sessions, events, daily = ctx.sessions_table, ctx.events_24h, ctx.daily

    write(
        ctx,
        "traffic.html",
        render_template(
            ctx,
            "traffic.html.j2",
            active_page="traffic",
            depth=0,
            bot_area_chart=_bot_area(daily),
            top_uas_table=_top_uas(sessions),
            top_refs_table=_top_referrers(events),
            top_paths_table=_top_paths(events, sessions),
            status_chart=_status_donut(daily),
        ),
    )


def _bot_area(daily: pl.DataFrame) -> str:
    if daily.is_empty():
        return hint("No data.")
    g = daily.group_by(["bucket", "bot_class"]).agg(pl.col("n").sum()).sort("bucket")
    classes = g["bot_class"].unique().to_list()
    traces = []
    for col in classes:
        sub = g.filter(pl.col("bot_class") == col)
        traces.append(
            {
                "x": sub["bucket"].cast(pl.String).to_list(),
                "y": sub["n"].to_list(),
                "name": col,
                "type": "scatter",
                "stackgroup": "one",
                "fillcolor": BOT_CLASS_COLORS.get(col),
                "line": {"width": 0.5, "color": BOT_CLASS_COLORS.get(col)},
            }
        )
    return plotly_div("bot-area", traces, layout={"yaxis": {"title": "requests"}})


def _top_uas(sessions: pl.DataFrame) -> str:
    if sessions.is_empty():
        return hint("No sessions.")
    by = "ua_family" if "ua_family" in sessions.columns else "ua"
    if by not in sessions.columns:
        return hint("No data.")
    if "bot_class" in sessions.columns:
        ct = (
            sessions.group_by(by)
            .agg(
                [
                    pl.len().alias("sessions"),
                    pl.col("bot_class").mode().first().alias("dominant_class"),
                ]
            )
            .sort("sessions", descending=True)
            .head(TOP_N)
        )
    else:
        ct = (
            sessions.group_by(by)
            .agg(pl.len().alias("sessions"))
            .sort("sessions", descending=True)
            .head(TOP_N)
        )
    return df_to_html(ct, table_id="top-uas")


def _top_referrers(events: pl.DataFrame) -> str:
    if events.is_empty() or "referrer_domain" not in events.columns:
        return hint("No data.")
    s = events.filter(pl.col("referrer_domain").fill_null("") != "")
    if s.is_empty():
        return hint("No referrer hits.")
    return df_to_html(
        s.group_by("referrer_domain")
        .agg(pl.len().alias("requests"))
        .sort("requests", descending=True)
        .head(TOP_N),
        table_id="top-refs",
    )


def _top_paths(events: pl.DataFrame, sessions: pl.DataFrame) -> str:
    if events.is_empty() or sessions.is_empty():
        return hint("No data.")
    human_ids = set(
        sessions.filter(pl.col("bot_class").is_in(list(HUMAN_CLASSES)))[
            "session_id"
        ].to_list()
    )
    s = events.filter(pl.col("session_id").is_in(human_ids))
    if s.is_empty():
        return hint("No human sessions yet.")
    return df_to_html(
        s.group_by("route_template")
        .agg(pl.len().alias("requests"))
        .sort("requests", descending=True)
        .head(TOP_N),
        table_id="top-paths",
    )


def _status_donut(daily: pl.DataFrame) -> str:
    if daily.is_empty():
        return hint("No data.")
    cutoff = daily["bucket"].max() - pl.duration(days=7)
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
