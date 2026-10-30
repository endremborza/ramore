import polars as pl

from .base import (
    RenderContext,
    class_style,
    hint,
    plotly_div,
    render_template,
    stream,
    traces,
    write,
)

# Requests per session: bucket upper bounds, each bucket labelled by its range.
REQ_BREAKS = [1, 2, 5, 10, 20, 50]
REQ_LABELS = ["1", "2", "3–5", "6–10", "11–20", "21–50", "51+"]


def render(ctx: RenderContext) -> None:
    write(
        ctx,
        "sessions/index.html",
        render_template(
            ctx,
            "sessions_index.html.j2",
            active_page="sessions",
            depth=1,
            chart=_req_distribution(ctx.sessions_table),
        ),
    )


def _req_distribution(sessions: pl.LazyFrame | None) -> str:
    counts = stream(
        sessions,
        lambda s: s.group_by(
            pl.col("n_req")
            .bin_intervals(REQ_BREAKS, labels=REQ_LABELS, right_closed=True)
            .cast(pl.String)
            .alias("bucket"),
            "bot_class",
        ).len(),
    )
    if counts.is_empty():
        return hint("No session data yet.")
    return plotly_div(
        "req-dist",
        traces(counts, "bucket", "len", "bot_class", class_style("bar")),
        layout={
            "barmode": "stack",
            "xaxis": {
                "title": "requests per session",
                "categoryorder": "array",
                "categoryarray": REQ_LABELS,
            },
            "yaxis": {"title": "sessions"},
        },
        height=380,
    )
