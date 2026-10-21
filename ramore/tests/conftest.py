import os
import sys
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

from ramore import config, demo
from ramore.report.archive import annotate_routes
from ramore.report.classify import annotate_events, classify_sessions
from ramore.report.parse import parse_lines
from ramore.report.sessions import assign_sessions

# The box's directories the fake box keeps under its root.
BOX_DIRS = ("/etc/", "/usr/local/", "/var/log/")

_SSH = """#!{python}
import re, subprocess, sys
on_box = lambda s: re.sub(r"(?<![\\w./+=-])({dirs})", lambda m: {root!r} + m[1], s)
cmd = on_box(sys.argv[-1]).replace("sudo -n ", "")
script = on_box(sys.stdin.read()) if cmd.endswith("bash -s") else None
done = subprocess.run(["bash", "-c", cmd], input=script, stdin=None if script else subprocess.DEVNULL, text=True)
sys.exit(done.returncode)
"""
_STUB = """#!/bin/sh
echo "{name} $*" >> {calls}
[ "{name} $*" != "nginx -t" ] || exit "${{NGINX_T_EXIT:-0}}"
"""


@dataclass(frozen=True)
class FakeBox:
    """A directory standing in for the app's box, and the log of the stubbed commands."""

    root: Path
    calls: Path

    def path(self, on_box: str) -> Path:
        return self.root / on_box.lstrip("/")


@pytest.fixture(autouse=True)
def cfg(tmp_path: Path) -> config.Config:
    cfg = config.use(config.load(demo.write_app(tmp_path)))
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def fake_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeBox:
    """`ssh` first on PATH runs each command under the box's root with sudo dropped; `nginx`
    and `systemctl` stubs log their calls (`NGINX_T_EXIT` fails `nginx -t`)."""
    box = FakeBox(tmp_path / "box", tmp_path / "calls")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    box.root.mkdir()
    scripts = {
        "ssh": _SSH.format(
            python=sys.executable, root=str(box.root), dirs="|".join(BOX_DIRS)
        ),
        **{n: _STUB.format(name=n, calls=box.calls) for n in ("nginx", "systemctl")},
    }
    for name, text in scripts.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return box


def annotated(lines: list[str]) -> pl.DataFrame:
    """Request-log lines as the archive stores them, before the write."""
    df = assign_sessions(annotate_routes(parse_lines(lines)[0]))
    return annotate_events(df, classify_sessions(df))
