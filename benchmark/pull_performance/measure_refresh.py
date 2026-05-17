import os
import subprocess
import time
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
from shared.prometheus import bytes_by_layer
from shared.registry import (
    clear_registry, fetch_layer_digests, image_slug, prepare_local_registry,
    registry, save_toc, tdfs_cmd,
)
from shared.services import (
    clear_2dfs_cache, clear_stargz_cache, ensure_buildkit, save_stargz_run_log,
)
from pull_performance.measure import _next_container_name
from pull_performance.paths import (
    refresh_artifacts_dir, refresh_bytes_chart_path, refresh_bytes_csv_path,
    refresh_chart_path, refresh_csv_path, refresh_log_path,
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
OP_TYPES = ["on_demand_bytes_fetched"]
PROM_SETTLE_S = 1.0  # > scrape_interval (500ms) so post-op scrape is visible

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


def _export_tocs(version: int, toc_dir: str) -> list[str]:
    os.makedirs(toc_dir, exist_ok=True)
    repo = _repo()
    digests = fetch_layer_digests(registry(CFG), repo, f"v{version}--0.0.0.0")
    log.info(f"v{version} layers: {[d[:19] for d in digests]}")
    for i, d in enumerate(digests):
        save_toc(
            registry(CFG), repo, d,
            os.path.join(toc_dir, f"toc_v{version}_layer{i}.json"),
        )
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


def _snapshot_bytes() -> dict[str, dict[str, int]]:
    """Returns {op_type: {layer: bytes}} at current time."""
    return {op: bytes_by_layer(op) for op in OP_TYPES}


def _delta(before: dict[str, dict[str, int]],
           after: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Per-op_type, per-layer delta. Layers present only in `after` count as new."""
    out: dict[str, dict[str, int]] = {}
    for op in OP_TYPES:
        b = before.get(op, {})
        a = after.get(op, {})
        d: dict[str, int] = {}
        for layer in set(b) | set(a):
            v = a.get(layer, 0) - b.get(layer, 0)
            if v != 0:
                d[layer] = v
        out[op] = d
    return out


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _log_deltas(label: str, deltas: dict[str, dict[str, int]]) -> None:
    for op in OP_TYPES:
        total = sum(deltas.get(op, {}).values())
        log.result(f"  {label} {op}: total={_fmt_bytes(total)} "
                   f"layers={len(deltas.get(op, {}))}")


def _setup_warm_v0(
    in_paths: list[str],
) -> tuple[str, dict[str, dict[str, int]]]:
    """Clear stargz cache, pull v0, start container, warm page cache via cat.

    Diagnostic: cat twice back-to-back. If second cat is fast, the kernel page
    cache is being used; if both are ~same, FUSE is bypassing it (in which case
    refresh cannot help regardless of mount preservation).
    """
    clear_stargz_cache()
    time.sleep(PROM_SETTLE_S)
    before = _snapshot_bytes()
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
    time.sleep(PROM_SETTLE_S)
    v0_warm_deltas = _delta(before, _snapshot_bytes())
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
    _log_deltas("v0_warm", v0_warm_deltas)
    return name, v0_warm_deltas


def _run_baseline_arm(
    in_paths: list[str], log_path: str | None = None,
) -> tuple[float, float, float, float,
           dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """stop -> rpull v1 -> run -> read.
    Returns (stop_s, pull_s, run_s, read_s, v0_warm_deltas, update_deltas)."""
    log_window_start = time.time()
    name, v0_warm_deltas = _setup_warm_v0(in_paths)
    v1 = _pull_ref(1)

    update_before = _snapshot_bytes()

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
    time.sleep(PROM_SETTLE_S)
    update_deltas = _delta(update_before, _snapshot_bytes())
    stop_container(name2)
    log.result(
        f"  baseline: stop={stop_t:.2f}s pull={pull_t:.2f}s "
        f"run={run_t:.2f}s read={read_t:.2f}s"
    )
    _log_deltas("baseline update", update_deltas)
    if log_path:
        save_stargz_run_log(log_window_start, time.time(), log_path)
        log.result(f"  stargz logs -> {log_path}")
    return stop_t, pull_t, run_t, read_t, v0_warm_deltas, update_deltas


def _run_refresh_arm(
    in_paths: list[str], log_path: str | None = None,
) -> tuple[float, float,
           dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """ctr-remote refresh v0 v1 -> read.
    Returns (refresh_s, read_s, v0_warm_deltas, update_deltas)."""
    log_window_start = time.time()
    name, v0_warm_deltas = _setup_warm_v0(in_paths)
    v0 = _pull_ref(0)
    v1 = _pull_ref(1)

    update_before = _snapshot_bytes()

    t0 = time.perf_counter()
    subprocess.run(
        ["sudo", "ctr-remote", "refresh", "--plain-http", v0, v1],
        check=True, capture_output=not log.VERBOSE,
    )
    refresh_t = time.perf_counter() - t0

    read_t = _cat_all_in_container(name, in_paths)
    _assert_mutated(name, expected=True)
    time.sleep(PROM_SETTLE_S)
    update_deltas = _delta(update_before, _snapshot_bytes())
    stop_container(name)
    log.result(f"  refresh:  refresh={refresh_t:.2f}s read={read_t:.2f}s")
    _log_deltas("refresh update", update_deltas)
    if log_path:
        save_stargz_run_log(log_window_start, time.time(), log_path)
        log.result(f"  stargz logs -> {log_path}")
    return refresh_t, read_t, v0_warm_deltas, update_deltas


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
            baseline = _run_baseline_arm(in_paths, baseline_log)
            results["baseline"].append((run, *baseline))
            time.sleep(CFG.pull_cooldown)
            refresh = _run_refresh_arm(in_paths, refresh_log)
            results["refresh"].append((run, *refresh))
        else:
            refresh = _run_refresh_arm(in_paths, refresh_log)
            results["refresh"].append((run, *refresh))
            time.sleep(CFG.pull_cooldown)
            baseline = _run_baseline_arm(in_paths, baseline_log)
            results["baseline"].append((run, *baseline))

        time.sleep(CFG.pull_cooldown)

    return results


# ── output ─────────────────────────────────────────────────────────────


def print_results(results: dict) -> None:
    n = N_RUNS
    log.result(f"\n=== Refresh-vs-Baseline Results (mean ± stddev, n={n} runs) ===")

    if results["baseline"]:
        arr = np.array(
            [(stop, pull, run, read)
             for _, stop, pull, run, read, _, _ in results["baseline"]]
        )
        tot = arr.sum(axis=1)
        log.result(
            f"baseline:  stop={arr[:,0].mean():.2f}±{arr[:,0].std():.2f}  "
            f"pull={arr[:,1].mean():.2f}±{arr[:,1].std():.2f}  "
            f"run={arr[:,2].mean():.2f}±{arr[:,2].std():.2f}  "
            f"read={arr[:,3].mean():.2f}±{arr[:,3].std():.2f}  "
            f"total={tot.mean():.2f}±{tot.std():.2f}"
        )
    if results["refresh"]:
        arr = np.array(
            [(refresh, read) for _, refresh, read, _, _ in results["refresh"]]
        )
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

    for run, stop, pull, run_t, read, _, _ in results["baseline"]:
        rows.append({
            "run": run, "arm": "baseline",
            "stop_s": f"{stop:.4f}", "pull_s": f"{pull:.4f}",
            "run_s": f"{run_t:.4f}",
            "refresh_s": "", "read_s": f"{read:.4f}",
            "total_s": f"{stop + pull + run_t + read:.4f}",
        })
    for run, refresh, read, _, _ in results["refresh"]:
        rows.append({
            "run": run, "arm": "refresh",
            "stop_s": "", "pull_s": "", "run_s": "",
            "refresh_s": f"{refresh:.4f}", "read_s": f"{read:.4f}",
            "total_s": f"{refresh + read:.4f}",
        })

    for stat_name, stat_fn in (("mean", np.mean), ("std", lambda a: np.std(a, ddof=0))):
        if results["baseline"]:
            arr = np.array(
                [(stop, pull, run_t, read)
                 for _, stop, pull, run_t, read, _, _ in results["baseline"]]
            )
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
            arr = np.array(
                [(refresh, read) for _, refresh, read, _, _ in results["refresh"]]
            )
            tot = arr.sum(axis=1)
            rows.append({
                "run": stat_name, "arm": "refresh",
                "stop_s": "", "pull_s": "", "run_s": "",
                "refresh_s": f"{float(stat_fn(arr[:,0])):.4f}",
                "read_s":    f"{float(stat_fn(arr[:,1])):.4f}",
                "total_s":   f"{float(stat_fn(tot)):.4f}",
            })

    write_csv(path, fieldnames, rows)


# ── bytes output ───────────────────────────────────────────────────────


def _iter_phase_deltas(results: dict):
    """Yields (run, arm, phase, deltas) for every recorded window."""
    for run, _, _, _, _, v0_warm, update in results["baseline"]:
        yield run, "baseline", "v0_warm", v0_warm
        yield run, "baseline", "update", update
    for run, _, _, v0_warm, update in results["refresh"]:
        yield run, "refresh", "v0_warm", v0_warm
        yield run, "refresh", "update", update


def save_bytes_csv(results: dict, execution_ts: str) -> None:
    path = refresh_bytes_csv_path(SCRIPT_DIR, MODEL, SOURCE_IMAGE, execution_ts)
    fieldnames = ["run", "arm", "phase", "layer", "op_type", "bytes"]
    rows: list[dict] = []

    for run, arm, phase, deltas in _iter_phase_deltas(results):
        for op in OP_TYPES:
            for layer, b in sorted(deltas.get(op, {}).items()):
                rows.append({
                    "run": run, "arm": arm, "phase": phase,
                    "layer": layer, "op_type": op, "bytes": b,
                })

    # Per-(arm, phase, op_type) total summary across runs.
    totals: dict[tuple[str, str, str], list[int]] = {}
    for _, arm, phase, deltas in _iter_phase_deltas(results):
        for op in OP_TYPES:
            totals.setdefault((arm, phase, op), []).append(
                sum(deltas.get(op, {}).values())
            )
    for (arm, phase, op), vals in sorted(totals.items()):
        a = np.array(vals, dtype=float)
        rows.append({
            "run": "mean", "arm": arm, "phase": phase,
            "layer": "", "op_type": op, "bytes": f"{a.mean():.1f}",
        })
        rows.append({
            "run": "std", "arm": arm, "phase": phase,
            "layer": "", "op_type": op, "bytes": f"{a.std(ddof=0):.1f}",
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
        arr = np.array(
            [(stop, pull, run_t, read)
             for _, stop, pull, run_t, read, _, _ in results["baseline"]]
        )
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
        arr = np.array(
            [(refresh, read) for _, refresh, read, _, _ in results["refresh"]]
        )
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


def plot_bytes(results: dict, execution_ts: str) -> None:
    """One bar per (arm, phase). Bytes = on_demand_bytes_fetched, mean across runs."""
    op = OP_TYPES[0]
    # (arm, phase, descriptive label, color)
    groups = [
        ("baseline", "v0_warm", "setup",  "#9ecae1"),
        ("baseline", "update",  "update", "#1f77b4"),
        ("refresh",  "v0_warm", "setup",  "#fdbe85"),
        ("refresh",  "update",  "update", "#ff7f0e"),
    ]

    totals_by_key: dict[tuple[str, str], list[int]] = {}
    for _, arm, phase, deltas in _iter_phase_deltas(results):
        totals_by_key.setdefault((arm, phase), []).append(
            sum(deltas.get(op, {}).values())
        )

    if not any(totals_by_key.values()):
        log.info("plot_bytes: no data, skipping")
        return

    fig, ax = plt.subplots(figsize=(10, 4.0))
    bar_w = 0.6
    x_centers = np.arange(len(groups), dtype=float)

    means_gb: list[float] = []
    for gi, (arm, phase, _, color) in enumerate(groups):
        vals = totals_by_key.get((arm, phase), [0])
        m_gb = float(np.mean(vals)) / (1024 ** 3)
        means_gb.append(m_gb)
        ax.bar(
            x_centers[gi], m_gb, bar_w,
            color=color, edgecolor="white", linewidth=0.3,
        )
        if m_gb > 0:
            ax.text(
                x_centers[gi], m_gb,
                _fmt_bytes(int(m_gb * (1024 ** 3))),
                ha="center", va="bottom", fontsize=10, fontweight="bold",
            )

    if max(means_gb) > 0:
        ax.set_ylim(0, max(means_gb) * 1.18)

    ax.set_xticks(x_centers)
    ax.set_xticklabels([g[2] for g in groups], fontsize=9)
    # Arm name above each tick
    for gi, (arm, _, _, _) in enumerate(groups):
        ax.text(
            x_centers[gi], 1.02, arm,
            ha="center", va="bottom", fontsize=10, fontweight="bold",
            transform=ax.get_xaxis_transform(),
        )

    ax.set_ylabel("GiB")
    ax.set_title(
        f"stargz on-demand bytes fetched per phase  "
        f"({MODE}, n={N_RUNS} runs)",
        pad=24,
    )
    ax.grid(True, linestyle="--", alpha=0.3, axis="y")

    figure_footer(fig, MODEL, SOURCE_IMAGE, fontsize=7)
    output_path = refresh_bytes_chart_path(
        SCRIPT_DIR, MODEL, SOURCE_IMAGE, execution_ts
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.tight_layout(rect=(0, 0.10, 1, 1))
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
    save_bytes_csv(results, execution_ts)
    plot(results, execution_ts)
    plot_bytes(results, execution_ts)
    clear_artifacts(SCRIPT_DIR)


if __name__ == "__main__":
    main()
