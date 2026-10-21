import json

from ... import SITE_STATE
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
from .base import Mode, RenderContext, build_context, copy_includes, write


def render_all(mode: Mode = "local") -> RenderContext:
    ctx = build_context(mode)
    with timing.timed(f"render.{mode}.includes"):
        copy_includes(ctx)
    for name, mod in [
        ("landing", landing),
        ("traffic", traffic),
        ("performance", performance),
        ("errors", errors),
        ("sessions", sessions),
        ("classification", classification),
        ("box", box),
        ("runs", runs),
    ]:
        with timing.timed(f"render.{mode}.{name}"):
            mod.render(ctx)
    state = {k: ctx.state_snapshot[k] for k in ("last_run", "last_event_ts")}
    write(ctx, SITE_STATE, json.dumps(state))
    return ctx
