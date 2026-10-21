"""`ramore box`: ramore's side of the app's box, rendered from the app's config.

nginx writes the request log in ramore's JSON format (`REQUEST_FIELDS`) from an http-level
`access_log`, which every server block without an `access_log` of its own inherits. With a
`[metrics]` section, a root timer runs `sample.py` every `interval_s` into `METRICS_LOG`. The
install is one sudo script over ssh; `STAMP` holds the hash of what it wrote, so a report run
reinstalls exactly when the config or ramore changed what the box should hold.
"""

import base64
import hashlib
import json
from pathlib import Path

from .. import config, ssh

REQUEST_LOG = "/var/log/nginx/ramore.log"
METRICS_LOG = "/var/log/ramore/metrics.log"
STAMP = "/etc/ramore/stamp"
NGINX_CONF = "/etc/nginx/conf.d/ramore.conf"
NGINX_CONF_PREV = "/etc/ramore/nginx.conf.prev"
SAMPLER = "/usr/local/lib/ramore/sample.py"
SAMPLER_CONF = "/etc/ramore/sample.json"
SAMPLER_UNIT = "ramore-sample"
UNIT_DIR = "/etc/systemd/system"
LOGROTATE = "/etc/logrotate.d/ramore"

# Each request-log key and the nginx variable it carries. Every value is a JSON string (`$status`
# can be `000`, no JSON number); the parser types them.
REQUEST_FIELDS = {
    "t": "$msec",
    "addr": "$remote_addr",
    "method": "$request_method",
    "path": "$request_uri",
    "status": "$status",
    "size": "$body_bytes_sent",
    "referrer": "$http_referer",
    "ua": "$http_user_agent",
    "rt": "$request_time",
    "uct": "$upstream_connect_time",
    "uht": "$upstream_header_time",
    "urt": "$upstream_response_time",
    "cs": "$upstream_cache_status",
    "host": "$host",
}

_SAMPLER_SRC = Path(__file__).with_name("sample.py")

_SERVICE = f"""[Unit]
Description=ramore box sample

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I -S {SAMPLER} {SAMPLER_CONF}
Nice=19
IOSchedulingClass=idle
"""

_LOGROTATE = """/var/log/ramore/*.log {
	daily
	rotate 14
	compress
	delaycompress
	missingok
	notifempty
	create 0644 root root
}
"""


def log_format() -> str:
    body = ",".join(f'"{k}":"{v}"' for k, v in REQUEST_FIELDS.items())
    return f"log_format ramore escape=json '{{{body}}}';"


def files(cfg: config.Config) -> dict[str, str]:
    """Every file the box should hold for `cfg`, by its path on the box."""
    out = {
        NGINX_CONF: f"{log_format()}\naccess_log {REQUEST_LOG} ramore;\n",
    }
    if cfg.metrics is not None:
        sampler_conf = {
            "log": METRICS_LOG,
            "mounts": list(cfg.metrics.mounts),
            "units": list(cfg.metrics.units),
        }
        out |= {
            SAMPLER: _SAMPLER_SRC.read_text(),
            SAMPLER_CONF: json.dumps(sampler_conf, indent=1, sort_keys=True) + "\n",
            f"{UNIT_DIR}/{SAMPLER_UNIT}.service": _SERVICE,
            f"{UNIT_DIR}/{SAMPLER_UNIT}.timer": _timer(cfg.metrics.interval_s),
            LOGROTATE: _LOGROTATE,
        }
    return out


def stamp(box_files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for path in sorted(box_files):
        h.update(f"{path}\0{box_files[path]}\0".encode())
    return h.hexdigest()


def install_script(box_files: dict[str, str]) -> str:
    """The sudo bash script that brings the box to `box_files`, stamped last so a failed
    install is retried by the next run."""
    lines = [
        "set -euo pipefail",
        'put() { install -d "$(dirname "$1")"; base64 -d > "$1.new"; '
        'chmod 0644 "$1.new"; mv "$1.new" "$1"; }',
        f"install -d {Path(STAMP).parent}",
        f"if [ -e {NGINX_CONF} ]; then install -m 0644 {NGINX_CONF} {NGINX_CONF_PREV}; fi",
        _put(NGINX_CONF, box_files[NGINX_CONF]),
        "if ! nginx -t; then",
        f"  if [ -e {NGINX_CONF_PREV} ]; then mv {NGINX_CONF_PREV} {NGINX_CONF}; "
        f"else rm -f {NGINX_CONF}; fi",
        "  exit 1",
        "fi",
        "systemctl reload nginx",
        f"rm -f {NGINX_CONF_PREV}",
    ]
    timer = f"{SAMPLER_UNIT}.timer"
    sampler_files = [p for p in box_files if p != NGINX_CONF]
    if sampler_files:
        lines += [
            f"install -d -m 0755 {Path(METRICS_LOG).parent}",
            *(_put(p, box_files[p]) for p in sampler_files),
            "systemctl daemon-reload",
            f"systemctl enable {timer}",
            f"systemctl restart {timer}",
            f"systemctl start {SAMPLER_UNIT}.service",
        ]
    else:
        lines += [
            f"systemctl disable --now {timer} 2>/dev/null || true",
            f"rm -f {SAMPLER} {SAMPLER_CONF} {UNIT_DIR}/{SAMPLER_UNIT}.service "
            f"{UNIT_DIR}/{timer} {LOGROTATE}",
            "systemctl daemon-reload",
        ]
    lines.append(f"echo {stamp(box_files)} > {STAMP}")
    return "\n".join(lines) + "\n"


def is_current(cfg: config.Config) -> bool:
    out = ssh.run(cfg.ssh_host, f"cat {STAMP} 2>/dev/null || true")
    return out.decode().strip() == stamp(files(cfg))


def ensure(cfg: config.Config) -> bool:
    """Install the box side unless the box already holds it; True when it installed."""
    if is_current(cfg):
        return False
    ssh.run(cfg.ssh_host, "sudo -n bash -s", install_script(files(cfg)).encode())
    return True


def main(*, check: bool = False, config_file: str = config.CONFIG_NAME) -> None:
    """Install or update ramore's side of the app's box; --check only says whether it is current."""
    cfg = config.use(config.load(config_file))
    if check:
        current = is_current(cfg)
        print("current" if current else "outdated")
        raise SystemExit(0 if current else 1)
    print("installed" if ensure(cfg) else "already current")


def _timer(interval_s: int) -> str:
    return f"""[Unit]
Description=ramore box sample every {interval_s} s

[Timer]
OnActiveSec=0
OnUnitActiveSec={interval_s}s
AccuracySec=1s

[Install]
WantedBy=timers.target
"""


def _put(path: str, content: str) -> str:
    b64 = base64.encodebytes(content.encode()).decode()
    return f"put {path} <<'RAMORE_B64'\n{b64}RAMORE_B64"
