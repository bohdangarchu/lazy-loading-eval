import os
import subprocess
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from shared import log
from shared.charts import figure_footer, save_figure, write_csv
from shared.config import load_config
from shared.registry import (
    prepare_local_registry, clear_registry, registry, image_slug,
    tdfs_cmd,
)
from shared.artifacts import write_2dfs_json, mutate_chunk, snapshot_artifacts
from shared.services import clear_2dfs_cache, clear_stargz_cache
from pull_performance.paths import (
    manual_update_csv_path, manual_update_chart_path, manual_update_artifacts_dir,
)
from pull_performance.prepare import prepare_chunks
from pull_performance.measure import _next_container_name
from pull_performance.refresh_common import (
    EXPERIMENTS,
    start_container, stop_container, exec_timed, timed_pull,
    extra_flags, base_image, build_mode,
)

CFG = load_config()
VERBOSE = True
MANUAL_UPDATE_MODES = ["baseline-2dfs-stargz"]
PARTITION_PERCENTS = [25, 50, 75, 100]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ── image naming ───────────────────────────────────────────────────────


def _build_name_manual(source_image: str, cfg, mode: str, version: int) -> str:
    return f"{registry(cfg)}/{image_slug(source_image)}-{mode}-manual:v{version}"


def _pull_name_manual(source_image: str, cfg, mode: str, version: int, max_allowed_splits: int) -> str:
    end_col = max_allowed_splits - 1
    return f"{registry(cfg)}/library/{image_slug(source_image)}-{mode}-manual:v{version}--0.0.0.{end_col}"


# ── build helpers ──────────────────────────────────────────────────────


def _build_version(
    chunk_paths: list[str],
    source_image: str,
    cfg,
    mode: str,
    version: int,
) -> None:
    target = _build_name_manual(source_image, cfg, mode, version)
    base = base_image(source_image, cfg, mode)

    write_2dfs_json([[p] for p in chunk_paths], SCRIPT_DIR)
    cmd = tdfs_cmd(cfg, SCRIPT_DIR) + [
        "build", "--platforms", "linux/amd64",
        *extra_flags(mode),
        "--force-http", "-f", "2dfs.json",
        base, target,
    ]
    log.info(f"Building {mode} manual v{version}: {target}")
    subprocess.run(
        cmd, check=True, cwd=SCRIPT_DIR,
        capture_output=not log.VERBOSE,
    )
    log.result(f"Built {target}")

    push_cmd = tdfs_cmd(cfg, SCRIPT_DIR) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")


# ── prepare ────────────────────────────────────────────────────────────


def prepare_manual_update(
    chunk_paths: list[str],
    source_image: str,
    cfg,
    mode: str,
    max_allowed_splits: int,
    artifacts_dir: str | None = None,
) -> None:
    """Build v0 + one image per pct in PARTITION_PERCENTS.

    Build sequence (cache cleared once at start):
      v0:    original chunks
      v{p1}: chunks [0..k1-1] bit-flipped, k_i = max(1, max_splits * p_i // 100)
      v{p2}: chunks [0..k2-1] bit-flipped (cumulative)
      ...
    Chunks are restored to original content after all builds.
    """
    clear_2dfs_cache(cfg)

    _build_version(chunk_paths, source_image, cfg, mode, 0)
    if artifacts_dir:
        snapshot_artifacts(SCRIPT_DIR, artifacts_dir)

    flipped: set[int] = set()
    prev_k = 0
    for pct in PARTITION_PERCENTS:
        k = max(1, max_allowed_splits * pct // 100)
        for i in range(prev_k, k):
            mutate_chunk(chunk_paths[i])
            flipped.add(i)
        prev_k = k
        _build_version(chunk_paths, source_image, cfg, mode, pct)

    log.info("Restoring chunk files...")
    for i in sorted(flipped):
        mutate_chunk(chunk_paths[i])
    log.result("Chunks restored.")


# ── measurement ────────────────────────────────────────────────────────


def measure_manual_update(
    source_image: str,
    cfg,
    max_allowed_splits: int,
) -> dict[str, list[tuple[int, int, float, float, float, float]]]:
    """results[mode] = list of (run, pct, stop_s, pull_s, run_s, file_access_s)"""
    results: dict[str, list[tuple[int, int, float, float, float, float]]] = {
        m: [] for m in MANUAL_UPDATE_MODES
    }

    for mode in MANUAL_UPDATE_MODES:
        for run in range(CFG.refresh_n_runs):
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                     f"=== {mode} run {run + 1}/{CFG.refresh_n_runs} ===")
            for pct in PARTITION_PERCENTS:
                k = max(1, max_allowed_splits * pct // 100)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                log.info(f"\n[{ts}] === {mode}: manual-update {pct}% ({k} chunk(s)) ===")

                clear_stargz_cache()

                v0_pull = _pull_name_manual(source_image, cfg, mode, 0, max_allowed_splits)
                v_new_pull = _pull_name_manual(source_image, cfg, mode, pct, max_allowed_splits)

                log.info(f"Pulling v0 (setup): {v0_pull}")
                subprocess.run(
                    ["sudo", "ctr-remote", "images", "rpull", "--plain-http", v0_pull],
                    check=True, capture_output=not log.VERBOSE,
                )

                name_old = _next_container_name(f"manual-{mode.replace('-', '')}-old")
                start_container(v0_pull, name_old)

                t0 = time.perf_counter()
                stop_container(name_old)
                stop_t = time.perf_counter() - t0
                log.result(f"  stop ({mode}): {stop_t:.2f}s")

                pull_t = timed_pull(
                    ["sudo", "ctr-remote", "images", "rpull", "--plain-http", v_new_pull]
                )
                log.result(f"  pull ({mode}): {pull_t:.2f}s")

                name_new = _next_container_name(f"manual-{mode.replace('-', '')}-new")
                t_run_start = time.perf_counter()
                start_container(v_new_pull, name_new)
                run_t = time.perf_counter() - t_run_start
                log.result(f"  run ({mode}): {run_t:.2f}s")

                file_access_t = exec_timed(name_new, k)
                log.result(f"  file access ({k} files, {mode}): {file_access_t:.2f}s")

                stop_container(name_new)
                log.info(f"\nSleeping {cfg.pull_cooldown}s before next...")
                time.sleep(cfg.pull_cooldown)

                results[mode].append((run, pct, stop_t, pull_t, run_t, file_access_t))

    return results


# ── output ─────────────────────────────────────────────────────────────


def print_results(
    results: dict[str, list[tuple[int, int, float, float, float, float]]],
) -> None:
    log.result(f"\n=== Manual-Update Baseline Results (mean ± stddev, n={CFG.refresh_n_runs} runs) ===")
    log.result(
        f"{'pct':>5}  {'mode':<32}  "
        f"{'stop':>10}  {'pull':>10}  {'run':>10}  {'file_acc':>10}  {'total':>10}"
    )
    log.result("-" * 95)
    for mode, entries in results.items():
        for pct in PARTITION_PERCENTS:
            group = [(s, p, r, f) for _, pp, s, p, r, f in entries if pp == pct]
            if not group:
                continue
            arr = np.array(group)
            tot = arr.sum(axis=1)
            log.result(
                f"{pct:>4}%  {mode:<32}  "
                f"{arr[:, 0].mean():>10.2f}  {arr[:, 1].mean():>10.2f}  "
                f"{arr[:, 2].mean():>10.2f}  {arr[:, 3].mean():>10.2f}  "
                f"{tot.mean():>10.2f}"
            )


def save_results_csv(
    results: dict[str, list[tuple[int, int, float, float, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    path = manual_update_csv_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    fieldnames = ["run", "partition_pct"]
    for mode in results:
        slug = mode.replace("-", "_")
        fieldnames += [
            f"{slug}_stop_s", f"{slug}_pull_s",
            f"{slug}_run_s", f"{slug}_file_access_s",
            f"{slug}_total_s",
        ]

    rows = []
    for run in range(CFG.refresh_n_runs):
        for pct in PARTITION_PERCENTS:
            row: dict = {"run": run, "partition_pct": pct}
            for mode, entries in results.items():
                slug = mode.replace("-", "_")
                match = [(s, p, r, f) for rr, pp, s, p, r, f in entries if rr == run and pp == pct]
                if match:
                    s, p, r, f = match[0]
                    row[f"{slug}_stop_s"] = f"{s:.4f}"
                    row[f"{slug}_pull_s"] = f"{p:.4f}"
                    row[f"{slug}_run_s"] = f"{r:.4f}"
                    row[f"{slug}_file_access_s"] = f"{f:.4f}"
                    row[f"{slug}_total_s"] = f"{s + p + r + f:.4f}"
                else:
                    row[f"{slug}_stop_s"] = ""
                    row[f"{slug}_pull_s"] = ""
                    row[f"{slug}_run_s"] = ""
                    row[f"{slug}_file_access_s"] = ""
                    row[f"{slug}_total_s"] = ""
            rows.append(row)

    for stat_name, stat_fn in (("mean", np.mean), ("std", lambda a: np.std(a, ddof=0))):
        for pct in PARTITION_PERCENTS:
            row = {"run": stat_name, "partition_pct": pct}
            for mode, entries in results.items():
                slug = mode.replace("-", "_")
                group = [(s, p, r, f) for _, pp, s, p, r, f in entries if pp == pct]
                if group:
                    arr = np.array(group)
                    tot = arr.sum(axis=1)
                    row[f"{slug}_stop_s"] = f"{float(stat_fn(arr[:, 0])):.4f}"
                    row[f"{slug}_pull_s"] = f"{float(stat_fn(arr[:, 1])):.4f}"
                    row[f"{slug}_run_s"] = f"{float(stat_fn(arr[:, 2])):.4f}"
                    row[f"{slug}_file_access_s"] = f"{float(stat_fn(arr[:, 3])):.4f}"
                    row[f"{slug}_total_s"] = f"{float(stat_fn(tot)):.4f}"
                else:
                    row[f"{slug}_stop_s"] = ""
                    row[f"{slug}_pull_s"] = ""
                    row[f"{slug}_run_s"] = ""
                    row[f"{slug}_file_access_s"] = ""
                    row[f"{slug}_total_s"] = ""
            rows.append(row)

    write_csv(path, fieldnames, rows)


# ── chart ──────────────────────────────────────────────────────────────


PHASE_LABELS = ("stop", "pull", "run", "file_access")
PHASE_COLORS = ("#7f7f7f", "#1f77b4", "#2ca02c", "#ff7f0e")


def plot(
    results: dict[str, list[tuple[int, int, float, float, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    mode = MANUAL_UPDATE_MODES[0]
    entries = results[mode]
    pcts = list(PARTITION_PERCENTS)

    fig, ax = plt.subplots(figsize=(10, 5.5))

    y_positions = list(range(len(pcts)))

    for y, pct in zip(y_positions, pcts):
        group = [(s, p, r, f) for _, pp, s, p, r, f in entries if pp == pct]
        if not group:
            continue
        arr = np.array(group)
        means = arr.mean(axis=0)
        cum_means = np.cumsum(means)
        cum_runs = np.cumsum(arr, axis=1)
        cum_stds = cum_runs.std(axis=0, ddof=0)

        left = 0.0
        for i in range(4):
            ax.barh(
                y, means[i], left=left, height=0.6,
                color=PHASE_COLORS[i], edgecolor=PHASE_COLORS[i], linewidth=0.5,
            )
            left += means[i]

        ax.errorbar(
            cum_means, [y] * 4, xerr=cum_stds,
            fmt="none", capsize=3, ecolor="black", elinewidth=1,
        )

    ax.set_yticks(y_positions)
    ax.set_yticklabels([f"{p}%" for p in pcts])
    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel("Partition size (%)")
    ax.set_title(
        f"manual-update baseline ({mode}, mean ± stddev, n={CFG.refresh_n_runs} runs)"
    )
    ax.invert_yaxis()
    ax.grid(True, linestyle="--", alpha=0.3, axis="x")

    handles = [
        mpatches.Patch(facecolor=c, edgecolor=c, label=lbl)
        for c, lbl in zip(PHASE_COLORS, PHASE_LABELS)
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=4,
        fontsize=9,
        frameon=False,
    )

    figure_footer(fig, model, base_image)

    output_path = manual_update_chart_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    save_figure(fig, output_path)


# ── main ───────────────────────────────────────────────────────────────


def manual_update_main(execution_ts: str) -> None:
    log.set_verbose(VERBOSE)
    log.info(f"Manual-update modes: {MANUAL_UPDATE_MODES}")
    log.info(f"Partition percents: {PARTITION_PERCENTS}")
    log.info(f"CFG.refresh_n_runs: {CFG.refresh_n_runs}")

    for model, base_image, max_allowed_splits in EXPERIMENTS:
        log.result(
            f"\n===== Manual-Update Experiment: {model} / {base_image} "
            f"(max_splits={max_allowed_splits}) ====="
        )

        chunk_paths = prepare_chunks(model, max_allowed_splits)

        prepare_local_registry(base_image, registry(CFG))

        clear_registry(CFG, preserve_base=True)
        for mode in MANUAL_UPDATE_MODES:
            log.info(f"\n=== Preparing mode: {mode} ===")
            artifacts_dir = manual_update_artifacts_dir(
                SCRIPT_DIR, execution_ts, model, base_image, build_mode(mode)
            )
            prepare_manual_update(
                chunk_paths, base_image, CFG, mode, max_allowed_splits, artifacts_dir
            )

        results = measure_manual_update(base_image, CFG, max_allowed_splits)

        clear_registry(CFG, preserve_base=True)

        print_results(results)
        save_results_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, execution_ts)
