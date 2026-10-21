"""Scrub all report history and start fresh.

Run before pointing the report at a new box: wipes the local store's archive, aggregates,
sites, run logs and state, and (unless `--local-only`) replaces the pages branch's history with
one commit holding only `publish.CARRIED`, so no previously published report survives. The next
run rebuilds everything from the new box's log.
"""

import shutil
import sys

from .. import config
from . import publish


def wipe_local() -> None:
    cfg = config.get()
    for d in cfg.store_dirs:
        if d.exists():
            shutil.rmtree(d)
    for f in (cfg.state_path, cfg.salts_path):
        f.unlink(missing_ok=True)
    cfg.ensure_dirs()


def main(
    *,
    local_only: bool = False,
    yes: bool = False,
    config_file: str = config.CONFIG_NAME,
) -> None:
    """Erase the report history: the local store and, without --local-only, the published site."""
    local_only = local_only or config.use(config.load(config_file)).publish is None
    scope = (
        "the local report store"
        if local_only
        else "the local report store AND the entire published pages history (force-push)"
    )
    if not yes:
        resp = input(f"Permanently erase {scope}. Type 'scrub' to proceed: ")
        if resp.strip() != "scrub":
            print("aborted", file=sys.stderr)
            raise SystemExit(1)

    wipe_local()
    print("local report history wiped")
    if not local_only:
        publish.reset()
        print("pages history reset to a single commit")
