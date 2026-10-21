import json

from ... import SITE_STATE, config
from .. import timing
from . import (
    box,
    classification,
    errors,
    landing,
    performance,
    runs,
    sessions,
    traffic,
)
from .base import ASSET_DIR, Mode, RenderContext, build_context, copy_dir, write

PAGES = {
    "landing": landing,
    "traffic": traffic,
    "performance": performance,
    "errors": errors,
    "sessions": sessions,
    "classification": classification,
    "box": box,
    "runs": runs,
}


def render_all(mode: Mode = "local") -> RenderContext:
    ctx = build_context(mode)
    with timing.timed(f"render.{mode}.includes"):
        copy_dir(ASSET_DIR, ctx.out_dir / "assets")
        for name, src in config.get().site_include.items():
            copy_dir(src, ctx.out_dir / name)
    for name, page in PAGES.items():
        with timing.timed(f"render.{mode}.{name}"):
            page.render(ctx)
    state = {k: ctx.state_snapshot[k] for k in ("last_run", "last_event_ts")}
    write(ctx, SITE_STATE, json.dumps(state))
    write(ctx, ".nojekyll", "")
    return ctx
