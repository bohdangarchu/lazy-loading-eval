import json
import os
import subprocess
import time
import urllib.request
import uuid
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from dotenv import load_dotenv
from huggingface_hub import snapshot_download

from shared import log, paths
from shared.artifacts import clear_artifacts, snapshot_artifacts, write_2dfs_json
from shared.charts import figure_footer, save_figure, write_csv
from shared.config import load_config
from shared.registry import (
    clear_registry, image_slug, prepare_local_registry, registry, tdfs_cmd,
)
from shared.services import (
    clear_2dfs_cache, clear_stargz_cache, ensure_buildkit, save_stargz_run_log,
)
from pull_performance.measure import _next_container_name
from pull_performance.paths import (
    refresh_artifacts_dir, refresh_chart_path,
    refresh_csv_path, refresh_log_path,
)
from pull_performance.refresh_common import (
    base_image, build_mode, extra_flags, start_container, stop_container, timed_pull,
)

load_dotenv()

CFG = load_config()
VERBOSE = True
N_RUNS = 1
MODE = "2dfs-stargz"
MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
SOURCE_IMAGE = "docker.io/library/python:3.12-slim"
MUTATED_FILENAME = "tokenizer_config.json"
MUTATION_STRING = b"added string"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ── snapshot download ──────────────────────────────────────────────────


def _model_snapshot_dir() -> str:
    return paths.models_dir(SCRIPT_DIR, MODEL)


def download_snapshot() -> list[str]:
    """Download full HF snapshot (weights + tokenizer/config JSONs).
    Returns absolute paths of every file in the snapshot dir.
    """
    local_dir = _model_snapshot_dir()
    os.makedirs(local_dir, exist_ok=True)

    has_cfg = os.path.exists(os.path.join(local_dir, MUTATED_FILENAME))
    has_weights = any(
        f.endswith(".safetensors") for f in os.listdir(local_dir)
    )
    if has_cfg and has_weights:
        log.info(f"Model snapshot present at {local_dir}, skipping download")
    else:
        log.info(f"Downloading full snapshot {MODEL} -> {local_dir}")
        token = os.environ.get("HF_TOKEN")
        snapshot_download(
            repo_id=MODEL,
            local_dir=local_dir,
            token=token,
            allow_patterns=["*.safetensors", "*.json", "*.txt", "*.model"],
        )

    files = sorted(
        os.path.join(local_dir, f)
        for f in os.listdir(local_dir)
        if os.path.isfile(os.path.join(local_dir, f))
    )
    return files


# ── mutation ───────────────────────────────────────────────────────────


def _mutate_chat_template() -> tuple[int, int]:
    """Insert MUTATION_STRING at the start of tokenizer_config.json's
    chat_template value. Returns (offset, length) so we can restore.
    """
    path = os.path.join(_model_snapshot_dir(), MUTATED_FILENAME)
    with open(path, "rb") as f:
        data = bytearray(f.read())

    marker = b'"chat_template":'
    i = data.find(marker)
    if i < 0:
        raise RuntimeError(f"chat_template not found in {path}")

    # Find opening quote of the chat_template string value.
    q = data.find(b'"', i + len(marker))
    if q < 0:
        raise RuntimeError(f"chat_template value quote not found in {path}")

    insert_at = q + 1
    data[insert_at:insert_at] = MUTATION_STRING
    with open(path, "wb") as f:
        f.write(data)
    log.info(
        f"Inserted {len(MUTATION_STRING)} bytes at offset {insert_at} in "
        f"{MUTATED_FILENAME}"
    )
    return insert_at, len(MUTATION_STRING)


def _restore_byte(offset: int, length: int) -> None:
    path = os.path.join(_model_snapshot_dir(), MUTATED_FILENAME)
    with open(path, "rb") as f:
        data = bytearray(f.read())
    del data[offset:offset + length]
    with open(path, "wb") as f:
        f.write(data)
    log.info(f"Removed {length} bytes at offset {offset} in {MUTATED_FILENAME}")


# ── image naming ───────────────────────────────────────────────────────


def _build_target(version: int) -> str:
    return f"{registry(CFG)}/{image_slug(SOURCE_IMAGE)}-{MODE}-refresh:v{version}"


def _pull_ref(version: int) -> str:
    return f"{registry(CFG)}/library/{image_slug(SOURCE_IMAGE)}-{MODE}-refresh:v{version}--0.0.0.0"


# ── TOC export ─────────────────────────────────────────────────────────


def _repo() -> str:
    return f"library/{image_slug(SOURCE_IMAGE)}-{MODE}-refresh"


def _full_tag(version: int) -> str:
    return f"v{version}--0.0.0.0"


def _fetch_layer_digests(version: int) -> list[str]:
    tag = _full_tag(version)
    base = f"http://{registry(CFG)}/v2/{_repo()}/manifests"
    accept = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ])
    req = urllib.request.Request(f"{base}/{tag}", headers={"Accept": accept})
    with urllib.request.urlopen(req) as resp:
        m = json.loads(resp.read())
    if "manifests" in m:
        entries = m["manifests"]
        chosen = None
        for entry in entries:
            plat = entry.get("platform", {})
            if plat.get("os") == "linux" and plat.get("architecture") == "amd64":
                chosen = entry
                break
        if chosen is None:
            log.info(f"no linux/amd64 in index ({len(entries)} entries); using first")
            chosen = entries[0]
        req2 = urllib.request.Request(f"{base}/{chosen['digest']}", headers={"Accept": accept})
        with urllib.request.urlopen(req2) as resp2:
            m = json.loads(resp2.read())
    return [layer["digest"] for layer in m["layers"]]


def _save_toc(digest: str, out_path: str) -> None:
    ref = f"{registry(CFG)}/{_repo()}@{digest}"
    log.info(f"fetch-toc {digest[:19]}... -> {os.path.basename(out_path)}")
    result = subprocess.run(
        ["sudo", "ctr-remote", "fetch-toc", ref],
        capture_output=True,
    )
    if result.returncode != 0:
        log.result(f"WARN: fetch-toc failed for {digest[:19]}... (rc={result.returncode}); skipping")
        if result.stderr:
            log.info(result.stderr.decode(errors="replace").strip())
        return
    with open(out_path, "wb") as f:
        f.write(result.stdout)


def _export_tocs(version: int, toc_dir: str) -> list[str]:
    os.makedirs(toc_dir, exist_ok=True)
    digests = _fetch_layer_digests(version)
    log.info(f"v{version} layers: {[d[:19] for d in digests]}")
    for i, d in enumerate(digests):
        _save_toc(d, os.path.join(toc_dir, f"toc_v{version}_layer{i}.json"))
    return digests


# ── build helpers ──────────────────────────────────────────────────────


def _build_version(snapshot_files: list[str], version: int) -> None:
    target = _build_target(version)
    base = base_image(SOURCE_IMAGE, CFG, MODE)

    write_2dfs_json([snapshot_files], SCRIPT_DIR)

    cmd = tdfs_cmd(CFG, SCRIPT_DIR) + [
        "build", "--platforms", "linux/amd64",
        *extra_flags(MODE),
        "--force-http", "-f", "2dfs.json",
        base, target,
    ]
    log.info(f"Building v{version}: {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built {target}")

    push_cmd = tdfs_cmd(CFG, SCRIPT_DIR) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")


# ── prepare ────────────────────────────────────────────────────────────


def prepare_refresh(
    artifacts_dir: str | None = None,
    toc_dir: str | None = None,
) -> list[str]:
    """Download snapshot, build & push v0, mutate one byte, build & push v1,
    restore. Returns snapshot file list.
    """
    snapshot_files = download_snapshot()

    clear_2dfs_cache(CFG)
    clear_registry(CFG, preserve_base=True, verbose=False)
    clear_artifacts(SCRIPT_DIR)

    _build_version(snapshot_files, 0)
    if artifacts_dir:
        snapshot_artifacts(SCRIPT_DIR, artifacts_dir)
    v0_digests: list[str] = []
    if toc_dir:
        v0_digests = _export_tocs(0, toc_dir)

    offset, original = _mutate_chat_template()
    try:
        _build_version(snapshot_files, 1)
    finally:
        _restore_byte(offset, original)

    if toc_dir:
        v1_digests = _export_tocs(1, toc_dir)
        changed = [
            i for i in range(min(len(v0_digests), len(v1_digests)))
            if v0_digests[i] != v1_digests[i]
        ]
        log.result(
            f"TOC: v0 vs v1 changed layer indices: {changed}"
            if changed else "TOC: no changed layers detected between v0 and v1"
        )

    return snapshot_files


# ── measurement helpers ────────────────────────────────────────────────


def _container_paths(snapshot_files: list[str]) -> list[str]:
    """In-container paths: files land at /{basename} per write_2dfs_json layout."""
    return [f"/{os.path.basename(p)}" for p in snapshot_files]


def _cat_all_in_container(name: str, in_paths: list[str]) -> float:
    """Exec cat over all snapshot files inside container, return elapsed seconds."""
    exec_id = uuid.uuid4().hex[:8]
    files = " ".join(in_paths)
    start = time.perf_counter()
    subprocess.run(
        ["sudo", "ctr", "tasks", "exec", "--exec-id", exec_id,
         name, "sh", "-c", f"cat {files} > /dev/null"],
        check=True, capture_output=not log.VERBOSE,
    )
    return time.perf_counter() - start


def _assert_mutated(name: str, expected: bool) -> None:
    """Verify whether MUTATION_STRING is present in MUTATED_FILENAME inside the
    container. Runs outside any timed window."""
    r = subprocess.run(
        ["sudo", "ctr", "tasks", "exec", "--exec-id", uuid.uuid4().hex[:8],
         name, "cat", f"/{MUTATED_FILENAME}"],
        capture_output=True, check=True,
    )
    found = MUTATION_STRING in r.stdout
    if found != expected:
        raise RuntimeError(
            f"validation failed in {name}: expected mutated={expected}, got {found}"
        )
    log.result(f"  validation OK ({name}): mutated={found}")


def _setup_warm_v0(in_paths: list[str]) -> str:
    """Clear stargz cache, pull v0, start container, warm page cache via cat.

    Diagnostic: cat twice back-to-back. If second cat is fast, the kernel page
    cache is being used; if both are ~same, FUSE is bypassing it (in which case
    refresh cannot help regardless of mount preservation).
    """
    clear_stargz_cache()
    v0 = _pull_ref(0)
    log.info(f"Pulling v0 (setup): {v0}")
    subprocess.run(
        ["sudo", "ctr-remote", "images", "rpull", "--plain-http", v0],
        check=True, capture_output=not log.VERBOSE,
    )
    name = _next_container_name(f"refresh-v0")
    start_container(v0, name)

    cold_t = _cat_all_in_container(name, in_paths)
    warm_t = _cat_all_in_container(name, in_paths)
    _assert_mutated(name, expected=False)
    log.result(
        f"DIAGNOSTICS: warm-up cat#1 (cold)={cold_t:.2f}s "
        f"cat#2 (re-read)={warm_t:.2f}s"
    )
    if cold_t > 0:
        ratio = warm_t / cold_t
        log.result(
            f"DIAGNOSTICS: re-read/cold ratio={ratio:.2f} "
            f"({'kernel page cache effective' if ratio < 0.5 else 'kernel page cache NOT effective — FUSE likely bypasses it'})"
        )
    return name


def _run_baseline_arm(
    in_paths: list[str], log_path: str | None = None,
) -> tuple[float, float, float, float]:
    """stop -> rpull v1 -> run -> read. Returns (stop_s, pull_s, run_s, read_s)."""
    log_window_start = time.time()
    name = _setup_warm_v0(in_paths)
    v1 = _pull_ref(1)

    t0 = time.perf_counter()
    stop_container(name)
    stop_t = time.perf_counter() - t0

    pull_t = timed_pull(
        ["sudo", "ctr-remote", "images", "rpull", "--plain-http", v1]
    )

    name2 = _next_container_name("refresh-v1")
    t0 = time.perf_counter()
    start_container(v1, name2)
    run_t = time.perf_counter() - t0

    read_t = _cat_all_in_container(name2, in_paths)
    _assert_mutated(name2, expected=True)
    stop_container(name2)
    log.result(
        f"  baseline: stop={stop_t:.2f}s pull={pull_t:.2f}s "
        f"run={run_t:.2f}s read={read_t:.2f}s"
    )
    if log_path:
        save_stargz_run_log(log_window_start, time.time(), log_path)
        log.result(f"  stargz logs -> {log_path}")
    return stop_t, pull_t, run_t, read_t


def _run_refresh_arm(
    in_paths: list[str], log_path: str | None = None,
) -> tuple[float, float]:
    """ctr-remote refresh v0 v1 -> read. Returns (refresh_s, read_s)."""
    log_window_start = time.time()
    name = _setup_warm_v0(in_paths)
    v0 = _pull_ref(0)
    v1 = _pull_ref(1)

    t0 = time.perf_counter()
    subprocess.run(
        ["sudo", "ctr-remote", "refresh", "--plain-http", v0, v1],
        check=True, capture_output=not log.VERBOSE,
    )
    refresh_t = time.perf_counter() - t0

    read_t = _cat_all_in_container(name, in_paths)
    _assert_mutated(name, expected=True)
    stop_container(name)
    log.result(f"  refresh:  refresh={refresh_t:.2f}s read={read_t:.2f}s")
    if log_path:
        save_stargz_run_log(log_window_start, time.time(), log_path)
        log.result(f"  stargz logs -> {log_path}")
    return refresh_t, read_t


def measure_refresh(snapshot_files: list[str], execution_ts: str) -> dict:
    """results = {
        "baseline": [(run, stop_s, pull_s, run_s, read_s), ...],
        "refresh":  [(run, refresh_s, read_s), ...],
    }
    """
    in_paths = _container_paths(snapshot_files)
    results: dict = {"baseline": [], "refresh": []}

    for run in range(N_RUNS):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info(f"\n[{ts}] === run {run + 1}/{N_RUNS} ===")

        baseline_log = refresh_log_path(
            SCRIPT_DIR, MODEL, SOURCE_IMAGE, "baseline", run, execution_ts,
        )
        refresh_log = refresh_log_path(
            SCRIPT_DIR, MODEL, SOURCE_IMAGE, "refresh", run, execution_ts,
        )

        # Alternate arm order per run to remove ordering bias.
        if run % 2 == 0:
            s, p, r, rd = _run_baseline_arm(in_paths, baseline_log)
            results["baseline"].append((run, s, p, r, rd))
            time.sleep(CFG.pull_cooldown)
            rf, rd2 = _run_refresh_arm(in_paths, refresh_log)
            results["refresh"].append((run, rf, rd2))
        else:
            rf, rd2 = _run_refresh_arm(in_paths, refresh_log)
            results["refresh"].append((run, rf, rd2))
            time.sleep(CFG.pull_cooldown)
            s, p, r, rd = _run_baseline_arm(in_paths, baseline_log)
            results["baseline"].append((run, s, p, r, rd))

        time.sleep(CFG.pull_cooldown)

    return results


# ── output ─────────────────────────────────────────────────────────────


def print_results(results: dict) -> None:
    n = N_RUNS
    log.result(f"\n=== Refresh-vs-Baseline Results (mean ± stddev, n={n} runs) ===")

    if results["baseline"]:
        arr = np.array([(s, p, r, rd) for _, s, p, r, rd in results["baseline"]])
        tot = arr.sum(axis=1)
        log.result(
            f"baseline:  stop={arr[:,0].mean():.2f}±{arr[:,0].std():.2f}  "
            f"pull={arr[:,1].mean():.2f}±{arr[:,1].std():.2f}  "
            f"run={arr[:,2].mean():.2f}±{arr[:,2].std():.2f}  "
            f"read={arr[:,3].mean():.2f}±{arr[:,3].std():.2f}  "
            f"total={tot.mean():.2f}±{tot.std():.2f}"
        )
    if results["refresh"]:
        arr = np.array([(rf, rd) for _, rf, rd in results["refresh"]])
        tot = arr.sum(axis=1)
        log.result(
            f"refresh:   refresh={arr[:,0].mean():.2f}±{arr[:,0].std():.2f}  "
            f"read={arr[:,1].mean():.2f}±{arr[:,1].std():.2f}  "
            f"total={tot.mean():.2f}±{tot.std():.2f}"
        )


def save_results_csv(results: dict, execution_ts: str) -> None:
    path = refresh_csv_path(SCRIPT_DIR, MODEL, SOURCE_IMAGE, execution_ts)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    fieldnames = [
        "run", "arm",
        "stop_s", "pull_s", "run_s", "refresh_s", "read_s", "total_s",
    ]
    rows: list[dict] = []

    for run, s, p, r, rd in results["baseline"]:
        rows.append({
            "run": run, "arm": "baseline",
            "stop_s": f"{s:.4f}", "pull_s": f"{p:.4f}", "run_s": f"{r:.4f}",
            "refresh_s": "", "read_s": f"{rd:.4f}",
            "total_s": f"{s + p + r + rd:.4f}",
        })
    for run, rf, rd in results["refresh"]:
        rows.append({
            "run": run, "arm": "refresh",
            "stop_s": "", "pull_s": "", "run_s": "",
            "refresh_s": f"{rf:.4f}", "read_s": f"{rd:.4f}",
            "total_s": f"{rf + rd:.4f}",
        })

    for stat_name, stat_fn in (("mean", np.mean), ("std", lambda a: np.std(a, ddof=0))):
        if results["baseline"]:
            arr = np.array([(s, p, r, rd) for _, s, p, r, rd in results["baseline"]])
            tot = arr.sum(axis=1)
            rows.append({
                "run": stat_name, "arm": "baseline",
                "stop_s": f"{float(stat_fn(arr[:,0])):.4f}",
                "pull_s": f"{float(stat_fn(arr[:,1])):.4f}",
                "run_s":  f"{float(stat_fn(arr[:,2])):.4f}",
                "refresh_s": "",
                "read_s": f"{float(stat_fn(arr[:,3])):.4f}",
                "total_s": f"{float(stat_fn(tot)):.4f}",
            })
        if results["refresh"]:
            arr = np.array([(rf, rd) for _, rf, rd in results["refresh"]])
            tot = arr.sum(axis=1)
            rows.append({
                "run": stat_name, "arm": "refresh",
                "stop_s": "", "pull_s": "", "run_s": "",
                "refresh_s": f"{float(stat_fn(arr[:,0])):.4f}",
                "read_s":    f"{float(stat_fn(arr[:,1])):.4f}",
                "total_s":   f"{float(stat_fn(tot)):.4f}",
            })

    write_csv(path, fieldnames, rows)


# ── chart ──────────────────────────────────────────────────────────────


PHASE_COLORS = {
    "stop":    "#7f7f7f",
    "pull":    "#1f77b4",
    "run":     "#2ca02c",
    "refresh": "#9467bd",
    "read":    "#ff7f0e",
}


def plot(results: dict, execution_ts: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 3.6))

    y_positions = [0, 1]
    labels = ["baseline", "refresh"]

    if results["baseline"]:
        arr = np.array([(s, p, r, rd) for _, s, p, r, rd in results["baseline"]])
        means = arr.mean(axis=0)
        cum_stds = np.cumsum(arr, axis=1).std(axis=0, ddof=0)
        left = 0.0
        for value, key in zip(means, ("stop", "pull", "run", "read")):
            ax.barh(
                0, value, left=left, height=0.5,
                color=PHASE_COLORS[key], edgecolor=PHASE_COLORS[key], linewidth=0.5,
            )
            left += value
        cum = np.cumsum(means)
        ax.errorbar(
            cum, [0] * len(cum), xerr=cum_stds,
            fmt="none", capsize=3, ecolor="black", elinewidth=1,
        )

    if results["refresh"]:
        arr = np.array([(rf, rd) for _, rf, rd in results["refresh"]])
        means = arr.mean(axis=0)
        cum_stds = np.cumsum(arr, axis=1).std(axis=0, ddof=0)
        left = 0.0
        for value, key in zip(means, ("refresh", "read")):
            ax.barh(
                1, value, left=left, height=0.5,
                color=PHASE_COLORS[key], edgecolor=PHASE_COLORS[key], linewidth=0.5,
            )
            left += value
        cum = np.cumsum(means)
        ax.errorbar(
            cum, [1] * len(cum), xerr=cum_stds,
            fmt="none", capsize=3, ecolor="black", elinewidth=1,
        )

    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels)
    ax.set_xlabel("Elapsed time (s)")
    ax.set_title(
        f"refresh vs baseline ({MODE}, mean ± stddev, n={N_RUNS} runs)"
    )
    ax.invert_yaxis()
    ax.grid(True, linestyle="--", alpha=0.3, axis="x")

    handles = [
        mpatches.Patch(facecolor=c, edgecolor=c, label=lbl)
        for lbl, c in PHASE_COLORS.items()
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.25),
        ncol=5, fontsize=9, frameon=False,
    )

    figure_footer(fig, MODEL, SOURCE_IMAGE)
    output_path = refresh_chart_path(SCRIPT_DIR, MODEL, SOURCE_IMAGE, execution_ts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.tight_layout(rect=(0, 0.12, 1, 1))
    save_figure(fig, output_path)


# ── main ───────────────────────────────────────────────────────────────


def main(execution_ts: str | None = None) -> None:
    if execution_ts is None:
        execution_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    log.set_verbose(VERBOSE)
    log.info(f"Model: {MODEL}")
    log.info(f"Mode: {MODE}")
    log.info(f"N_RUNS: {N_RUNS}")

    ensure_buildkit()
    prepare_local_registry(SOURCE_IMAGE, registry(CFG))

    artifacts_dir = refresh_artifacts_dir(
        SCRIPT_DIR, execution_ts, MODEL, SOURCE_IMAGE, build_mode(MODE)
    )
    toc_dir = os.path.join(artifacts_dir, "toc")
    log.result(f"TOC artifacts -> {toc_dir}")
    snapshot_files = prepare_refresh(
        artifacts_dir=artifacts_dir, toc_dir=toc_dir,
    )

    results = measure_refresh(snapshot_files, execution_ts)

    clear_registry(CFG, verbose=False, preserve_base=True)
    print_results(results)
    save_results_csv(results, execution_ts)
    plot(results, execution_ts)
    clear_artifacts(SCRIPT_DIR)


if __name__ == "__main__":
    main()
