import subprocess
import tempfile
from pathlib import Path

from .. import config
from . import timing


def publish_to_ghpages(commit_msg: str | None = None) -> None:
    """Push the public site to the app repo's pages branch via a worktree."""
    repo_dir = config.get().root
    worktree = config.get().ghpages_worktree

    with timing.timed("publish.ensure_worktree"):
        _ensure_worktree(repo_dir, worktree)
    with timing.timed("publish.reset_clean_rsync"):
        _git(repo_dir, "-C", worktree.as_posix(), "reset", "--hard")
        _clean_worktree(worktree)
        _rsync(config.get().site_public_dir, worktree)
        (worktree / ".nojekyll").touch()
    with timing.timed("publish.git_add"):
        _git(repo_dir, "-C", worktree.as_posix(), "add", "-A")

    if not _has_changes(repo_dir, worktree):
        return

    with timing.timed("publish.git_commit"):
        _git(
            repo_dir,
            "-C",
            worktree.as_posix(),
            "commit",
            "-m",
            commit_msg or _default_msg(),
        )
    with timing.timed("publish.git_push"):
        _git(
            repo_dir,
            "-C",
            worktree.as_posix(),
            "push",
            config.get().ghpages_remote,
            config.get().ghpages_branch,
        )


def reset_ghpages_history() -> None:
    """Replace the entire gh-pages branch with a single empty commit (force-push),
    erasing every previously published report. A ``CNAME`` (custom domain) is carried
    over. The next publish repopulates the site fresh on top of this root commit."""
    repo_dir = config.get().root
    subprocess.run(["git", "-C", repo_dir.as_posix(), "worktree", "prune"], check=True)
    if config.get().ghpages_worktree.exists():
        subprocess.run(
            [
                "git",
                "-C",
                repo_dir.as_posix(),
                "worktree",
                "remove",
                "--force",
                config.get().ghpages_worktree.as_posix(),
            ],
            check=False,
        )
    # Drop the stale local branch so the next publish re-fetches the reset remote.
    subprocess.run(
        ["git", "-C", repo_dir.as_posix(), "branch", "-D", config.get().ghpages_branch],
        check=False,
    )

    cname = _current_cname(repo_dir)
    tmp = Path(tempfile.mkdtemp(prefix="ghpages-reset-"))
    tmp_branch = "gh-pages-reset"
    subprocess.run(
        ["git", "-C", repo_dir.as_posix(), "branch", "-D", tmp_branch], check=False
    )
    try:
        subprocess.run(
            [
                "git",
                "-C",
                repo_dir.as_posix(),
                "worktree",
                "add",
                "--orphan",
                "-b",
                tmp_branch,
                tmp.as_posix(),
            ],
            check=True,
        )
        (tmp / ".nojekyll").touch()
        if cname:
            (tmp / "CNAME").write_text(cname)
        _git(repo_dir, "-C", tmp.as_posix(), "add", "-A")
        _git(repo_dir, "-C", tmp.as_posix(), "commit", "-m", "reset published history")
        _git(
            repo_dir,
            "-C",
            tmp.as_posix(),
            "push",
            "-f",
            config.get().ghpages_remote,
            f"{tmp_branch}:{config.get().ghpages_branch}",
        )
    finally:
        subprocess.run(
            [
                "git",
                "-C",
                repo_dir.as_posix(),
                "worktree",
                "remove",
                "--force",
                tmp.as_posix(),
            ],
            check=False,
        )
        subprocess.run(
            ["git", "-C", repo_dir.as_posix(), "branch", "-D", tmp_branch], check=False
        )


def _current_cname(repo: Path) -> str | None:
    fetched = subprocess.run(
        [
            "git",
            "-C",
            repo.as_posix(),
            "fetch",
            config.get().ghpages_remote,
            config.get().ghpages_branch,
        ],
        capture_output=True,
    )
    if fetched.returncode != 0:
        return None
    shown = subprocess.run(
        ["git", "-C", repo.as_posix(), "show", "FETCH_HEAD:CNAME"],
        capture_output=True,
        text=True,
    )
    return shown.stdout if shown.returncode == 0 and shown.stdout.strip() else None


def _ensure_worktree(repo: Path, worktree: Path) -> None:
    # Prune stale registrations first — `/tmp` clean-ups (or any external
    # removal of the worktree dir) leave the registration intact, so a later
    # `worktree add` fails with "branch is already used by worktree at ...".
    subprocess.run(["git", "-C", repo.as_posix(), "worktree", "prune"], check=True)
    if worktree.exists() and (worktree / ".git").exists():
        return
    worktree.parent.mkdir(parents=True, exist_ok=True)
    remote_branch = f"{config.get().ghpages_remote}/{config.get().ghpages_branch}"
    has_remote_branch = (
        subprocess.run(
            [
                "git",
                "-C",
                repo.as_posix(),
                "ls-remote",
                "--exit-code",
                "--heads",
                config.get().ghpages_remote,
                config.get().ghpages_branch,
            ],
            capture_output=True,
        ).returncode
        == 0
    )
    if has_remote_branch:
        subprocess.run(
            [
                "git",
                "-C",
                repo.as_posix(),
                "fetch",
                config.get().ghpages_remote,
                config.get().ghpages_branch,
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                repo.as_posix(),
                "worktree",
                "add",
                "-B",
                config.get().ghpages_branch,
                worktree.as_posix(),
                remote_branch,
            ],
            check=True,
        )
    else:
        subprocess.run(
            [
                "git",
                "-C",
                repo.as_posix(),
                "worktree",
                "add",
                "--orphan",
                "-b",
                config.get().ghpages_branch,
                worktree.as_posix(),
            ],
            check=True,
        )


def _clean_worktree(worktree: Path) -> None:
    for child in worktree.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            subprocess.run(["rm", "-rf", child.as_posix()], check=True)
        else:
            child.unlink()


def _rsync(src: Path, dst: Path) -> None:
    src_str = src.as_posix().rstrip("/") + "/"
    subprocess.run(
        ["rsync", "-a", "--delete", "--exclude=.git", src_str, dst.as_posix()],
        check=True,
    )


def _has_changes(repo: Path, worktree: Path) -> bool:
    out = subprocess.run(
        ["git", "-C", worktree.as_posix(), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return bool(out.strip())


def _default_msg() -> str:
    import datetime as dt

    return "report " + dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], check=True)
