import polars as pl

from ... import config
from .. import classify, routes
from ..routes import Kind
from ..sessions import SESSION_IDLE_MIN
from .base import RenderContext, plotly_div, render_template, write


def render(ctx: RenderContext) -> None:
    sessions = ctx.sessions_table
    table = routes.table()
    write(
        ctx,
        "classification.html",
        render_template(
            ctx,
            "classification.html.j2",
            active_page="classification",
            depth=0,
            bot_classes=[
                (c, classify.BOT_CLASS_COLORS[c]) for c in classify.BOT_CLASS_ORDER
            ],
            ua_families=classify.UA_FAMILIES,
            hard_rules=classify.HARD_RULES,
            soft_rules=classify.SOFT_RULES,
            soft_human_threshold=classify.SOFT_HUMAN_THRESHOLD,
            soft_bot_threshold=classify.SOFT_BOT_THRESHOLD,
            route_groups=[
                ("Bot-only routes", sorted(classify.bot_routes())),
                ("SvelteKit data prefetch routes", sorted(table.of_kind(Kind.DATA))),
                ("API routes", sorted(table.of_kind(Kind.API))),
            ],
            search=config.get().search,
            session_idle_min=SESSION_IDLE_MIN,
            n_sessions=len(sessions),
            rule_rates=_rule_rates(sessions),
            score_dist_chart=_score_dist_chart(sessions),
        ),
    )


def _rule_rates(sessions: pl.DataFrame) -> dict[str, float]:
    """The share of sessions each rule fired on."""
    if sessions.is_empty():
        return {}
    return sessions.select(pl.col(r.name).mean() for r in classify.RULES).row(
        0, named=True
    )


def _score_dist_chart(sessions: pl.DataFrame) -> str:
    """Sessions per soft score, among those no hard rule fired on."""
    soft = sessions.filter(~pl.any_horizontal(r.name for r in classify.HARD_RULES))
    if soft.is_empty():
        return ""
    counts = soft.group_by("score").len().sort("score")
    threshold = {"type": "line", "y0": 0, "y1": 1, "yref": "paper"}
    return plotly_div(
        "score-dist",
        [
            {
                "x": counts["score"].to_list(),
                "y": counts["len"].to_list(),
                "type": "bar",
                "marker": {"color": "#89b4fa"},
                "name": "sessions",
            }
        ],
        layout={
            "xaxis": {"title": "soft score", "dtick": 1},
            "yaxis": {"title": "sessions"},
            "shapes": [
                {
                    **threshold,
                    "x0": x,
                    "x1": x,
                    "line": {"color": color, "dash": "dot", "width": 1.5},
                }
                for x, color in (
                    (classify.SOFT_HUMAN_THRESHOLD - 0.5, "#a6e3a1"),
                    (classify.SOFT_BOT_THRESHOLD + 0.5, "#f38ba8"),
                )
            ],
        },
        height=260,
    )
