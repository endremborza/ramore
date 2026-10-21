from dataclasses import dataclass

from .. import config, ssh
from .state import Offset

FIRST_RUN_TAIL_BYTES = 64 * 2**20


@dataclass(frozen=True)
class Read:
    """Bytes [start, end) of `path`; `skip_first` drops the line the start may cut."""

    path: str
    start: int
    end: int
    skip_first: bool = False


@dataclass
class FetchResult:
    lines: list[str]
    offset: Offset
    rotated: bool


def fetch_new_lines(path: str, offset: Offset) -> FetchResult:
    """The lines appended to the box's log at `path` since `offset`.

    Every read stops at the size stat'd up front, so a line appended meanwhile waits for the next
    run. A changed inode is a rotation: the unread tail of `<path>.1` comes first. A missing log
    reads as empty and keeps `offset`.
    """
    host = config.get().ssh_host
    cur = _stat(host, path)
    if cur is None:
        return FetchResult([], offset, False)
    rot = (
        _stat(host, path + ".1") if offset.inode and cur.inode != offset.inode else None
    )
    reads, rotated = plan_reads(path, offset, cur, rot)
    lines = [line for r in reads for line in _read(host, r)]
    return FetchResult(lines, cur, rotated)


def plan_reads(
    path: str, offset: Offset, cur: Offset, rot: Offset | None
) -> tuple[list[Read], bool]:
    """What to read of a log now at `cur` (its rotated `.1` at `rot`), last read to `offset`."""
    if offset.inode == 0:
        start = max(0, cur.size - FIRST_RUN_TAIL_BYTES)
        return [Read(path, start, cur.size, skip_first=start > 0)], False
    if cur.inode != offset.inode:
        reads = [Read(path, 0, cur.size)]
        if rot is not None and rot.inode == offset.inode and rot.size > offset.size:
            reads.insert(0, Read(path + ".1", offset.size, rot.size))
        return reads, True
    if cur.size < offset.size:
        return [Read(path, 0, cur.size)], True
    if cur.size == offset.size:
        return [], False
    return [Read(path, offset.size, cur.size)], False


def _read(host: str, r: Read) -> list[str]:
    if r.end <= r.start:
        return []
    out = ssh.run(host, f"tail -c +{r.start + 1} {r.path} | head -c {r.end - r.start}")
    lines = out.decode("utf-8", errors="replace").split("\n")
    return lines[1:] if r.skip_first else lines


def _stat(host: str, path: str) -> Offset | None:
    out = ssh.run(host, f"stat -c '%i %s' {path} 2>/dev/null || true").split()
    return Offset(int(out[0]), int(out[1])) if out else None
