import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from shared import log
from shared.charts import MODE_COLORS, figure_footer, add_run_dots, save_figure, write_csv
from pull_performance.paths import refresh_csv_path, refresh_chart_path
from shared.config import load_config
from shared.registry import (
    prepare_local_registry, clear_registry, registry, image_slug,
    tdfs_cmd, stargz_base_image, zstd_base_image,
)
from shared.artifacts import write_2dfs_json, mutate_chunk
from shared.services import ensure_buildkit, clear_2dfs_cache, clear_stargz_cache
from pull_performance.prepare import prepare_chunks
from pull_performance.measure import _next_container_name

EXPERIMENTS = [
    # ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5GB     ~50 MB
    ("Qwen/Qwen2-1.5B", "docker.io/library/python:3.12-slim"),                      # ~3.09 GB     ~3.4 GB
    # ("openlm-research/open_llama_3b", "docker.io/library/python:3.12-slim"),    # ~6.85 GB     ~3.4 GB
]
CFG = load_config()
VERBOSE = True
MODES = [
    "2dfs-stargz-with-bg-fetch",
    "2dfs-stargz-zstd-with-bg-fetch",
    "2dfs-stargz",
    "2dfs-stargz-zstd",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

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


def _is_bg_fetch_mode(mode: str) -> bool:
    return mode.endswith("-with-bg-fetch")


def _build_mode(mode: str) -> str:
    """Strip bg-fetch suffix; build behavior is identical to the base mode."""
    if _is_bg_fetch_mode(mode):
        return mode[: -len("-with-bg-fetch")]
    return mode


def _extra_flags(mode: str) -> list[str]:
    base = _build_mode(mode)
    if base == "2dfs-stargz":
        return ["--enable-stargz", "--stargz-chunk-size", "2097152"]
    if base == "2dfs-stargz-zstd":
        return ["--enable-stargz", "--use-zstd", "--stargz-chunk-size", "8388608"]
    raise ValueError(f"Unknown mode: {mode}")


def _base_image(source_image: str, cfg, mode: str) -> str:
    base = _build_mode(mode)
    if base == "2dfs-stargz":
        return stargz_base_image(source_image, cfg)
    if base == "2dfs-stargz-zstd":
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
    clear_2dfs_cache(cfg)

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


def _refresh_layer(old_digest: str, new_digest: str, with_bg_fetch: bool = False) -> None:
    old = f"sha256:{old_digest}"
    new = f"sha256:{new_digest}"
    log.info(f"  refresh-layer {old[:19]}... -> {new[:19]}...")
    cmd = ["sudo", "ctr-remote", "refresh-layer", old, new]
    if with_bg_fetch:
        cmd.append("--with-background-fetch")
    subprocess.run(cmd, check=True, capture_output=not log.VERBOSE)


# ── measurement ────────────────────────────────────────────────────────


def measure_refresh(
    source_image: str,
    cfg,
    all_digests_per_mode: dict[str, list[dict[int, str]]],
) -> dict[str, list[tuple[int, int, float, float, float]]]:
    """results[mode] = list of (run, k, pull_t, layer_refresh_t, file_access_t)"""
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
                pull_t = _timed_pull(
                    ["sudo", "ctr-remote", "images", "rpull", "--plain-http", v0_pull]
                )
                log.result(f"  pull ({mode}): {pull_t:.2f}s")

                name = _next_container_name(f"refresh-{mode.replace('-', '')}")
                _start_container(v0_pull, name)

                with_bg_fetch = _is_bg_fetch_mode(mode)
                t0 = time.perf_counter()
                for i in range(k):
                    old_digest = all_digests[0][i]
                    new_digest = all_digests[i + 1][i]
                    _refresh_layer(old_digest, new_digest, with_bg_fetch=with_bg_fetch)
                layer_refresh_t = time.perf_counter() - t0
                log.result(f"  refresh-layer ({k} layers, {mode}): {layer_refresh_t:.2f}s")

                file_access_t = _exec_timed(name, k)
                log.result(f"  post-refresh file access ({k} refreshed files, {mode}): {file_access_t:.2f}s")

                _stop_container(name)
                log.info(f"\nSleeping {cfg.pull_cooldown}s before next...")
                time.sleep(cfg.pull_cooldown)

                results[mode].append((run, k, pull_t, layer_refresh_t, file_access_t))

    return results


# ── output ─────────────────────────────────────────────────────────────


def print_results(results: dict[str, list[tuple[int, int, float, float, float]]]) -> None:
    log.result("\n=== Refresh Performance Results (median across runs) ===")
    log.result(f"{'k':>4}  {'mode':<20}  {'pull':>8}  {'layer_refresh':>14}  {'file_access':>12}")
    log.result("-" * 64)
    for mode, entries in results.items():
        for k in range(1, CFG.refresh_n_splits + 1):
            group = [(pull_t, lr_t, fa_t)
                     for _, kk, pull_t, lr_t, fa_t in entries if kk == k]
            if not group:
                continue
            med_pull = float(np.median([g[0] for g in group]))
            med_lr = float(np.median([g[1] for g in group]))
            med_fa = float(np.median([g[2] for g in group]))
            log.result(f"{k:>4}  {mode:<20}  {med_pull:>8.2f}  {med_lr:>14.2f}  {med_fa:>12.2f}")


def save_results_csv(
    results: dict[str, list[tuple[int, int, float, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    path = refresh_csv_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    fieldnames = ["run", "n_refreshed"]
    for mode in results:
        slug = mode.replace("-", "_")
        fieldnames += [f"{slug}_pull_s", f"{slug}_layer_refresh_s", f"{slug}_file_access_s"]

    rows = []
    for run in range(CFG.refresh_n_runs):
        for k in range(1, CFG.refresh_n_splits + 1):
            row: dict = {"run": run, "n_refreshed": k}
            for mode, entries in results.items():
                slug = mode.replace("-", "_")
                match = [(p, lr, fa) for rr, kk, p, lr, fa in entries if rr == run and kk == k]
                if match:
                    p, lr, fa = match[0]
                    row[f"{slug}_pull_s"] = f"{p:.4f}"
                    row[f"{slug}_layer_refresh_s"] = f"{lr:.4f}"
                    row[f"{slug}_file_access_s"] = f"{fa:.4f}"
                else:
                    row[f"{slug}_pull_s"] = ""
                    row[f"{slug}_layer_refresh_s"] = row[f"{slug}_file_access_s"] = ""
            rows.append(row)

    write_csv(path, fieldnames, rows)


def plot(
    results: dict[str, list[tuple[int, int, float, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    k_values = list(range(1, CFG.refresh_n_splits + 1))
    x = np.arange(len(k_values))
    n_modes = len(results)
    width = min(0.8 / n_modes, 0.2)

    fig, ax = plt.subplots(figsize=(10, 6.5))

    for i, (mode, entries) in enumerate(results.items()):
        color = MODE_COLORS[mode]
        offset = (i - (n_modes - 1) / 2) * width

        med_lr = []
        med_fa = []
        for j, k in enumerate(k_values):
            lr_group = [lr for _, kk, _, lr, _ in entries if kk == k]
            fa_group = [fa for _, kk, _, _, fa in entries if kk == k]
            med_lr.append(float(np.median(lr_group)) if lr_group else 0.0)
            med_fa.append(float(np.median(fa_group)) if fa_group else 0.0)
            x_center = x[j] + offset + width / 2
            total_group = [lr + fa for lr, fa in zip(lr_group, fa_group)]
            add_run_dots(ax, x_center, total_group)

        ax.bar(x + offset, med_lr, width, color=color,
               edgecolor=color, linewidth=0.5, alpha=1.0, label=f"{mode} (layer refresh)")
        ax.bar(x + offset, med_fa, width, bottom=med_lr, color=color,
               edgecolor=color, linewidth=0.5, alpha=0.45, label=f"{mode} (file access)")

    ax.set_xlabel("Number of layers refreshed")
    ax.set_ylabel("Access time (s)")
    ax.set_title(
        f"refresh-layer total time (median, n={CFG.refresh_n_runs} runs, dots = individual run totals)"
    )
    ax.set_xticks(x)
    ax.set_xticklabels(k_values)
    ax.grid(True, linestyle="--", alpha=0.3, axis="y")

    mode_handles = []
    for m in results:
        c = MODE_COLORS[m]
        mode_handles.append(mpatches.Patch(facecolor=c, edgecolor=c, alpha=1.0, label=m))
    style_handles = [
        mpatches.Patch(facecolor="gray", edgecolor="gray", alpha=1.0, label="layer refresh"),
        mpatches.Patch(facecolor="gray", edgecolor="gray", alpha=0.45, label="file access"),
    ]
    ax.legend(
        handles=mode_handles + style_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=min(4, len(mode_handles) + len(style_handles)),
        fontsize=9,
        frameon=False,
    )

    figure_footer(fig, model, base_image)

    output_path = refresh_chart_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    save_figure(fig, output_path)


# ── main ───────────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    ensure_buildkit()
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
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
        save_results_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, execution_ts)


if __name__ == "__main__":
    main()
