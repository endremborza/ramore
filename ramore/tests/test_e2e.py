"""The commands end to end through the CLI: `ramore report` against a fake box serving the demo's
traffic and samples, publishing to a bare git remote; then `units` and `reset`."""

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from polars.testing import assert_frame_equal

from ramore import alert, box, config, demo, units
from ramore.__main__ import main
from ramore.report import aggregate, archive, parse, publish

from .conftest import FakeBox

CNAME = "reports.example.org\n"


def _git(*args: str | Path, stdin: str = "") -> str:
    return subprocess.run(
        ["git", *map(str, args)],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def app(cfg: config.Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The demo app as a git repo whose `origin` already has a pages branch holding the `CNAME`
    GitHub writes for a custom domain; the bare remote's path."""
    for k, v in {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "ramore",
        "GIT_AUTHOR_EMAIL": "ramore@example.org",
        "GIT_COMMITTER_NAME": "ramore",
        "GIT_COMMITTER_EMAIL": "ramore@example.org",
    }.items():
        monkeypatch.setenv(k, v)
    remote = tmp_path / "remote.git"
    _git("init", "--quiet", "--bare", remote)
    _git("-C", cfg.root, "init", "--quiet")
    _git("-C", cfg.root, "remote", "add", "origin", remote)
    blob = _git("-C", cfg.root, "hash-object", "-w", "--stdin", stdin=CNAME)
    tree = _git("-C", cfg.root, "mktree", stdin=f"100644 blob {blob}\tCNAME\n")
    commit = _git("-C", cfg.root, "commit-tree", tree, "-m", "Create CNAME")
    _git(
        "-C",
        cfg.root,
        "push",
        "--quiet",
        "origin",
        f"{commit}:refs/heads/{config.DEFAULT_BRANCH}",
    )
    return remote


def _ramore(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    config_file = str(config.get().root / config.CONFIG_NAME)
    monkeypatch.setattr(sys, "argv", ["ramore", *args, "--config-file", config_file])
    main()


def _append(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write("".join(line + "\n" for line in lines))


def _published(remote: Path) -> tuple[int, set[str]]:
    """The pages branch's commit count and files."""
    ref = config.DEFAULT_BRANCH
    n = int(_git("--git-dir", remote, "rev-list", "--count", ref))
    return n, set(
        _git("--git-dir", remote, "ls-tree", "-r", "--name-only", ref).split("\n")
    )


def _runs() -> list[dict]:
    logs = sorted(config.get().run_logs_dir.glob("run-*.log"))
    return [json.loads(line) for f in logs for line in f.read_text().splitlines()]


def test_report_end_to_end(
    app: Path, fake_box: FakeBox, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mails: list[list[alert.Event]] = []
    monkeypatch.setattr(alert, "send", mails.append)
    end = dt.datetime.now(dt.timezone.utc)
    lines = demo.traffic(end - dt.timedelta(days=2), end, visitors=15)
    first, before_rotation, after_rotation = (
        lines[: len(lines) // 2],
        lines[len(lines) // 2 : -50],
        lines[-50:],
    )
    log = fake_box.path(box.REQUEST_LOG)
    _append(log, first)
    _append(
        fake_box.path(box.METRICS_LOG), demo.samples(end - dt.timedelta(hours=1), end)
    )

    _ramore(monkeypatch, "report", "--mode", "both")
    cfg = config.get()
    assert box.is_current(cfg)
    n_commits, files = _published(app)
    assert (
        n_commits == 2 and {"CNAME", "index.html", "ramore.json", ".nojekyll"} <= files
    )
    assert not publish.publish(), "an unchanged site publishes nothing"

    _append(log, before_rotation)
    log.rename(fake_box.path(box.REQUEST_LOG + ".1"))
    _append(log, after_rotation)
    _ramore(monkeypatch, "report", "--mode", "both")
    live, _ = parse.keep_live_hosts(parse.parse_lines(lines)[0])
    assert len(archive.read_hot()) == len(live), (
        "every line archived once, across the rotation"
    )
    incremental = {level: aggregate.load(level) for level in aggregate.LEVELS}
    aggregate.rebuild()
    for level, df in incremental.items():
        keys = aggregate.GROUP_KEYS
        assert_frame_equal(
            df.sort(keys), aggregate.load(level).sort(keys), check_exact=False
        )
    assert _published(app)[0] == 3

    _ramore(monkeypatch, "report", "--reclassify-all", "--no-publish")
    assert len(archive.read_hot()) == len(live)
    _ramore(monkeypatch, "report", "--dry-run")
    runs = _runs()
    assert [r["status"] for r in runs] == ["ok"] * 4
    assert runs[1]["rotated"] and runs[-1]["lines_fetched"] == 0
    assert mails == [], "the demo box stays under every alert threshold"

    unit_dir = tmp_path / "units"
    monkeypatch.setattr(units, "UNIT_DIR", unit_dir)
    monkeypatch.setattr(units.shutil, "which", lambda name: f"/usr/bin/{name}")
    _ramore(monkeypatch, "units", "--report", "--monitor")
    names = units.unit_names(cfg)
    assert {p.name for p in unit_dir.iterdir()} == set(
        units.render(cfg, Path(), "", True, True)
    )
    assert (
        f"enable --now {names['report']}.timer {names['monitor']}.service"
        in fake_box.calls.read_text()
    )
    _ramore(monkeypatch, "units", "--report", "--monitor", "--remove")
    assert not any(unit_dir.iterdir())

    _ramore(monkeypatch, "reset", "--yes")
    assert archive.read_hot().is_empty()
    assert _published(app) == (1, {"CNAME"})
    assert (
        _git("--git-dir", app, "show", f"{config.DEFAULT_BRANCH}:CNAME")
        == CNAME.strip()
    )


def test_failed_run_raises_and_logs_once(
    fake_box: FakeBox, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("NGINX_T_EXIT", "1")
    with pytest.raises(subprocess.CalledProcessError):
        _ramore(monkeypatch, "report")
    (run,) = _runs()
    assert run["status"] == "error" and "CalledProcessError" in run["error"]
    assert "duration_s" in run
