import dataclasses
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from ramore import box, config
from ramore.report.parse import parse_lines

METRICS = config.Metrics(60, ("/",), ("app-*.service",))
BOX_ROOTS = ("/etc/", "/usr/local/", "/var/log/")


def _nginx_json(value: str) -> str:
    """A value as nginx's `escape=json` writes it."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def test_log_format_parses():
    values = {
        "$msec": "1777382055.123",
        "$remote_addr": "203.0.113.42",
        "$request_method": "GET",
        "$request_uri": "/v1/names/authors?q=därwin",
        "$status": "200",
        "$body_bytes_sent": "4321",
        "$http_referer": "",
        "$http_user_agent": 'Mozilla/5.0 "quoted" back\\slash',
        "$request_time": "0.045",
        "$upstream_connect_time": "0.001",
        "$upstream_header_time": "0.040",
        "$upstream_response_time": "0.040",
        "$upstream_cache_status": "MISS",
        "$host": "www.example.org",
    }
    template = re.search(r"'(.*)'", box.log_format()).group(1)
    line = re.sub(r"\$[a-z_]+", lambda m: _nginx_json(values[m.group(0)]), template)
    df, fail = parse_lines([line])
    assert fail == 0
    r = df.row(0, named=True)
    assert r["ua"] == values["$http_user_agent"]
    assert r["path"] == values["$request_uri"]
    assert r["status"] == 200 and r["size"] == 4321


def test_files_follow_metrics(cfg: config.Config):
    assert set(box.files(cfg)) == {box.NGINX_CONF}
    with_metrics = box.files(dataclasses.replace(cfg, metrics=METRICS))
    assert (
        with_metrics[box.SAMPLER]
        == Path(box.__file__).with_name("sample.py").read_text()
    )
    assert json.loads(with_metrics[box.SAMPLER_CONF])["units"] == list(METRICS.units)
    assert (
        f"OnUnitActiveSec={METRICS.interval_s}s"
        in with_metrics[f"{box.UNIT_DIR}/{box.SAMPLER_UNIT}.timer"]
    )


def test_stamp_tracks_config(cfg: config.Config):
    a = box.stamp(box.files(dataclasses.replace(cfg, metrics=METRICS)))
    b = box.stamp(
        box.files(
            dataclasses.replace(
                cfg, metrics=dataclasses.replace(METRICS, interval_s=30)
            )
        )
    )
    assert a == box.stamp(box.files(dataclasses.replace(cfg, metrics=METRICS)))
    assert a != b != box.stamp(box.files(cfg))


@pytest.fixture
def fake_box(tmp_path: Path) -> tuple[Path, Path, dict]:
    """A root to install into, and `nginx`/`systemctl` stubs logging their calls."""
    root, bin_dir, calls = tmp_path / "box", tmp_path / "bin", tmp_path / "calls"
    bin_dir.mkdir()
    for name in ("nginx", "systemctl"):
        stub = bin_dir / name
        stub.write_text(
            f'#!/bin/sh\necho "{name} $*" >> {calls}\n'
            f'[ "{name} $*" != "nginx -t" ] || exit "${{NGINX_T_EXIT:-0}}"\n'
        )
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return root, calls, env


def _run(script: str, root: Path, env: dict) -> subprocess.CompletedProcess:
    for prefix in BOX_ROOTS:
        script = script.replace(prefix, f"{root}{prefix}")
    return subprocess.run(
        ["bash", "-s"], input=script.encode(), env=env, capture_output=True
    )


def _on_box(root: Path, path: str) -> Path:
    return root / path.lstrip("/")


def test_install_script_writes_the_box(cfg: config.Config, fake_box):
    root, calls, env = fake_box
    files = box.files(dataclasses.replace(cfg, metrics=METRICS))
    assert _run(box.install_script(files), root, env).returncode == 0
    for path, content in files.items():
        assert _on_box(root, path).read_text() == content
    assert _on_box(root, box.STAMP).read_text().strip() == box.stamp(files)
    log = calls.read_text()
    assert "systemctl reload nginx" in log
    assert f"systemctl enable {box.SAMPLER_UNIT}.timer" in log


def test_install_script_restores_nginx_conf_on_failed_test(
    cfg: config.Config, fake_box
):
    root, _, env = fake_box
    conf = _on_box(root, box.NGINX_CONF)
    conf.parent.mkdir(parents=True)
    conf.write_text("previous\n")
    run = _run(box.install_script(box.files(cfg)), root, {**env, "NGINX_T_EXIT": "1"})
    assert run.returncode != 0
    assert conf.read_text() == "previous\n"
    assert not _on_box(root, box.STAMP).exists()


def test_install_script_removes_the_sampler_without_metrics(
    cfg: config.Config, fake_box
):
    root, calls, env = fake_box
    _run(
        box.install_script(box.files(dataclasses.replace(cfg, metrics=METRICS))),
        root,
        env,
    )
    assert _run(box.install_script(box.files(cfg)), root, env).returncode == 0
    assert not _on_box(root, box.SAMPLER).exists()
    assert f"systemctl disable --now {box.SAMPLER_UNIT}.timer" in calls.read_text()
    assert _on_box(root, box.STAMP).read_text().strip() == box.stamp(box.files(cfg))
