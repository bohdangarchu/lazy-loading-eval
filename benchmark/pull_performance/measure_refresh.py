import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
import numpy as np

from shared import log
from shared.charts import MODE_COLORS, figure_footer, add_run_dots, save_figure, write_csv
from shared.config import load_config
from shared.registry import (
    prepare_local_registry, clear_registry, registry, image_slug,
    tdfs_cmd, stargz_base_image, zstd_base_image,
)
from shared.artifacts import write_2dfs_json, mutate_chunk
from shared.services import ensure_buildkit
from pull_performance.prepare import prepare_chunks, _clear_2dfs_cache
from pull_performance.measure import _next_container_name
from shared.services import clear_stargz_cache

EXPERIMENTS = [
    ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5GB     ~50 MB
    ("facebook/opt-350m", "docker.io/tensorflow/tensorflow"),                # ~1.4 GB     ~700 MB
    ("Qwen/Qwen2-1.5B", "docker.io/ollama/ollama"),                      # ~3.09 GB     ~3.4 GB
    ("openlm-research/open_llama_3b", "docker.io/ollama/ollama"),    # ~6.0 GB     ~3.4 GB
]
CFG = load_config()
VERBOSE = True
MODES = ["2dfs-stargz", "2dfs-stargz-zstd"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", "refresh")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts", "refresh")

_ALLOTMENT_RE = re.compile(r"Stargz Allotment 0/(\d+) ([a-f0-9]{64})")


# ── image naming ───────────────────────────────────────────────────────


def _build_name_refresh(source_image: str, cfg, mode: str, version_idx: int) -> str:
    return f"{registry(cfg)}/{image_slug(source_image)}-{mode}-refresh:v{version_idx}"


def _pull_name_refresh(source_image: str, cfg, mode: str, version_idx: int) -> str:
    end_col = CFG.refresh_n_splits - 1
    return f"{registry(cfg)}/library/{image_slug(source_image)}-{mode}-refresh:v{version_idx}--0.0.0.{end_col}"


# ── digest parsing ─────────────────────────────────────────────────────


def _parse_allotment_digests(output: str) -> dict[int, str]:
    """Parse {col: digest} from tdfs build output."""
    return {int(m.group(1)): m.group(2) for m in _ALLOTMENT_RE.finditer(output)}


# ── build helpers ──────────────────────────────────────────────────────


def _extra_flags(mode: str) -> list[str]:
    if mode == "2dfs-stargz":
        return ["--enable-stargz", "--stargz-chunk-size", "2097152"]
    if mode == "2dfs-stargz-zstd":
        return ["--enable-stargz", "--use-zstd", "--stargz-chunk-size", "8388608"]
    raise ValueError(f"Unknown mode: {mode}")


def _base_image(source_image: str, cfg, mode: str) -> str:
    if mode == "2dfs-stargz":
        return stargz_base_image(source_image, cfg)
    if mode == "2dfs-stargz-zstd":
        return zstd_base_image(source_image, cfg)
    raise ValueError(f"Unknown mode: {mode}")


def _build_version(
    chunk_paths: list[str],
    source_image: str,
    cfg,
    mode: str,
    version_idx: int,
) -> dict[int, str]:
    """Build one refresh image version and return its allotment digest map."""
    target = _build_name_refresh(source_image, cfg, mode, version_idx)
    base = _base_image(source_image, cfg, mode)

    write_2dfs_json(chunk_paths, SCRIPT_DIR)
    cmd = tdfs_cmd(cfg, SCRIPT_DIR) + [
        "build", "--platforms", "linux/amd64",
        *_extra_flags(mode),
        "--force-http", "-f", "2dfs.json",
        base, target,
    ]
    log.info(f"Building {mode} refresh v{version_idx}: {target}")
    result = subprocess.run(
        cmd, check=True, cwd=SCRIPT_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if log.VERBOSE:
        print(result.stdout, end="")
    log.result(f"Built {target}")

    digests = _parse_allotment_digests(result.stdout)
    log.info(f"  Parsed digests: { {c: d[:12] + '...' for c, d in digests.items()} }")

    push_cmd = tdfs_cmd(cfg, SCRIPT_DIR) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")

    return digests


# ── prepare ────────────────────────────────────────────────────────────


def prepare_refresh(
    chunk_paths: list[str],
    source_image: str,
    cfg,
    mode: str,
) -> list[dict[int, str]]:
    """Build CFG.refresh_n_splits+1 image versions, return all_digests[version_idx][col].

    Build sequence (cache cleared once at start):
      v0: original chunks
      v1: chunk 0 bit-flipped
      v2: chunk 0 + chunk 1 bit-flipped
      ...
      vN: all chunks bit-flipped
    Chunks are restored to original content after all builds.
    """
    _clear_2dfs_cache(cfg)

    all_digests: list[dict[int, str]] = []

    # v0: no mutations
    parsed = _build_version(chunk_paths, source_image, cfg, mode, 0)
    all_digests.append(parsed)

    # v1..vN: cumulative mutations
    for k in range(1, CFG.refresh_n_splits + 1):
        mutate_chunk(chunk_paths[k - 1])
        parsed = _build_version(chunk_paths, source_image, cfg, mode, k)
        # Inherit digests from previous version for splits not logged (cache hits)
        inherited = dict(all_digests[k - 1])
        inherited.update(parsed)
        all_digests.append(inherited)

    # Restore: double-flip restores original bits
    log.info("Restoring chunk files...")
    for path in chunk_paths:
        mutate_chunk(path)
    log.result("Chunks restored.")

    return all_digests


# ── container helpers ──────────────────────────────────────────────────


def _timed_pull(cmd: list[str]) -> float:
    start = time.perf_counter()
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Pull failed (exit {result.returncode}):\n{result.stderr}")
    return time.perf_counter() - start


def _start_container(image: str, name: str) -> None:
    """Start a detached stargz container that stays alive via sleep infinity."""
    subprocess.run(
        ["sudo", "ctr-remote", "run", "--detach", "--snapshotter=stargz",
         image, name, "sleep", "infinity"],
        check=True, capture_output=not log.VERBOSE,
    )


def _exec_timed(name: str, n: int) -> float:
    """Exec into running container, cat n chunk files, return elapsed seconds."""
    files = " ".join(f"/chunk{i + 1}.bin" for i in range(n))
    exec_id = uuid.uuid4().hex[:8]
    start = time.perf_counter()
    subprocess.run(
        ["sudo", "ctr", "tasks", "exec", "--exec-id", exec_id,
         name, "sh", "-c", f"cat {files} > /dev/null"],
        check=True, capture_output=not log.VERBOSE,
    )
    return time.perf_counter() - start


def _stop_container(name: str) -> None:
    subprocess.run(["sudo", "nerdctl", "stop", name], check=True,
                   capture_output=not log.VERBOSE)
    subprocess.run(["sudo", "ctr", "tasks", "delete", name], check=True,
                   capture_output=not log.VERBOSE)
    subprocess.run(["sudo", "ctr", "containers", "delete", name], check=True,
                   capture_output=not log.VERBOSE)


def _refresh_layer(old_digest: str, new_digest: str) -> None:
    old = f"sha256:{old_digest}"
    new = f"sha256:{new_digest}"
    log.info(f"  refresh-layer {old[:19]}... -> {new[:19]}...")
    subprocess.run(
        ["sudo", "ctr-remote", "refresh-layer", old, new],
        check=True, capture_output=not log.VERBOSE,
    )


# ── measurement ────────────────────────────────────────────────────────


def measure_refresh(
    source_image: str,
    cfg,
    all_digests_per_mode: dict[str, list[dict[int, str]]],
) -> dict[str, list[tuple[int, int, float, float, float]]]:
    """results[mode] = list of (run, k, pull_t, baseline_t, refresh_t)"""
    results: dict[str, list[tuple[int, int, float, float, float]]] = {m: [] for m in MODES}

    for mode in MODES:
        all_digests = all_digests_per_mode[mode]

        for run in range(CFG.refresh_n_runs):
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                     f"=== {mode} run {run + 1}/{CFG.refresh_n_runs} ===")
            for k in range(1, CFG.refresh_n_splits + 1):
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                log.info(f"\n[{ts}] === {mode}: refresh {k} layer(s) ===")

                clear_stargz_cache()

                v0_pull = _pull_name_refresh(source_image, cfg, mode, 0)
                log.info(f"Pulling v0: {v0_pull}")
                pull_t = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", v0_pull])
                log.result(f"  pull: {pull_t:.2f}s")

                name = _next_container_name(f"refresh-{mode.replace('-', '')}")
                _start_container(v0_pull, name)

                baseline_t = _exec_timed(name, CFG.refresh_n_splits)
                log.result(f"  baseline access: {baseline_t:.2f}s")

                for i in range(k):
                    old_digest = all_digests[0][i]
                    new_digest = all_digests[i + 1][i]
                    _refresh_layer(old_digest, new_digest)

                refresh_t = _exec_timed(name, CFG.refresh_n_splits)
                log.result(f"  post-refresh access ({k} layers): {refresh_t:.2f}s")

                _stop_container(name)
                log.info(f"\nSleeping {cfg.pull_cooldown}s before next...")
                time.sleep(cfg.pull_cooldown)

                results[mode].append((run, k, pull_t, baseline_t, refresh_t))

    return results


# ── output ─────────────────────────────────────────────────────────────


def print_results(results: dict[str, list[tuple[int, int, float, float, float]]]) -> None:
    log.result("\n=== Refresh Performance Results (median across runs) ===")
    log.result(f"{'k':>4}  {'mode':<20}  {'pull':>8}  {'baseline':>10}  {'refresh':>10}")
    log.result("-" * 60)
    for mode, entries in results.items():
        for k in range(1, CFG.refresh_n_splits + 1):
            group = [(pull_t, baseline_t, refresh_t)
                     for _, kk, pull_t, baseline_t, refresh_t in entries if kk == k]
            if not group:
                continue
            med_pull = float(np.median([g[0] for g in group]))
            med_base = float(np.median([g[1] for g in group]))
            med_ref = float(np.median([g[2] for g in group]))
            log.result(f"{k:>4}  {mode:<20}  {med_pull:>8.2f}  {med_base:>10.2f}  {med_ref:>10.2f}")


def save_results_csv(
    results: dict[str, list[tuple[int, int, float, float, float]]],
    model: str,
    base_image: str,
) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"{model_slug}_{img_slug}_refresh_{ts}.csv")

    fieldnames = ["run", "n_refreshed"]
    for mode in results:
        slug = mode.replace("-", "_")
        fieldnames += [f"{slug}_pull_s", f"{slug}_baseline_s", f"{slug}_refresh_s"]

    rows = []
    for run in range(CFG.refresh_n_runs):
        for k in range(1, CFG.refresh_n_splits + 1):
            row: dict = {"run": run, "n_refreshed": k}
            for mode, entries in results.items():
                slug = mode.replace("-", "_")
                match = [(p, b, r) for rr, kk, p, b, r in entries if rr == run and kk == k]
                if match:
                    p, b, r = match[0]
                    row[f"{slug}_pull_s"] = f"{p:.4f}"
                    row[f"{slug}_baseline_s"] = f"{b:.4f}"
                    row[f"{slug}_refresh_s"] = f"{r:.4f}"
                else:
                    row[f"{slug}_pull_s"] = row[f"{slug}_baseline_s"] = row[f"{slug}_refresh_s"] = ""
            rows.append(row)

    write_csv(path, fieldnames, rows)


def plot(
    results: dict[str, list[tuple[int, int, float, float, float]]],
    model: str,
    base_image: str,
) -> None:
    os.makedirs(CHARTS_DIR, exist_ok=True)
    k_values = list(range(1, CFG.refresh_n_splits + 1))
    x = np.arange(len(k_values))
    n_modes = len(results)
    width = min(0.8 / n_modes, 0.2)

    fig, ax = plt.subplots(figsize=(max(8, n_modes * 3), 6))

    for i, (mode, entries) in enumerate(results.items()):
        color = MODE_COLORS[mode]
        offset = (i - (n_modes - 1) / 2) * width

        # Dashed baseline line (median baseline across all k, per mode)
        all_baselines = [b for _, _, _, b, _ in entries]
        if all_baselines:
            med_baseline = float(np.median(all_baselines))
            ax.axhline(med_baseline, color=color, linestyle="--", linewidth=1.0, alpha=0.6)

        med_refresh = []
        for j, k in enumerate(k_values):
            group = [refresh_t for _, kk, _, _, refresh_t in entries if kk == k]
            med_r = float(np.median(group)) if group else 0.0
            med_refresh.append(med_r)
            x_center = x[j] + offset + width / 2
            add_run_dots(ax, x_center, group)

        ax.bar(x + offset, med_refresh, width, color=color,
               edgecolor=color, linewidth=0.5, label=mode)

    ax.set_xlabel("Number of layers refreshed")
    ax.set_ylabel("Access time (s)")
    ax.set_title(
        f"refresh-layer access time (median, n={CFG.refresh_n_runs} runs, dots = individual runs)\n"
        f"dashed = baseline access before refresh"
    )
    ax.set_xticks(x)
    ax.set_xticklabels(k_values)
    ax.grid(True, linestyle="--", alpha=0.3, axis="y")

    method_handles = [mpatches.Patch(facecolor=MODE_COLORS[m], edgecolor=MODE_COLORS[m], label=m)
                      for m in results]
    baseline_handle = Line2D([0], [0], color="gray", linestyle="--", linewidth=1.0,
                             label="baseline (dashed)")
    ax.legend(handles=method_handles + [baseline_handle], loc="upper left")

    figure_footer(fig, model, base_image)

    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(CHARTS_DIR, f"{model_slug}_{img_slug}_refresh_{ts}.png")
    fig.tight_layout()
    save_figure(fig, output_path)


# ── main ───────────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    ensure_buildkit()
    log.info(f"Modes: {MODES}")
    log.info(f"CFG.refresh_n_splits: {CFG.refresh_n_splits}")
    log.info(f"CFG.refresh_n_runs: {CFG.refresh_n_runs}")

    for model, base_image in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} =====")

        chunk_paths = prepare_chunks(model, CFG.refresh_n_splits)

        prepare_local_registry(base_image, registry(CFG))

        all_digests_per_mode: dict[str, list[dict[int, str]]] = {}

        clear_registry(CFG, preserve_base=True)
        for mode in MODES:
            log.info(f"\n=== Preparing mode: {mode} ===")
            all_digests_per_mode[mode] = prepare_refresh(chunk_paths, base_image, CFG, mode)

        results = measure_refresh(base_image, CFG, all_digests_per_mode)

        clear_registry(CFG, preserve_base=True)

        print_results(results)
        save_results_csv(results, model, base_image)
        plot(results, model, base_image)


if __name__ == "__main__":
    main()
