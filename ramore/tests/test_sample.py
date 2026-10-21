import json
from pathlib import Path

import polars as pl

from ramore.box import sample
from ramore.report import metrics


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _fake_box(tmp_path: Path) -> tuple[str, str]:
    proc, cg = tmp_path / "proc", tmp_path / "cgroup"
    _write(
        proc / "meminfo", "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n"
    )
    for pid, n_fds, soft in ((101, 3, "1024"), (102, 1, "4096"), (103, 2, "unlimited")):
        for fd in range(n_fds):
            _write(proc / str(pid) / "fd" / str(fd), "")
        _write(
            proc / str(pid) / "limits",
            "Limit Soft Limit Hard Limit Units\n"
            f"Max open files            {soft}                 65536                files\n",
        )
    nginx = cg / "system.slice" / "nginx.service"
    _write(nginx / "memory.current", "5000\n")
    _write(nginx / "memory.max", "max\n")
    _write(nginx / "memory.stat", "anon 300\nfile 100\n")
    _write(nginx / "cgroup.procs", "101\n999\n")
    web = (
        cg
        / "user.slice"
        / "user@1000.service"
        / "app.slice"
        / "app-web.slice"
        / "web@1.service"
    )
    _write(web / "memory.current", "7000\n")
    _write(web / "memory.max", "8000\n")
    _write(web / "memory.stat", "file 10\nanon 600\n")
    _write(web / "cgroup.procs", "")
    _write(web / "worker" / "cgroup.procs", "102\n103\n")
    _write(cg / "system.slice" / "other.service" / "cgroup.procs", "1\n")
    return str(proc), str(cg)


def test_sample_rows(tmp_path: Path):
    proc, cg = _fake_box(tmp_path)
    cfg = {"mounts": [str(tmp_path)], "units": ["nginx.service", "web@*.service"]}
    rows = sample.sample(cfg, proc=proc, cgroup=cg)
    host, mount, nginx, web = rows
    assert host["used"] == 600 * 1024 and host["limit"] == 1000 * 1024
    assert mount["name"] == str(tmp_path) and 0 < mount["used"] <= mount["limit"]
    assert {
        k: nginx[k] for k in ("name", "used", "limit", "anon", "fds", "fd_limit")
    } == {
        "name": "nginx.service",
        "used": 5000,
        "limit": None,
        "anon": 300,
        "fds": 3,
        "fd_limit": 1024,
    }
    assert nginx["procs"] == 2  # 999 exited: listed, no fds
    assert (web["fds"], web["fd_limit"], web["procs"], web["anon"]) == (3, 4096, 2, 600)
    assert {k for r in rows for k in r} <= set(metrics.SCHEMA)


def test_main_appends_lines_metrics_parses(tmp_path: Path, monkeypatch):
    proc, cg = _fake_box(tmp_path)
    monkeypatch.setattr(sample, "PROC", proc)
    monkeypatch.setattr(sample, "CGROUP", cg)
    log = tmp_path / "metrics.log"
    conf = tmp_path / "sample.json"
    units = ["nginx.service", "web@*.service"]
    conf.write_text(json.dumps({"log": str(log), "mounts": ["/"], "units": units}))
    sample.main(["sample.py", str(conf)])
    sample.main(["sample.py", str(conf)])
    df, fail = metrics.parse_lines(log.read_text().split("\n"))
    assert fail == 0
    assert len(df) == 2 * 4  # host, /, nginx, web — twice
    assert df.schema["t"] == pl.Datetime("us", "UTC")
    metrics.write(df)
    assert len(metrics.read(df["t"].min())) == df.n_unique(["t", "scope", "name"])
