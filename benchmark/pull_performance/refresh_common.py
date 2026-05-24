import subprocess
import time
import uuid

from shared import log
from shared.registry import stargz_base_image, zstd_base_image

EXPERIMENTS = [
    # ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5GB     ~50 MB
    ("Qwen/Qwen2-1.5B", "docker.io/library/python:3.12-slim"),                      # ~3.09 GB     ~3.4 GB
    ("openlm-research/open_llama_3b", "docker.io/library/python:3.12-slim"),    # ~6.85 GB     ~3.4 GB
]


# ── build helpers ──────────────────────────────────────────────────────


def build_mode(mode: str) -> str:
    """Strip baseline- prefix and -with-bg-fetch suffix; build behavior is identical to the base mode."""
    if mode.startswith("baseline-"):
        mode = mode[len("baseline-"):]
    if mode.endswith("-with-bg-fetch"):
        mode = mode[: -len("-with-bg-fetch")]
    return mode


def extra_flags(mode: str) -> list[str]:
    base = build_mode(mode)
    if base == "2dfs-stargz":
        return ["--enable-stargz", "--stargz-chunk-size", "2097152"]
    if base == "2dfs-stargz-zstd":
        return ["--enable-stargz", "--use-zstd", "--stargz-chunk-size", "8388608"]
    raise ValueError(f"Unknown mode: {mode}")


def base_image(source_image: str, cfg, mode: str) -> str:
    base = build_mode(mode)
    if base == "2dfs-stargz":
        return stargz_base_image(source_image, cfg)
    if base == "2dfs-stargz-zstd":
        return zstd_base_image(source_image, cfg)
    raise ValueError(f"Unknown mode: {mode}")


# ── container helpers ──────────────────────────────────────────────────


def timed_pull(cmd: list[str]) -> float:
    start = time.perf_counter()
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Pull failed (exit {result.returncode}):\n{result.stderr}")
    return time.perf_counter() - start


def start_container(image: str, name: str) -> None:
    """Start a detached stargz container that stays alive via sleep infinity."""
    subprocess.run(
        ["sudo", "ctr-remote", "run", "--detach", "--snapshotter=stargz",
         image, name, "sleep", "infinity"],
        check=True, capture_output=not log.VERBOSE,
    )

def cat_chunks_in_container(name: str, n: int) -> None:
    """Cat /chunk1.bin../chunkN.bin in container, discarding output."""
    files = " ".join(f"/chunk{i + 1}.bin" for i in range(n))
    exec_id = uuid.uuid4().hex[:8]
    subprocess.run(
        ["sudo", "ctr", "tasks", "exec", "--exec-id", exec_id,
         name, "sh", "-c", f"cat {files} > /dev/null"],
        check=True, capture_output=not log.VERBOSE,
    )


def kill_container(name: str) -> None:
    """Send SIGKILL to the container."""
    t0 = time.perf_counter()
    subprocess.run(["sudo", "nerdctl", "kill", name], check=True,
                   capture_output=not log.VERBOSE)
    log.info(f"  stop[kill]={time.perf_counter() - t0:.2f}s")


def delete_container(name: str) -> None:
    """Delete the task and container record. The task delete is slow (FUSE
    rootfs unmount)."""
    t0 = time.perf_counter()
    subprocess.run(["sudo", "ctr", "tasks", "delete", name], check=True,
                   capture_output=not log.VERBOSE)
    task_dt = time.perf_counter() - t0

    t0 = time.perf_counter()
    subprocess.run(["sudo", "ctr", "containers", "delete", name], check=True,
                   capture_output=not log.VERBOSE)
    log.info(f"  cleanup[task-delete]={task_dt:.2f}s "
             f"container-delete={time.perf_counter() - t0:.2f}s")


def stop_container(name: str) -> None:
    """Kill and delete. For cleanup where timing doesn't matter."""
    kill_container(name)
    delete_container(name)
