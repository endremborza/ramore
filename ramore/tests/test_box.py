import dataclasses
import json
import re
import subprocess
from pathlib import Path

import pytest

from ramore import box, config, ssh
from ramore.report.parse import parse_lines

from .conftest import FakeBox


def _nginx_json(value: str) -> str:
    """A value as nginx's `escape=json` writes it."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _install(cfg: config.Config) -> None:
    ssh.run(
        cfg.ssh_host, "sudo -n bash -s", box.install_script(box.files(cfg)).encode()
    )


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
    assert cfg.metrics is not None
    with_metrics = box.files(cfg)
    assert (
        with_metrics[box.SAMPLER]
        == Path(box.__file__).with_name("sample.py").read_text()
    )
    assert json.loads(with_metrics[box.SAMPLER_CONF])["units"] == list(
        cfg.metrics.units
    )
    timer = with_metrics[f"{box.UNIT_DIR}/{box.SAMPLER_UNIT}.timer"]
    assert f"OnUnitActiveSec={cfg.metrics.interval_s}s" in timer
    assert set(box.files(dataclasses.replace(cfg, metrics=None))) == {box.NGINX_CONF}


def test_stamp_tracks_config(cfg: config.Config):
    assert cfg.metrics is not None
    slower = dataclasses.replace(cfg.metrics, interval_s=cfg.metrics.interval_s * 2)
    a = box.stamp(box.files(cfg))
    b = box.stamp(box.files(dataclasses.replace(cfg, metrics=slower)))
    assert a == box.stamp(box.files(cfg))
    assert a != b != box.stamp(box.files(dataclasses.replace(cfg, metrics=None)))


def test_ensure_installs_once(cfg: config.Config, fake_box: FakeBox):
    assert not box.is_current(cfg)
    assert box.ensure(cfg)
    for path, content in box.files(cfg).items():
        assert fake_box.path(path).read_text() == content
    assert box.is_current(cfg) and not box.ensure(cfg)
    calls = fake_box.calls.read_text()
    assert "systemctl reload nginx" in calls
    assert f"systemctl enable {box.SAMPLER_UNIT}.timer" in calls


def test_install_restores_nginx_conf_on_failed_test(
    cfg: config.Config, fake_box: FakeBox, monkeypatch: pytest.MonkeyPatch
):
    conf = fake_box.path(box.NGINX_CONF)
    conf.parent.mkdir(parents=True)
    conf.write_text("previous\n")
    monkeypatch.setenv("NGINX_T_EXIT", "1")
    with pytest.raises(subprocess.CalledProcessError):
        _install(cfg)
    assert conf.read_text() == "previous\n"
    assert not fake_box.path(box.STAMP).exists()


def test_install_removes_the_sampler_without_metrics(
    cfg: config.Config, fake_box: FakeBox
):
    _install(cfg)
    bare = dataclasses.replace(cfg, metrics=None)
    _install(bare)
    assert not fake_box.path(box.SAMPLER).exists()
    assert (
        f"systemctl disable --now {box.SAMPLER_UNIT}.timer"
        in fake_box.calls.read_text()
    )
    assert box.is_current(bare)
