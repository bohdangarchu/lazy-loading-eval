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


def _task_pid(name: str) -> str | None:
    """Host PID of the container's init process, via `ctr tasks ls`."""
    r = subprocess.run(["sudo", "ctr", "tasks", "ls"],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == name:
            return parts[1]
    return None


def stop_container(name: str) -> None:
    # TEMP investigation: per-call timing + sample the init process's kernel
    # state during the ~6s task-delete stall to confirm it wedges in D state.
    t0 = time.perf_counter()
    subprocess.run(["sudo", "nerdctl", "kill", name], check=True,
                   capture_output=not log.VERBOSE)
    log.info(f"  stop[kill]={time.perf_counter() - t0:.2f}s")

    pid = _task_pid(name)
    sampler = None
    if pid:
        # Sample STAT + wchan every 0.2s for up to 12s, in the background.
        sampler = subprocess.Popen(
            ["sudo", "sh", "-c",
             f"for i in $(seq 1 60); do "
             f"printf '%s ' \"$(date +%T.%2N)\"; "
             f"ps -o stat=,wchan:32= -p {pid} 2>/dev/null || echo gone; "
             f"sleep 0.2; done"],
            stdout=subprocess.PIPE, text=True,
        )

    t0 = time.perf_counter()
    subprocess.run(["sudo", "ctr", "tasks", "delete", name], check=True,
                   capture_output=not log.VERBOSE)
    log.info(f"  stop[task-delete]={time.perf_counter() - t0:.2f}s (pid={pid})")

    if sampler:
        sampler.terminate()
        out, _ = sampler.communicate(timeout=5)
        last = None  # print only on state change, with the timestamp it began
        for line in out.splitlines():
            ts, _, state = line.partition(" ")
            if state != last:
                log.info(f"    sample {ts} {state}")
                last = state

    t0 = time.perf_counter()
    subprocess.run(["sudo", "ctr", "containers", "delete", name], check=True,
                   capture_output=not log.VERBOSE)
    log.info(f"  stop[container-delete]={time.perf_counter() - t0:.2f}s")
