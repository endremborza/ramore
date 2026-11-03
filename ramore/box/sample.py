"""One sample of the box's memory, disks and units, appended to the metrics log as JSON lines.

Runs on the box with the stdlib alone, as root under ramore's sampler timer:
`python3 -I -S sample.py <config.json>`. The config names the log, the mounts and the unit globs.
Each line is one scope at time `t`: `host` (memory), a mount (disk) or a unit, matched by its
cgroup directory, which systemd names after the unit. `used`/`limit` is the fill of each scope;
a unit adds `anon` memory, the open fds and soft limit of its process nearest that limit, and
its process count.
"""

import fnmatch
import json
import os
import sys
import time

PROC = "/proc"
CGROUP = "/sys/fs/cgroup"


def sample(cfg: dict, proc: str, cgroup: str) -> list[dict]:
    t = round(time.time(), 3)
    units = _unit_dirs(cgroup, cfg["units"])
    rows = [
        _host(proc),
        *(_mount(m) for m in cfg["mounts"]),
        *(_unit(name, units[name], proc) for name in sorted(units)),
    ]
    return [{"t": t, **r} for r in rows]


def _host(proc: str) -> dict:
    info = {}
    with open(f"{proc}/meminfo") as f:
        for line in f:
            key, rest = line.split(":", 1)
            info[key] = int(rest.split()[0]) * 1024
    total = info["MemTotal"]
    return {
        "scope": "host",
        "name": "",
        "used": total - info["MemAvailable"],
        "limit": total,
    }


def _mount(path: str) -> dict:
    st = os.statvfs(path)
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    return {
        "scope": "mount",
        "name": path,
        "used": used,
        "limit": used + st.f_bavail * st.f_frsize,
    }


def _unit_dirs(cgroup: str, globs: list[str]) -> dict[str, str]:
    found = {}
    for root, dirs, _ in os.walk(cgroup):
        rest = []
        for d in dirs:
            if any(fnmatch.fnmatchcase(d, g) for g in globs):
                found[d] = os.path.join(root, d)
            else:
                rest.append(d)
        dirs[:] = rest
    return found


def _unit(name: str, path: str, proc: str) -> dict:
    pids = _pids(path)
    open_files = []
    for pid in pids:
        try:
            fds = len(os.listdir(f"{proc}/{pid}/fd"))
            open_files.append((fds, _open_files_limit(f"{proc}/{pid}/limits")))
        except (FileNotFoundError, ProcessLookupError):
            continue
    fds, fd_limit = max(open_files, key=_fd_fill, default=(0, None))
    return {
        "scope": "unit",
        "name": name,
        "used": _read_int(f"{path}/memory.current"),
        "limit": _read_int(f"{path}/memory.max"),
        "anon": _stat_field(f"{path}/memory.stat", "anon"),
        "fds": fds,
        "fd_limit": fd_limit,
        "procs": len(pids),
    }


def _fd_fill(open_files: tuple[int, int | None]) -> tuple[bool, float]:
    """Ranks a process by its open fds against its own soft limit; unlimited ones rank below
    every limited one, by count."""
    fds, limit = open_files
    return (limit is not None, fds / limit if limit else fds)


def _pids(path: str) -> list[int]:
    pids = []
    for root, _, files in os.walk(path):
        if "cgroup.procs" in files:
            with open(os.path.join(root, "cgroup.procs")) as f:
                pids.extend(int(p) for p in f.read().split())
    return pids


def _open_files_limit(path: str) -> int | None:
    with open(path) as f:
        for line in f:
            if line.startswith("Max open files"):
                soft = line.split()[3]
                return None if soft == "unlimited" else int(soft)
    return None


def _read_int(path: str) -> int | None:
    try:
        with open(path) as f:
            value = f.read().strip()
    except FileNotFoundError:
        return None
    return None if value == "max" else int(value)


def _stat_field(path: str, key: str) -> int | None:
    try:
        with open(path) as f:
            for line in f:
                k, v = line.split()
                if k == key:
                    return int(v)
    except FileNotFoundError:
        pass
    return None


def main(argv: list[str]) -> None:
    with open(argv[1]) as f:
        cfg = json.load(f)
    rows = sample(cfg, PROC, CGROUP)
    out = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
    fd = os.open(cfg["log"], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, out.encode())
    finally:
        os.close(fd)


if __name__ == "__main__":
    main(sys.argv)
