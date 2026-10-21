"""The public site on the app repo's pages branch, by git plumbing in the app's repo: the site
directory goes through a temporary index into a commit on the branch's remote head, then a push;
nothing is checked out. `CARRIED` files on the branch (GitHub writes `CNAME` for a custom domain)
pass into every commit."""

import datetime as dt
import os
import subprocess
import tempfile
from pathlib import Path

from .. import config

CARRIED = ("CNAME",)
_LS_REMOTE_NO_MATCH = 2


def publish() -> bool:
    """Commit `site-public/` onto the pages branch and push it; False when the site is unchanged."""
    head = _remote_head()
    git_dir = _git("rev-parse", "--absolute-git-dir")
    with tempfile.TemporaryDirectory() as tmp:
        index = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        site = config.get().site_public_dir
        _git(
            "--git-dir",
            git_dir,
            "--work-tree",
            ".",
            "add",
            "--all",
            "--force",
            ".",
            cwd=site,
            env=index,
        )
        if carried := _carried(head):
            _git("update-index", "--index-info", stdin=carried, env=index)
        tree = _git("write-tree", env=index)
    if head is not None and tree == _git("rev-parse", f"{head}^{{tree}}"):
        return False
    msg = "report " + dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
    _push(_git("commit-tree", tree, *(["-p", head] if head else []), "-m", msg))
    return True


def reset() -> None:
    """Replace the pages branch's history with one commit of the `CARRIED` files (force push)."""
    tree = _git("mktree", stdin=_carried(_remote_head()))
    _push(_git("commit-tree", tree, "-m", "reset published history"), "--force")


def _remote_head() -> str | None:
    """The pages branch's head on the remote, fetched; None when the remote has no such branch."""
    t = _target()
    listed = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", t.remote, t.branch],
        cwd=config.get().root,
        stdout=subprocess.DEVNULL,
    )
    if listed.returncode == _LS_REMOTE_NO_MATCH:
        return None
    listed.check_returncode()
    _git("fetch", "--quiet", t.remote, t.branch)
    return _git("rev-parse", "FETCH_HEAD")


def _carried(head: str | None) -> str:
    """`head`'s `CARRIED` entries in `ls-tree` form, which `update-index` and `mktree` read."""
    entries = _git("ls-tree", head, *CARRIED) if head else ""
    return entries + "\n" if entries else ""


def _push(commit: str, *flags: str) -> None:
    t = _target()
    _git("push", "--quiet", *flags, t.remote, f"{commit}:refs/heads/{t.branch}")


def _git(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: str = "",
) -> str:
    """`git args` in the app's repo (or `cwd`); its stdout, stripped."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd or config.get().root,
        env=env,
        input=stdin,
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.strip()


def _target() -> config.Publish:
    target = config.get().publish
    if target is None:
        raise RuntimeError("the app's ramore.toml has no [publish] section")
    return target
