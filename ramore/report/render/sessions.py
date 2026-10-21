import polars as pl

from ..classify import BOT_CLASS_COLORS, BOT_CLASS_ORDER
from .base import RenderContext, hint, plotly_div, render_template, write

_BUCKETS = [(1, 1), (2, 2), (3, 5), (6, 10), (11, 20), (21, 50), (51, None)]
_BUCKET_LABELS = ["1", "2", "3–5", "6–10", "11–20", "21–50", "51+"]


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


def _req_distribution(sessions: pl.DataFrame) -> str:
    if sessions.is_empty() or "n_req" not in sessions.columns:
        return hint("No session data yet.")

    if "bot_class" not in sessions.columns:
        sessions = sessions.with_columns(pl.lit("unknown").alias("bot_class"))

    df = sessions.with_columns(
        pl.col("n_req")
        .map_elements(_bucket_label, return_dtype=pl.String)
        .alias("bucket")
    )
    counts = df.group_by(["bucket", "bot_class"]).agg(pl.len().alias("count"))

    present = set(counts["bot_class"].unique().to_list())
    traces = []
    for cls in BOT_CLASS_ORDER:
        if cls not in present:
            continue
        sub = counts.filter(pl.col("bot_class") == cls)
        bucket_counts = {
            row["bucket"]: row["count"] for row in sub.iter_rows(named=True)
        }
        traces.append(
            {
                "x": _BUCKET_LABELS,
                "y": [bucket_counts.get(b, 0) for b in _BUCKET_LABELS],
                "name": cls,
                "type": "bar",
                "marker": {"color": BOT_CLASS_COLORS.get(cls)},
            }
        )

    return plotly_div(
        "req-dist",
        traces,
        layout={
            "barmode": "stack",
            "xaxis": {"title": "requests per session"},
            "yaxis": {"title": "sessions"},
        },
        height=380,
    )


def _bucket_label(n: int) -> str:
    for (lo, hi), label in zip(_BUCKETS, _BUCKET_LABELS):
        if hi is None or n <= hi:
            return label
    return _BUCKET_LABELS[-1]
