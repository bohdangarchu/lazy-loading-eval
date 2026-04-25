import json
import os
import re
import subprocess
import time

from shared import log

_JOURNAL_KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def parse_journal_kv(text: str) -> dict[str, str]:
    """Parse a logrus-formatted log line into a key→value dict."""
    result = {}
    for m in _JOURNAL_KV_RE.finditer(text):
        result[m.group(1)] = m.group(2).strip('"')
    return result


def collect_stargz_journal_since(since_s: float) -> list[dict]:
    """Return all stargz-snapshotter journal entries since a Unix timestamp."""
    result = subprocess.run(
        ["sudo", "journalctl", "-u", "stargz-snapshotter",
         f"--since=@{since_s:.0f}", "--output", "json", "--no-pager"],
        capture_output=True, text=True, check=True,
    )
    entries = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


def save_stargz_run_log(pull_start_s: float, run_end_s: float, log_path: str) -> None:
    entries = collect_stargz_journal_since(pull_start_s)
    run_end_us = run_end_s * 1_000_000
    windowed = [e for e in entries if int(e.get("__REALTIME_TIMESTAMP", 0)) <= run_end_us]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as f:
        json.dump(windowed, f, indent=2)
    log.info(f"  Saved {len(windowed)} log entries → {os.path.basename(log_path)}")


def clear_2dfs_cache(cfg) -> None:
    log.info("Clearing 2dfs cache...")
    home = cfg.tdfs_home_dir or os.path.expanduser("~/.2dfs")
    cmd = f"sudo rm -rf {home}/blobs/* {home}/uncompressed-keys/* {home}/index/*"
    subprocess.run(cmd, shell=True, check=True)


STARGZ_ROOT = "/var/lib/containerd-stargz-grpc"


def clear_stargz_cache() -> None:
    """Full stargz wipe: unmount FUSE, rm -rf stargz root, restart containerd."""
    log.info("Clearing stargz cache...")
    subprocess.run("sudo systemctl stop stargz-snapshotter", shell=True, check=True)
    subprocess.run(
        f"grep '{STARGZ_ROOT}/snapshotter/snapshots' /proc/mounts"
        f" | awk '{{print $2}}' | xargs -r sudo umount -l",
        shell=True, check=True,
    )
    subprocess.run(f"sudo bash -c 'rm -rf {STARGZ_ROOT}/*'", shell=True, check=True)
    subprocess.run("sudo nerdctl image rm -f $(sudo nerdctl images -q) 2>/dev/null || true", shell=True)
    subprocess.run("sudo ctr content rm $(sudo ctr content ls -q) 2>/dev/null || true", shell=True)
    subprocess.run(
        "sudo rm -f /var/lib/containerd/io.containerd.metadata.v1.bolt/meta.db",
        shell=True, check=True,
    )
    subprocess.run("sudo systemctl start stargz-snapshotter", shell=True, check=True)
    subprocess.run("sudo systemctl restart containerd", shell=True, check=True)


def prune_buildkit() -> None:
    ensure_buildkit()
    log.info("Pruning buildkit cache...")
    subprocess.run(["sudo", "buildctl", "prune", "--all"], check=True, capture_output=not log.VERBOSE)


def ensure_buildkit() -> None:
    result = subprocess.run(
        ["sudo", "systemctl", "is-active", "--quiet", "buildkit"],
        capture_output=True,
    )
    if result.returncode != 0:
        log.info("buildkit is not running, starting it...")
        subprocess.run(["sudo", "systemctl", "start", "buildkit"], check=True)
        # Wait for the socket to become available (up to 10s)
        sock = "/run/buildkit/buildkitd.sock"
        for _ in range(20):
            if os.path.exists(sock):
                break
            time.sleep(0.5)
        log.info("buildkit started.")
