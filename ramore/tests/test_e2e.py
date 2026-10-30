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
from ramore.report import aggregate, archive, parse, publish, pull, state
from ramore.report import run as report_run

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


def _n_live(lines: list[str]) -> int:
    return len(parse.keep_live_hosts(parse.parse_lines(lines)[0])[0])


def _assert_aggregates_match_rebuild() -> None:
    incremental = {level: aggregate.load(level) for level in aggregate.LEVELS}
    aggregate.rebuild()
    keys = aggregate.GROUP_KEYS
    for level, df in incremental.items():
        assert_frame_equal(
            df.sort(keys), aggregate.load(level).sort(keys), check_exact=False
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
    n_live = _n_live(lines)
    assert len(archive.read_hot()) == n_live, (
        "every line archived once, across the rotation"
    )
    _assert_aggregates_match_rebuild()
    assert _published(app)[0] == 3

    _ramore(monkeypatch, "report", "--reclassify-all", "--no-publish")
    assert len(archive.read_hot()) == n_live
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


def test_a_failed_alert_mail_leaves_the_report_running(
    fake_box: FakeBox, monkeypatch: pytest.MonkeyPatch
):
    cfg_file = config.get().root / config.CONFIG_NAME
    cfg_file.write_text(cfg_file.read_text() + "\n[alerts]\nmemory_pct = 1\n")
    for k in alert.SMTP_ENV:
        monkeypatch.delenv(k, raising=False)
    end = dt.datetime.now(dt.timezone.utc)
    hour_ago = end - dt.timedelta(hours=1)
    _append(fake_box.path(box.REQUEST_LOG), demo.traffic(hour_ago, end, visitors=5))
    _append(fake_box.path(box.METRICS_LOG), demo.samples(hour_ago, end))
    _ramore(monkeypatch, "report", "--no-publish")
    (run,) = _runs()
    assert run["status"] == "ok" and alert.SMTP_ENV[0] in run["alert_error"]
    assert not archive.read_hot().is_empty()
    assert not (config.get().incidents_dir / "report.json").exists(), (
        "an unsent incident stays open, so the next run mails it"
    )


def test_a_backlog_is_pulled_in_chunks_and_an_interrupted_pull_resumes(
    fake_box: FakeBox, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pull, "PULL_CHUNK_BYTES", 2**16)
    end = dt.datetime.now(dt.timezone.utc)
    lines = demo.traffic(end - dt.timedelta(days=2), end, visitors=15)
    log = fake_box.path(box.REQUEST_LOG)
    _append(log, lines)
    ingest, chunks = report_run.ingest, 0

    def killed_at_the_third_chunk(*args):
        nonlocal chunks
        chunks += 1
        if chunks == 3:
            raise MemoryError("killed")
        return ingest(*args)

    monkeypatch.setattr(report_run, "ingest", killed_at_the_third_chunk)
    with pytest.raises(MemoryError):
        _ramore(monkeypatch, "report", "--no-publish")
    assert 0 < state.load().offsets[box.REQUEST_LOG].size < log.stat().st_size
    monkeypatch.setattr(report_run, "ingest", ingest)
    _ramore(monkeypatch, "report", "--no-publish")
    assert state.load().offsets[box.REQUEST_LOG].size == log.stat().st_size
    assert len(archive.read_hot()) == _n_live(lines), "no line lost across the chunks"
    _assert_aggregates_match_rebuild()
