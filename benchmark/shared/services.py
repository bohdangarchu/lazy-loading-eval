import os
import subprocess

from shared import log


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
        log.info("buildkit started.")
