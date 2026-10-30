"""`ramore units`: this host's systemd user units for the app.

`--report` installs `ramore-report-<app>.timer`, running `ramore report` every `REPORT_EVERY_H`
hours (a run slept through runs on wake) under `REPORT_MEMORY_MAX`; `--monitor` installs
`ramore-monitor-<app>.service`, restarted at least daily. Both run the `ramore` on PATH in the
config's directory, take the mail settings from the user manager's environment
(`~/.config/environment.d/`), and outlive a logout only with `loginctl enable-linger`.
"""

import re
import shutil
import subprocess
from pathlib import Path

from . import config

REPORT_EVERY_H = 4
REPORT_AT_MINUTE = 10
REPORT_MEMORY_MAX = "10G"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


def unit_names(cfg: config.Config) -> dict[str, str]:
    slug = re.sub(r"[^a-z0-9]+", "-", cfg.name.lower()).strip("-")
    return {role: f"ramore-{role}-{slug}" for role in ("report", "monitor")}


def render(
    cfg: config.Config, config_path: Path, exe: str, report: bool, monitor: bool
) -> dict[str, str]:
    """The unit files for the chosen roles, by file name."""
    names = unit_names(cfg)

    def service(cmd: str) -> str:
        return f"WorkingDirectory={cfg.root}\nExecStart={exe} {cmd} --config-file {config_path}"

    out = {}
    if report:
        out[f"{names['report']}.service"] = f"""[Unit]
Description=ramore report: {cfg.name}

[Service]
Type=oneshot
{service("report")}
MemoryMax={REPORT_MEMORY_MAX}
"""
        out[f"{names['report']}.timer"] = f"""[Unit]
Description=ramore report every {REPORT_EVERY_H} h: {cfg.name}

[Timer]
OnCalendar=*-*-* 00/{REPORT_EVERY_H}:{REPORT_AT_MINUTE:02d}:00
Persistent=true

[Install]
WantedBy=timers.target
"""
    if monitor:
        out[f"{names['monitor']}.service"] = f"""[Unit]
Description=ramore monitor: {cfg.name}

[Service]
{service("monitor")}
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=15
RuntimeMaxSec=1d

[Install]
WantedBy=default.target
"""
    return out


def main(
    *,
    report: bool = False,
    monitor: bool = False,
    remove: bool = False,
    config_file: str = config.CONFIG_NAME,
) -> None:
    """Install this host's --report timer and/or --monitor service, enabled and started;
    --remove stops and deletes them instead."""
    if not (report or monitor):
        raise SystemExit("name a role: --report and/or --monitor")
    config_path = Path(config_file).resolve()
    cfg = config.use(config.load(config_path))
    exe = shutil.which("ramore")
    if exe is None:
        raise SystemExit("no `ramore` on PATH")
    files = render(cfg, config_path, exe, report, monitor)
    names = unit_names(cfg)
    started = [f"{names['report']}.timer"] * report + [
        f"{names['monitor']}.service"
    ] * monitor
    if remove:
        _systemctl("disable", "--now", *started)
        for name in files:
            (UNIT_DIR / name).unlink(missing_ok=True)
        _systemctl("daemon-reload")
        return
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (UNIT_DIR / name).write_text(text)
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", *started)
    _systemctl("restart", *started)
    print("\n".join(f"{UNIT_DIR / n}" for n in files))


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True)
