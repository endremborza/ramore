import subprocess
from dataclasses import dataclass

from .. import config
from .state import State

FIRST_RUN_TAIL_LINES = 200_000


@dataclass
class FetchResult:
    lines: list[str]
    new_inode: int
    new_size: int
    rotated: bool


def fetch_new_lines(state: State) -> FetchResult:
    """SSH-pull new log lines starting from state.last_size byte offset.

    Handles rotation: if the inode changed, also pulls the unread tail of the rotated
    `<log>.1` before reading the new active log.
    """
    cfg = config.get()
    ssh_host, log, rotated_log = cfg.ssh_host, cfg.log_path, cfg.log_path + ".1"
    inode, size = _stat(ssh_host, log)
    rotated = state.last_inode and inode != state.last_inode

    if state.last_inode == 0:
        # First run ever: only ingest the most recent FIRST_RUN_TAIL_LINES lines so
        # we don't try to fetch a multi-gigabyte log.
        lines = _ssh_lines(ssh_host, f"tail -n {FIRST_RUN_TAIL_LINES} {log}")
        return FetchResult(lines=lines, new_inode=inode, new_size=size, rotated=False)

    if rotated:
        rot_inode, rot_size = _stat_or_none(ssh_host, rotated_log)
        old_tail: list[str] = []
        if rot_inode == state.last_inode and rot_size and rot_size > state.last_size:
            old_tail = _ssh_lines(
                ssh_host,
                f"tail -c +{state.last_size + 1} {rotated_log}",
            )
        new_full = _ssh_lines(ssh_host, f"cat {log}")
        return FetchResult(
            lines=old_tail + new_full,
            new_inode=inode,
            new_size=size,
            rotated=True,
        )

    if size < state.last_size:
        # Truncated without rotation; treat as fresh.
        new_full = _ssh_lines(ssh_host, f"cat {log}")
        return FetchResult(lines=new_full, new_inode=inode, new_size=size, rotated=True)

    if size == state.last_size:
        return FetchResult(lines=[], new_inode=inode, new_size=size, rotated=False)

    tail = _ssh_lines(ssh_host, f"tail -c +{state.last_size + 1} {log}")
    return FetchResult(lines=tail, new_inode=inode, new_size=size, rotated=False)


def _ssh(host: str, cmd: str) -> str:
    return subprocess.check_output(
        ["ssh", "-o", "StrictHostKeyChecking=no", host, cmd],
        text=True,
    )


def _ssh_lines(host: str, cmd: str) -> list[str]:
    out = _ssh(host, cmd)
    return out.split("\n")


def _stat(host: str, path: str) -> tuple[int, int]:
    out = _ssh(host, f"stat -c '%i %s' {path}").strip().split()
    return int(out[0]), int(out[1])


def _stat_or_none(host: str, path: str) -> tuple[int | None, int | None]:
    try:
        return _stat(host, path)
    except subprocess.CalledProcessError:
        return None, None
