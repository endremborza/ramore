import polars as pl

from .base import RenderContext, df_to_html, hint, render_template, write


def render(ctx: RenderContext) -> None:
    runs = ctx.runs_index
    if not runs:
        table_html = hint("No run history yet.")
    else:
        df = pl.DataFrame(runs, infer_schema_length=len(runs), strict=False)
        if "ts" in df.columns:
            df = df.with_columns(
                pl.col("ts")
                .map_elements(
                    lambda t: f'<a href="{t}.html">{t}</a>', return_dtype=pl.String
                )
                .alias("run")
            )
            cols = ["run"] + [c for c in df.columns if c not in ("ts", "run")]
            df = df.select(cols)
        table_html = df_to_html(df, table_id="runs", escape=False)

    write(
        ctx,
        "runs/index.html",
        render_template(
            ctx,
            "runs_index.html.j2",
            active_page="runs",
            depth=1,
            runs_table=table_html,
        ),
    )

    for r in runs:
        ts = r.get("ts", "")
        if not ts:
            continue
        write(
            ctx,
            f"runs/{ts}.html",
            render_template(
                ctx,
                "run_detail.html.j2",
                active_page="runs",
                depth=1,
                run=r,
            ),
        )
