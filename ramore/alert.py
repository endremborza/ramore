"""Incidents and their mail.

A caller evaluates its whole set of checks at once and steps its own incident table with them: a
check failing for `sustain_s` fires once, passing again (or no longer being evaluated) resolves
it, and every step's events go out as one mail. The table persists per caller under the store's
`incidents/`, so a restart neither re-fires nor forgets an open incident. A table is saved only
after its mail went out, so a failed send fires again on the next step.
"""

import json
import os
import smtplib
import socket
from dataclasses import asdict, dataclass, replace
from email.message import EmailMessage
from typing import Iterable, Literal

from . import config

SMTP_SSL_PORT = 465
SMTP_ENV = ("RAMORE_SMTP_HOST", "RAMORE_SMTP_USER", "RAMORE_SMTP_PASSWORD")
MAIL_TO_ENV = "RAMORE_MAIL_TO"


@dataclass(frozen=True)
class Check:
    key: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Incident:
    since: float
    fired: bool
    detail: str


@dataclass(frozen=True)
class Event:
    key: str
    kind: Literal["fired", "resolved"]
    detail: str
    since: float


Table = dict[str, Incident]


def step(
    table: Table, checks: Iterable[Check], now: float, sustain_s: float
) -> tuple[Table, list[Event]]:
    new: Table = {}
    events: list[Event] = []
    for c in checks:
        inc = table.get(c.key)
        if c.ok:
            if inc is not None and inc.fired:
                events.append(Event(c.key, "resolved", c.detail, inc.since))
            continue
        inc = replace(inc, detail=c.detail) if inc else Incident(now, False, c.detail)
        if not inc.fired and now - inc.since >= sustain_s:
            inc = replace(inc, fired=True)
            events.append(Event(c.key, "fired", c.detail, inc.since))
        new[c.key] = inc
    for key, inc in table.items():
        if key not in new and inc.fired and all(e.key != key for e in events):
            events.append(Event(key, "resolved", "no longer checked", inc.since))
    return new, events


def run_step(
    caller: str, checks: Iterable[Check], now: float, sustain_s: float
) -> list[Event]:
    """Step `caller`'s persisted table and mail its events; the events."""
    path = config.get().incidents_dir / f"{caller}.json"
    table = (
        {k: Incident(**v) for k, v in json.loads(path.read_text()).items()}
        if path.exists()
        else {}
    )
    new, events = step(table, checks, now, sustain_s)
    if events:
        send(events)
    if new != table:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: asdict(v) for k, v in new.items()}, indent=1))
        os.replace(tmp, path)
    return events


def mail_env() -> dict[str, str]:
    """The SMTP settings from the environment; a missing one raises."""
    missing = [k for k in SMTP_ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"mail needs {', '.join(missing)} in the environment")
    return {k: os.environ[k] for k in SMTP_ENV}


def send(events: list[Event]) -> None:
    env = mail_env()
    host, user, password = (env[k] for k in SMTP_ENV)
    fired = [e for e in events if e.kind == "fired"]
    msg = EmailMessage()
    msg["Subject"] = (
        f"{config.get().name}: {len(fired)} fired, {len(events) - len(fired)} resolved"
        f" ({events[0].key})"
    )
    msg["From"] = user
    msg["To"] = os.environ.get(MAIL_TO_ENV) or user
    msg.set_content(
        f"from {socket.gethostname()}\n\n"
        + "\n".join(f"{e.kind} {e.key}: {e.detail}" for e in events)
    )
    with smtplib.SMTP_SSL(host, SMTP_SSL_PORT, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
