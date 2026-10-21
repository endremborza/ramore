import polars as pl

from .base import RenderContext, df_to_html, hint, plotly_div, render_template, write


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
    e = events.with_columns(
        [
            (pl.col("status") >= 500).alias("is_5xx"),
            ((pl.col("status") >= 400) & (pl.col("status") < 500)).alias("is_4xx"),
            (pl.col("cs") == "HIT").alias("is_hit"),
        ]
    )
    g = (
        e.group_by("route_template")
        .agg(
            [
                pl.len().alias("n"),
                pl.col("urt").quantile(0.5).alias("urt_p50"),
                pl.col("urt").quantile(0.95).alias("urt_p95"),
                pl.col("urt").quantile(0.99).alias("urt_p99"),
                pl.col("is_5xx").mean().alias("err_5xx"),
                pl.col("is_4xx").mean().alias("err_4xx"),
                pl.col("is_hit").mean().alias("cache_hit"),
            ]
        )
        .sort("n", descending=True)
        .with_columns(
            [
                (pl.col("urt_p50") * 1000).round(1).alias("urt_p50_ms"),
                (pl.col("urt_p95") * 1000).round(1).alias("urt_p95_ms"),
                (pl.col("urt_p99") * 1000).round(1).alias("urt_p99_ms"),
                (pl.col("err_5xx") * 100).round(2),
                (pl.col("err_4xx") * 100).round(2),
                (pl.col("cache_hit") * 100).round(2),
            ]
        )
        .drop(["urt_p50", "urt_p95", "urt_p99"])
    )
    return df_to_html(g, table_id="route-perf")


def _p99_per_route_chart(hourly: pl.DataFrame) -> str:
    if hourly.is_empty():
        return hint("No data.")
    cutoff = hourly["bucket"].max() - pl.duration(days=1)
    s = hourly.filter(pl.col("bucket") >= cutoff)
    if s.is_empty():
        return hint("No recent hourly data.")
    top = (
        s.group_by("route_template")
        .agg(pl.col("n").sum())
        .sort("n", descending=True)
        .head(8)["route_template"]
        .to_list()
    )
    g = (
        s.filter(pl.col("route_template").is_in(top))
        .group_by(["bucket", "route_template"])
        .agg(pl.col("urt_p99").max())
        .sort("bucket")
    )
    traces = []
    for part in g.partition_by("route_template", maintain_order=False):
        route = part["route_template"][0]
        traces.append(
            {
                "x": part["bucket"].cast(pl.String).to_list(),
                "y": [
                    v * 1000 if v is not None else None
                    for v in part["urt_p99"].to_list()
                ],
                "name": str(route),
                "type": "scatter",
                "mode": "lines",
            }
        )
    return plotly_div("p99-routes", traces, layout={"yaxis": {"title": "p99 ms"}})


def _cache_hit_chart(hourly: pl.DataFrame) -> str:
    if hourly.is_empty():
        return hint("No data.")
    cutoff = hourly["bucket"].max() - pl.duration(days=7)
    s = hourly.filter(pl.col("bucket") >= cutoff)
    g = (
        s.group_by("bucket")
        .agg(
            [
                pl.col("n").filter(pl.col("cs") == "HIT").sum().alias("hit_n"),
                pl.col("n").sum().alias("total_n"),
            ]
        )
        .with_columns(
            (pl.col("hit_n") / pl.col("total_n").clip(lower_bound=1) * 100).alias(
                "hit_pct"
            )
        )
        .sort("bucket")
    )
    return plotly_div(
        "cache-hit",
        [
            {
                "x": g["bucket"].cast(pl.String).to_list(),
                "y": g["hit_pct"].to_list(),
                "type": "scatter",
                "mode": "lines",
                "fill": "tozeroy",
                "line": {"color": "#89b4fa"},
                "name": "cache hit %",
            }
        ],
        layout={"yaxis": {"title": "cache hit %", "range": [0, 100]}},
    )


def _slow_requests(events: pl.DataFrame) -> str:
    if events.is_empty():
        return hint("No data.")
    cols = [
        c
        for c in ("t", "route_template", "status", "urt", "cs", "size")
        if c in events.columns
    ]
    s = (
        events.filter(pl.col("urt").is_not_null())
        .sort("urt", descending=True)
        .head(100)
        .select(cols)
    )
    if "urt" in s.columns:
        s = s.with_columns((pl.col("urt") * 1000).round(1).alias("urt_ms")).drop("urt")
    return df_to_html(s, table_id="slow-reqs")
