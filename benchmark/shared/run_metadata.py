import json
import os
import socket
import subprocess
from datetime import datetime, timezone

SCHEMA_VERSION = 1


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return ""


def hostname() -> str:
    return socket.gethostname()


def write_run_json(
    path: str, *, execution_ts: str, started_at: datetime,
    config: dict, sections: dict,
) -> None:
    """Write run.json: common provenance + benchmark-specific `sections`.

    `started_at` is the run's start time; finish time and duration are taken
    at call time. `sections` is merged into the top level (e.g. modes,
    capacities, experiments)."""
    finished = datetime.now(timezone.utc)
    started = started_at.astimezone(timezone.utc)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "execution_ts": execution_ts,
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at": finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": round((finished - started).total_seconds(), 1),
        "git_commit": git_commit(),
        "hostname": hostname(),
        "config": config,
        **sections,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
