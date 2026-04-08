import subprocess

from shared import log


def ensure_buildkit() -> None:
    result = subprocess.run(
        ["sudo", "systemctl", "is-active", "--quiet", "buildkit"],
        capture_output=True,
    )
    if result.returncode != 0:
        log.info("buildkit is not running, starting it...")
        subprocess.run(["sudo", "systemctl", "start", "buildkit"], check=True)
        log.info("buildkit started.")
