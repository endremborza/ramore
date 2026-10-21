import json

import pytest

from ramore import alert, config
from ramore.alert import Check, step
from ramore.report import metrics

SUSTAIN = 120


def _kinds(events: list[alert.Event]) -> list[tuple[str, str]]:
    return [(e.kind, e.key) for e in events]


def test_fires_once_after_sustain_and_resolves():
    t, ev = step({}, [Check("home", False, "502")], 0, SUSTAIN)
    assert ev == [] and not t["home"].fired
    t, ev = step(t, [Check("home", False, "502")], SUSTAIN - 1, SUSTAIN)
    assert ev == []
    t, ev = step(t, [Check("home", False, "503")], SUSTAIN, SUSTAIN)
    assert _kinds(ev) == [("fired", "home")] and ev[0].detail == "503"
    t, ev = step(t, [Check("home", False, "503")], SUSTAIN + 15, SUSTAIN)
    assert ev == []
    t, ev = step(t, [Check("home", True, "200")], SUSTAIN + 30, SUSTAIN)
    assert _kinds(ev) == [("resolved", "home")] and t == {}


def test_a_blip_never_fires():
    t, _ = step({}, [Check("home", False, "502")], 0, SUSTAIN)
    t, ev = step(t, [Check("home", True, "200")], 15, SUSTAIN)
    assert ev == [] and t == {}


def test_unchecked_key_resolves():
    t, _ = step({}, [Check("anon app@1.service", False, "95%")], 0, 0)
    t, ev = step(t, [Check("memory", True, "40%")], 1, 0)
    assert _kinds(ev) == [("resolved", "anon app@1.service")] and t == {}


def test_failed_mail_fires_again(monkeypatch):
    sent = []

    def fail(events):
        raise OSError("smtp down")

    monkeypatch.setattr(alert, "send", fail)
    with pytest.raises(OSError):
        alert.run_step("report", [Check("memory", False, "99%")], 0, 0)
    monkeypatch.setattr(alert, "send", sent.append)
    alert.run_step("report", [Check("memory", False, "99%")], 1, 0)
    assert _kinds(sent[0]) == [("fired", "memory")]
    assert alert.run_step("report", [Check("memory", False, "99%")], 2, 0) == []


def test_box_checks_on_the_latest_sample():
    a = config.Alerts()
    gb = metrics.GB
    rows = [
        {"t": 1.0, "scope": "host", "name": "", "used": 99 * gb, "limit": 100 * gb},
        {"t": 2.0, "scope": "host", "name": "", "used": 10 * gb, "limit": 100 * gb},
        {"t": 2.0, "scope": "mount", "name": "/", "used": 95, "limit": 100},
        {
            "t": 2.0,
            "scope": "unit",
            "name": "be.service",
            "used": 9 * gb,
            "limit": 10 * gb,
            "anon": 2 * gb,
            "fds": 900,
            "fd_limit": 1000,
        },
        {
            "t": 2.0,
            "scope": "unit",
            "name": "nginx.service",
            "used": gb,
            "limit": None,
            "anon": gb,
            "fds": 10,
            "fd_limit": None,
        },
    ]
    df, _ = metrics.parse_lines(json.dumps(r) for r in rows)
    got = {c.key: c.ok for c in metrics.checks(df, a)}
    assert got == {
        "memory": True,
        "disk /": False,
        "anon be.service": True,
        "fds be.service": False,
    }


def test_send_mails_one_message_per_step(monkeypatch):
    sent = []

    class SMTP:
        def __init__(self, host, port, timeout):
            assert (host, port) == ("smtp.example.org", alert.SMTP_SSL_PORT)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            assert (user, password) == ("me@example.org", "secret")

        def send_message(self, msg):
            sent.append(msg)

    env = dict(zip(alert.SMTP_ENV, ("smtp.example.org", "me@example.org", "secret")))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(alert.MAIL_TO_ENV, raising=False)
    monkeypatch.setattr(alert.smtplib, "SMTP_SSL", SMTP)
    alert.send(
        [
            alert.Event("memory", "fired", "99%", 0),
            alert.Event("disk /", "resolved", "50%", 0),
        ]
    )
    (msg,) = sent
    assert msg["Subject"] == f"{config.get().name}: 1 fired, 1 resolved (memory)"
    assert msg["To"] == "me@example.org"
    assert "fired memory: 99%\nresolved disk /: 50%" in msg.get_content()


def test_mail_env_names_what_is_missing(monkeypatch):
    for k in alert.SMTP_ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match=alert.SMTP_ENV[0]):
        alert.mail_env()
