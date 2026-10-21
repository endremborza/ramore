import datetime as dt
import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import config


@dataclass
class Offset:
    """How far a box log has been read: its inode and byte size at the last pull."""

    inode: int = 0
    size: int = 0


@dataclass
class State:
    offsets: dict[str, Offset] = field(default_factory=dict)
    last_event_ts: str = ""
    last_run: str = ""
    salt_date: str = ""
    parse_failures_last_run: int = 0
    lines_pulled_last_run: int = 0


def load() -> State:
    path = config.get().state_path
    if not path.exists():
        return State()
    raw = json.loads(path.read_text())
    fields = {k: raw[k] for k in State.__dataclass_fields__ if k in raw}
    fields["offsets"] = {p: Offset(**o) for p, o in fields.get("offsets", {}).items()}
    return State(**fields)


def save(s: State) -> None:
    _atomic_write_json(config.get().state_path, asdict(s))


def today_iso() -> str:
    return dt.date.today().isoformat()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load_salts() -> dict[str, str]:
    path = config.get().salts_path
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_salts(salts: dict[str, str]) -> None:
    _atomic_write_json(config.get().salts_path, salts)


def get_or_create_salt(date_iso: str) -> str:
    salts = load_salts()
    if date_iso not in salts:
        salts[date_iso] = secrets.token_hex(16)
        save_salts(salts)
    return salts[date_iso]


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True))
    os.replace(tmp, path)
