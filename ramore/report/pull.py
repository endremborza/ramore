from dataclasses import dataclass
from typing import Iterator

from .. import config, ssh
from .state import Offset

FIRST_RUN_TAIL_BYTES = 64 * 2**20
PULL_CHUNK_BYTES = 256 * 2**20


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


def fetch_chunks(path: str, offset: Offset) -> Iterator[FetchResult]:
    """The lines appended to the box's log at `path` since `offset`, in chunks of at most
    `PULL_CHUNK_BYTES` cut at line ends. A chunk's `offset` is where the log's next read starts
    once the chunk is archived, so a backlog is held a chunk at a time and an interrupted pull
    resumes after its last archived chunk.

    Every read stops at the size stat'd up front, so a line appended meanwhile waits for the next
    run. A changed inode is a rotation: the unread tail of `<path>.1` comes first, its chunks
    carrying the rotated file's inode. The last chunk ends at the stat'd size, an empty one when
    the reads hold no bytes (a rotation to an empty log). A missing log yields nothing.
    """
    host = config.get().ssh_host
    cur = _stat(host, path)
    if cur is None:
        return
    rot = (
        _stat(host, path + ".1") if offset.inode and cur.inode != offset.inode else None
    )
    reads, rotated = plan_reads(path, offset, cur, rot)
    inodes = {path: cur.inode, path + ".1": rot.inode if rot else 0}
    end = offset
    for r in reads:
        start, skip = r.start, r.skip_first
        while start < r.end:
            data = _read(host, r.path, start, min(r.end, start + PULL_CHUNK_BYTES))
            if not data:
                break
            last = start + len(data) >= r.end
            cut = len(data) if last else (data.rfind(b"\n") + 1 or len(data))
            text = data[:cut].decode("utf-8", errors="replace")
            lines = text.removesuffix("\n").split("\n")
            start += cut
            end = Offset(inodes[r.path], start)
            yield FetchResult(lines[1:] if skip else lines, end, rotated)
            skip = False
    if end != cur:
        yield FetchResult([], cur, rotated)


def fetch_new_lines(path: str, offset: Offset) -> FetchResult:
    """`fetch_chunks` in one piece, for a log small enough to hold whole."""
    lines: list[str] = []
    end = FetchResult(lines, offset, False)
    for chunk in fetch_chunks(path, offset):
        lines += chunk.lines
        end = chunk
    return FetchResult(lines, end.offset, end.rotated)


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


def _read(host: str, path: str, start: int, end: int) -> bytes:
    return ssh.run(host, f"tail -c +{start + 1} {path} | head -c {end - start}")


def _stat(host: str, path: str) -> Offset | None:
    out = ssh.run(host, f"stat -c '%i %s' {path} 2>/dev/null || true").split()
    return Offset(int(out[0]), int(out[1])) if out else None
