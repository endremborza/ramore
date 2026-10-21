import polars as pl

from ... import config
from ..classify import BOT_CLASS_COLORS, BOT_CLASS_ORDER
from .base import RenderContext, copy_assets, hint, plotly_div, render_template, write

WINDOWS = [
    ("Last 1h", "1h", 1),
    ("Last 24h", "24h", 24),
    ("Last 7d", "7d", 7 * 24),
    ("Last 30d", "30d", 30 * 24),
]


def render(ctx: RenderContext) -> None:
    copy_assets(ctx)

    kpis = {
        key: _kpi_block(label, ctx.events_hot, ctx.sessions_table, hours)
        for label, key, hours in WINDOWS
    }

    write(
        ctx,
        "index.html",
        render_template(
            ctx,
            "landing.html.j2",
            active_page="landing",
            depth=0,
            kpis=kpis,
            traffic_chart=_traffic_chart(ctx.daily),
            bot_chart=_bot_session_chart(ctx.sessions_table),
            login_chart=_login_chart(ctx.events_hot),
            login_label=login.label if (login := config.get().login) else "",
        ),
    )


def _kpi_block(
    label: str, events: pl.DataFrame, sessions: pl.DataFrame, hours: int
) -> dict:
    if events.is_empty():
        return {"label": label, "html": _empty_kpi_html()}
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
        return {"label": label, "html": _empty_kpi_html()}

    classes = _session_classes(sessions, cutoff)
    n_sessions = sum(classes.values())
    cards = [
        _card("Requests", f"{n:,}"),
        _card("Sessions", f"{n_sessions:,}"),
        _card("5xx rate", _pct(k["n_5xx"] / n)),
        _card("p99 urt", _ms(k["p99"])),
        _card("Cache hit", _pct(k["cache_hit"] / n)),
    ]
    if login is not None:
        cards.append(_login_card(login, k["logins"], k["failed_logins"]))
    if n_sessions:
        cards.append(_breakdown_card(classes))
    return {"label": label, "html": "\n".join(cards)}


def _session_classes(sessions: pl.DataFrame, cutoff: pl.Expr) -> dict[str, int]:
    """Sessions with a request at or after ``cutoff``, per bot class."""
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


def _card(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="kpi-card">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f"{sub_html}"
        f"</div>"
    )


def _login_outcomes(login: config.Login) -> tuple[pl.Expr, pl.Expr]:
    """A login hit answered with the success status, and one answered with a 4xx."""
    hit = pl.col("route_template") == login.route
    return hit & (pl.col("status") == login.ok_status), hit & (pl.col("status") >= 400)


def _login_card(login: config.Login, ok: int, failed: int) -> str:
    return _card(login.label, f"{ok:,}", f"{failed:,} failed" if failed else "")


def _breakdown_card(class_counts: dict[str, int]) -> str:
    counts = {cls: class_counts.get(cls, 0) for cls in BOT_CLASS_ORDER}
    total = sum(counts.values())
    if total == 0:
        return _card("Class breakdown", "—")
    segs = []
    legend = []
    for cls, n in counts.items():
        if n == 0:
            continue
        pct = n / total * 100
        color = BOT_CLASS_COLORS.get(cls, "#cdd6f4")
        segs.append(
            f'<span class="seg" style="background:{color};width:{pct:.2f}%" '
            f'title="{cls}: {n} ({pct:.1f}%)"></span>'
        )
        legend.append(
            f'<span class="legend-item"><span class="dot" style="background:{color}"></span>'
            f"{cls} {n}</span>"
        )
    return (
        '<div class="kpi-card kpi-breakdown">'
        '<div class="kpi-label">Sessions by class</div>'
        f'<div class="bot-bar">{"".join(segs)}</div>'
        f'<div class="bot-legend">{" ".join(legend)}</div>'
        "</div>"
    )


def _empty_kpi_html() -> str:
    return _card("Requests", "0")


def _pct(v: float) -> str:
    import math

    if v is None or math.isnan(v):
        return "—"
    return f"{v * 100:.2f}%"


def _ms(v: float) -> str:
    import math

    if v is None or math.isnan(v):
        return "—"
    return f"{v * 1000:.0f} ms"


def _traffic_chart(daily: pl.DataFrame) -> str:
    if daily.is_empty():
        return hint("No data yet.")
    g = daily.group_by("bucket").agg(pl.col("n").sum()).sort("bucket")
    return plotly_div(
        "traffic-30d",
        [
            {
                "x": g["bucket"].cast(pl.String).to_list(),
                "y": g["n"].to_list(),
                "type": "bar",
                "marker": {"color": "#89b4fa"},
                "name": "Requests",
            }
        ],
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
    g = (
        o.group_by(pl.col("t").dt.truncate("1d").alias("day"))
        .agg(pl.col("ok").sum(), pl.col("failed").sum())
        .sort("day")
    )
    days = g["day"].cast(pl.String).to_list()
    return plotly_div(
        "logins",
        [
            {
                "x": days,
                "y": g[col].to_list(),
                "name": name,
                "type": "bar",
                "marker": {"color": color},
            }
            for col, name, color in (
                ("ok", "Logins", "#a6e3a1"),
                ("failed", "Failed", "#f38ba8"),
            )
        ],
        layout={"barmode": "stack", "yaxis": {"title": "logins"}},
    )


def _bot_session_chart(sessions: pl.DataFrame) -> str:
    if sessions.is_empty():
        return hint("No sessions yet.")
    s = sessions.with_columns(pl.col("start").dt.truncate("1d").alias("day"))
    pivot = s.group_by(["day", "bot_class"]).agg(pl.len().alias("n")).sort("day")
    classes = pivot["bot_class"].unique().to_list()
    traces = []
    for cls in classes:
        sub = pivot.filter(pl.col("bot_class") == cls)
        traces.append(
            {
                "x": sub["day"].cast(pl.String).to_list(),
                "y": sub["n"].to_list(),
                "name": cls,
                "type": "bar",
                "marker": {"color": BOT_CLASS_COLORS.get(cls, "#cdd6f4")},
            }
        )
    return plotly_div(
        "bot-stack",
        traces,
        layout={"barmode": "stack", "yaxis": {"title": "sessions"}},
    )
