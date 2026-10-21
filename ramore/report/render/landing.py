from dataclasses import dataclass

import polars as pl

from ... import config
from ..classify import BOT_CLASS_COLORS, BOT_CLASS_ORDER
from .base import (
    RenderContext,
    class_style,
    fmt_ms,
    fmt_pct,
    hint,
    plotly_div,
    render_template,
    traces,
    write,
)

WINDOWS = {"Last 1h": 1, "Last 24h": 24, "Last 7d": 7 * 24, "Last 30d": 30 * 24}


@dataclass(frozen=True)
class Card:
    label: str
    value: str
    sub: str = ""


@dataclass(frozen=True)
class ClassShare:
    cls: str
    n: int
    pct: float
    color: str


@dataclass(frozen=True)
class Kpis:
    cards: list[Card]
    classes: list[ClassShare]


def render(ctx: RenderContext) -> None:
    login = config.get().login
    write(
        ctx,
        "index.html",
        render_template(
            ctx,
            "landing.html.j2",
            active_page="landing",
            depth=0,
            windows={
                label: kpis(ctx.events_hot, ctx.sessions_table, hours)
                for label, hours in WINDOWS.items()
            },
            traffic_chart=_traffic_chart(ctx.daily),
            bot_chart=_bot_session_chart(ctx.sessions_table),
            login_chart=_login_chart(ctx.events_hot),
            login_label=login.label if login else "",
        ),
    )


def kpis(events: pl.DataFrame, sessions: pl.DataFrame, hours: int) -> Kpis:
    """The KPIs of the last `hours` before the latest request."""
    empty = Kpis([Card("Requests", "0")], [])
    if events.is_empty():
        return empty
    cutoff = events["t"].max() - pl.duration(hours=hours)
    login = config.get().login
    login_counts = {}
    if login is not None:
        ok, failed = _login_outcomes(login)
        login_counts = {"logins": ok.sum(), "failed_logins": failed.sum()}
    k = (
        events.lazy()
        .filter(pl.col("t") >= cutoff)
        .select(
            n=pl.len(),
            n_5xx=(pl.col("status") >= 500).sum(),
            cache_hit=(pl.col("cs") == "HIT").sum(),
            p99=pl.col("urt").quantile(0.99),
            **login_counts,
        )
        .collect(streaming=True)
        .row(0, named=True)
    )
    n = k["n"]
    if n == 0:
        return empty
    classes = _session_classes(sessions, cutoff)
    n_sessions = sum(classes.values())
    cards = [
        Card("Requests", f"{n:,}"),
        Card("Sessions", f"{n_sessions:,}"),
        Card("5xx rate", fmt_pct(k["n_5xx"] / n)),
        Card("p99 urt", fmt_ms(k["p99"])),
        Card("Cache hit", fmt_pct(k["cache_hit"] / n)),
    ]
    if login is not None:
        failed = k["failed_logins"]
        cards.append(
            Card(
                login.label, f"{k['logins']:,}", f"{failed:,} failed" if failed else ""
            )
        )
    shares = [
        ClassShare(
            cls, classes[cls], classes[cls] / n_sessions * 100, BOT_CLASS_COLORS[cls]
        )
        for cls in BOT_CLASS_ORDER
        if classes.get(cls)
    ]
    return Kpis(cards, shares)


def _session_classes(sessions: pl.DataFrame, cutoff: pl.Expr) -> dict[str, int]:
    """Sessions with a request at or after `cutoff`, per bot class."""
    if sessions.is_empty():
        return {}
    return dict(
        sessions.lazy()
        .filter(pl.col("end") >= cutoff)
        .group_by("bot_class")
        .len()
        .collect(streaming=True)
        .iter_rows()
    )


def _login_outcomes(login: config.Login) -> tuple[pl.Expr, pl.Expr]:
    """A login hit answered with the success status, and one answered with a 4xx."""
    hit = pl.col("route_template") == login.route
    return hit & (pl.col("status") == login.ok_status), hit & (pl.col("status") >= 400)


def _traffic_chart(daily: pl.DataFrame) -> str:
    if daily.is_empty():
        return hint("No data yet.")
    g = (
        daily.group_by("bucket")
        .agg(pl.col("n").sum())
        .with_columns(name=pl.lit("Requests"))
    )
    return plotly_div(
        "traffic-30d",
        traces(
            g,
            "bucket",
            "n",
            "name",
            lambda _: {"type": "bar", "marker": {"color": "#89b4fa"}},
        ),
        layout={"yaxis": {"title": "requests"}},
    )


def _login_chart(events: pl.DataFrame) -> str:
    login = config.get().login
    if login is None:
        return ""
    ok, failed = _login_outcomes(login)
    o = events.filter(ok | failed).select("t", ok=ok, failed=failed)
    if o.is_empty():
        return hint(f"No {login.label} yet.")
    colors = {"Logins": "#a6e3a1", "Failed": "#f38ba8"}
    g = (
        o.group_by(pl.col("t").dt.truncate("1d").alias("day"))
        .agg(pl.col("ok").sum().alias("Logins"), pl.col("failed").sum().alias("Failed"))
        .unpivot(index="day", variable_name="outcome", value_name="n")
    )
    return plotly_div(
        "logins",
        traces(
            g,
            "day",
            "n",
            "outcome",
            lambda o: {"type": "bar", "marker": {"color": colors[o]}},
        ),
        layout={"barmode": "stack", "yaxis": {"title": "logins"}},
    )


def _bot_session_chart(sessions: pl.DataFrame) -> str:
    if sessions.is_empty():
        return hint("No sessions yet.")
    g = sessions.group_by(
        pl.col("start").dt.truncate("1d").alias("day"), "bot_class"
    ).len()
    return plotly_div(
        "bot-stack",
        traces(g, "day", "len", "bot_class", class_style("bar")),
        layout={"barmode": "stack", "yaxis": {"title": "sessions"}},
    )
