import polars as pl

from .base import (
    RenderContext,
    df_to_html,
    hint,
    plotly_div,
    render_template,
    traces,
    write,
)

TOP_ROUTES_CHARTED = 8
SLOW_SHOWN = 100
CACHE_DAYS = 7
_QUANTILES = ((50, 0.5), (95, 0.95), (99, 0.99))


def render(ctx: RenderContext) -> None:
    write(
        ctx,
        "performance.html",
        render_template(
            ctx,
            "performance.html.j2",
            active_page="performance",
            depth=0,
            route_table=_route_table(ctx.events_24h),
            p99_chart=_p99_per_route_chart(ctx.hourly),
            cache_chart=_cache_hit_chart(ctx.hourly),
            slow_table=_slow_requests(ctx.events_24h),
        ),
    )


def _route_table(events: pl.DataFrame) -> str:
    if events.is_empty():
        return hint("No data.")
    status, urt = pl.col("status"), pl.col("urt")
    g = (
        events.group_by("route_template")
        .agg(
            pl.len().alias("n"),
            *(
                (urt.quantile(q) * 1000).round(1).alias(f"urt_p{p}_ms")
                for p, q in _QUANTILES
            ),
            ((status >= 500).mean() * 100).round(2).alias("err_5xx"),
            (((status >= 400) & (status < 500)).mean() * 100).round(2).alias("err_4xx"),
            ((pl.col("cs") == "HIT").mean() * 100).round(2).alias("cache_hit"),
        )
        .sort("n", descending=True)
    )
    return df_to_html(g, table_id="route-perf")


def _p99_per_route_chart(hourly: pl.DataFrame) -> str:
    if hourly.is_empty():
        return hint("No data.")
    recent = hourly.filter(
        pl.col("bucket") >= hourly["bucket"].max() - pl.duration(days=1)
    )
    top = (
        recent.group_by("route_template")
        .agg(pl.col("n").sum())
        .sort(["n", "route_template"], descending=[True, False])
        .head(TOP_ROUTES_CHARTED)["route_template"]
    )
    g = (
        recent.filter(pl.col("route_template").is_in(top.implode()))
        .group_by("bucket", "route_template")
        .agg((pl.col("urt_p99").max() * 1000).alias("p99_ms"))
    )
    return plotly_div(
        "p99-routes",
        traces(g, "bucket", "p99_ms", "route_template"),
        layout={"yaxis": {"title": "p99 ms"}},
    )


def _cache_hit_chart(hourly: pl.DataFrame) -> str:
    if hourly.is_empty():
        return hint("No data.")
    g = (
        hourly.filter(
            pl.col("bucket") >= hourly["bucket"].max() - pl.duration(days=CACHE_DAYS)
        )
        .group_by("bucket")
        .agg(
            (
                pl.col("n").filter(pl.col("cs") == "HIT").sum()
                / pl.col("n").sum()
                * 100
            ).alias("hit_pct")
        )
        .with_columns(name=pl.lit("cache hit %"))
    )
    style = {
        "type": "scatter",
        "mode": "lines",
        "fill": "tozeroy",
        "line": {"color": "#89b4fa"},
    }
    return plotly_div(
        "cache-hit",
        traces(g, "bucket", "hit_pct", "name", lambda _: style),
        layout={"yaxis": {"title": "cache hit %", "range": [0, 100]}},
    )


def _slow_requests(events: pl.DataFrame) -> str:
    if events.is_empty():
        return hint("No data.")
    slow = (
        events.filter(pl.col("urt").is_not_null())
        .sort("urt", descending=True)
        .head(SLOW_SHOWN)
        .select(
            "t",
            "route_template",
            "status",
            (pl.col("urt") * 1000).round(1).alias("urt_ms"),
            "cs",
            "size",
        )
    )
    return df_to_html(slow, table_id="slow-reqs")
