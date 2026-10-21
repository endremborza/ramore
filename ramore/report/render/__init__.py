from .. import timing
from . import classification, errors, landing, performance, runs, sessions, traffic
from .base import Mode, RenderContext, build_context, copy_includes


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
        ("runs", runs),
    ]:
        with timing.timed(f"render.{mode}.{name}"):
            mod.render(ctx)
    return ctx
