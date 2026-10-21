from .base import RenderContext, render_template, write


def render(ctx: RenderContext) -> None:
    runs = ctx.runs_index
    columns = list(dict.fromkeys(k for r in runs for k in r if k != "ts"))
    write(
        ctx,
        "runs/index.html",
        render_template(
            ctx,
            "runs_index.html.j2",
            active_page="runs",
            depth=1,
            runs=runs,
            columns=columns,
        ),
    )
    for r in runs:
        write(
            ctx,
            f"runs/{r['ts']}.html",
            render_template(
                ctx, "run_detail.html.j2", active_page="runs", depth=1, run=r
            ),
        )
