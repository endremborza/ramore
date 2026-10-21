import subprocess


def run(host: str, cmd: str, stdin: bytes | None = None) -> bytes:
    """`cmd` on `host` through ssh; its stdout. A non-zero exit raises."""
    return subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", host, cmd],
        input=stdin,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout
