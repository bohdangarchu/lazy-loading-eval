import os
import re
import subprocess
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from shared import log
from shared.charts import MODE_COLORS, figure_footer, save_figure, write_csv
from pull_performance.paths import refresh_csv_path, refresh_chart_path, refresh_artifacts_dir
from shared.config import load_config
from shared.registry import (
    prepare_local_registry, clear_registry, registry, image_slug,
    tdfs_cmd,
)
from shared.artifacts import write_2dfs_json, mutate_chunk, snapshot_artifacts, clear_artifacts
from shared.services import ensure_buildkit, clear_2dfs_cache, clear_stargz_cache
from pull_performance.prepare import prepare_chunks
from pull_performance.measure import _next_container_name
from pull_performance.refresh_common import (
    EXPERIMENTS,
    build_mode, extra_flags, base_image,
    timed_pull, start_container, exec_timed, stop_container,
)
from pull_performance.measure_manual_update import manual_update_main

CFG = load_config()
VERBOSE = True
RUN_MANUAL_UPDATE_BASELINE = True
MODES = [
    "2dfs-stargz-with-bg-fetch",
    "2dfs-stargz-zstd-with-bg-fetch",
    "2dfs-stargz",
    "2dfs-stargz-zstd",
]
PARTITION_PERCENTS = [25, 50, 75, 100]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

_ALLOTMENT_RE = re.compile(r"Stargz Allotment 0/(\d+) ([a-f0-9]{64})")


# ── image naming ───────────────────────────────────────────────────────


def _build_name_refresh(source_image: str, cfg, mode: str, version_idx: int) -> str:
    return f"{registry(cfg)}/{image_slug(source_image)}-{mode}-refresh:v{version_idx}"


def _pull_name_refresh(source_image: str, cfg, mode: str, version_idx: int, max_allowed_splits: int) -> str:
    end_col = max_allowed_splits - 1
    return f"{registry(cfg)}/library/{image_slug(source_image)}-{mode}-refresh:v{version_idx}--0.0.0.{end_col}"


# ── digest parsing ─────────────────────────────────────────────────────


def _parse_allotment_digests(output: str) -> dict[int, str]:
    """Parse {col: digest} from tdfs build output."""
    return {int(m.group(1)): m.group(2) for m in _ALLOTMENT_RE.finditer(output)}


# ── build helpers ──────────────────────────────────────────────────────


def _is_bg_fetch_mode(mode: str) -> bool:
    return mode.endswith("-with-bg-fetch")


def _build_version(
    chunk_paths: list[str],
    source_image: str,
    cfg,
    mode: str,
    version_idx: int,
) -> dict[int, str]:
    """Build one refresh image version and return its allotment digest map."""
    target = _build_name_refresh(source_image, cfg, mode, version_idx)
    base = base_image(source_image, cfg, mode)

    write_2dfs_json([[p] for p in chunk_paths], SCRIPT_DIR)
    cmd = tdfs_cmd(cfg, SCRIPT_DIR) + [
        "build", "--platforms", "linux/amd64",
        *extra_flags(mode),
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
    max_allowed_splits: int,
    artifacts_dir: str | None = None,
) -> list[dict[int, str]]:
    """Build max_allowed_splits+1 image versions, return all_digests[version_idx][col].

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
    if artifacts_dir:
        snapshot_artifacts(SCRIPT_DIR, artifacts_dir)
    all_digests.append(parsed)

    # v1..vN: cumulative mutations
    for k in range(1, max_allowed_splits + 1):
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
    max_allowed_splits: int,
) -> dict[str, list[tuple[int, int, float, float, float]]]:
    """results[mode] = list of (run, pct, pull_t, layer_refresh_t, file_access_t)"""
    results: dict[str, list[tuple[int, int, float, float, float]]] = {m: [] for m in MODES}

    for mode in MODES:
        all_digests = all_digests_per_mode[mode]

        for run in range(CFG.refresh_n_runs):
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                     f"=== {mode} run {run + 1}/{CFG.refresh_n_runs} ===")
            for pct in PARTITION_PERCENTS:
                k = max(1, max_allowed_splits * pct // 100)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                log.info(f"\n[{ts}] === {mode}: refresh {pct}% ({k} layer(s)) ===")

                clear_stargz_cache()

                v0_pull = _pull_name_refresh(source_image, cfg, mode, 0, max_allowed_splits)
                log.info(f"Pulling v0: {v0_pull}")
                pull_t = timed_pull(
                    ["sudo", "ctr-remote", "images", "rpull", "--plain-http", v0_pull]
                )
                log.result(f"  pull ({mode}): {pull_t:.2f}s")

                name = _next_container_name(f"refresh-{mode.replace('-', '')}")
                start_container(v0_pull, name)

                with_bg_fetch = _is_bg_fetch_mode(mode)
                t0 = time.perf_counter()
                for i in range(k):
                    old_digest = all_digests[0][i]
                    new_digest = all_digests[i + 1][i]
                    _refresh_layer(old_digest, new_digest, with_bg_fetch=with_bg_fetch)
                layer_refresh_t = time.perf_counter() - t0
                log.result(f"  refresh-layer ({k} layers, {mode}): {layer_refresh_t:.2f}s")

                file_access_t = exec_timed(name, k)
                log.result(f"  post-refresh file access ({k} refreshed files, {mode}): {file_access_t:.2f}s")

                stop_container(name)
                log.info(f"\nSleeping {cfg.pull_cooldown}s before next...")
                time.sleep(cfg.pull_cooldown)

                results[mode].append((run, pct, pull_t, layer_refresh_t, file_access_t))

    return results


# ── output ─────────────────────────────────────────────────────────────


def print_results(results: dict[str, list[tuple[int, int, float, float, float]]]) -> None:
    log.result(f"\n=== Refresh Performance Results (mean ± stddev, n={CFG.refresh_n_runs} runs) ===")
    log.result(
        f"{'pct':>5}  {'mode':<32}  "
        f"{'layer_refresh_mean':>18}  {'layer_refresh_std':>18}  "
        f"{'file_access_mean':>18}  {'file_access_std':>18}"
    )
    log.result("-" * 117)
    for mode, entries in results.items():
        for pct in PARTITION_PERCENTS:
            group = [(lr_t, fa_t)
                     for _, pp, _, lr_t, fa_t in entries if pp == pct]
            if not group:
                continue
            lr_arr = np.array([g[0] for g in group])
            fa_arr = np.array([g[1] for g in group])
            log.result(
                f"{pct:>4}%  {mode:<32}  "
                f"{lr_arr.mean():>18.2f}  {lr_arr.std(ddof=0):>18.2f}  "
                f"{fa_arr.mean():>18.2f}  {fa_arr.std(ddof=0):>18.2f}"
            )


def save_results_csv(
    results: dict[str, list[tuple[int, int, float, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    path = refresh_csv_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    fieldnames = ["run", "partition_pct"]
    for mode in results:
        slug = mode.replace("-", "_")
        fieldnames += [
            f"{slug}_pull_s", f"{slug}_layer_refresh_s",
            f"{slug}_file_access_s", f"{slug}_total_s",
        ]

    rows = []
    for run in range(CFG.refresh_n_runs):
        for pct in PARTITION_PERCENTS:
            row: dict = {"run": run, "partition_pct": pct}
            for mode, entries in results.items():
                slug = mode.replace("-", "_")
                match = [(p, lr, fa) for rr, pp, p, lr, fa in entries if rr == run and pp == pct]
                if match:
                    p, lr, fa = match[0]
                    row[f"{slug}_pull_s"] = f"{p:.4f}"
                    row[f"{slug}_layer_refresh_s"] = f"{lr:.4f}"
                    row[f"{slug}_file_access_s"] = f"{fa:.4f}"
                    row[f"{slug}_total_s"] = f"{lr + fa:.4f}"
                else:
                    row[f"{slug}_pull_s"] = ""
                    row[f"{slug}_layer_refresh_s"] = ""
                    row[f"{slug}_file_access_s"] = ""
                    row[f"{slug}_total_s"] = ""
            rows.append(row)

    # Summary rows: mean and stddev across runs, per (mode, pct).
    # std on each component is per-component (no sums); std on total is the
    # std of (layer_refresh + file_access) sums, used by the chart's upper whisker.
    for stat_name, stat_fn in (("mean", np.mean), ("std", lambda a: np.std(a, ddof=0))):
        for pct in PARTITION_PERCENTS:
            row = {"run": stat_name, "partition_pct": pct}
            for mode, entries in results.items():
                slug = mode.replace("-", "_")
                group = [(p, lr, fa) for _, pp, p, lr, fa in entries if pp == pct]
                if group:
                    p_arr = np.array([g[0] for g in group])
                    lr_arr = np.array([g[1] for g in group])
                    fa_arr = np.array([g[2] for g in group])
                    tot_arr = lr_arr + fa_arr
                    row[f"{slug}_pull_s"] = f"{float(stat_fn(p_arr)):.4f}"
                    row[f"{slug}_layer_refresh_s"] = f"{float(stat_fn(lr_arr)):.4f}"
                    row[f"{slug}_file_access_s"] = f"{float(stat_fn(fa_arr)):.4f}"
                    row[f"{slug}_total_s"] = f"{float(stat_fn(tot_arr)):.4f}"
                else:
                    row[f"{slug}_pull_s"] = ""
                    row[f"{slug}_layer_refresh_s"] = ""
                    row[f"{slug}_file_access_s"] = ""
                    row[f"{slug}_total_s"] = ""
            rows.append(row)

    write_csv(path, fieldnames, rows)


def plot(
    results: dict[str, list[tuple[int, int, float, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    pcts = list(PARTITION_PERCENTS)
    x = np.arange(len(pcts))
    n_modes = len(results)
    width = min(0.8 / n_modes, 0.2)

    fig, ax = plt.subplots(figsize=(10, 6.5))

    for i, (mode, entries) in enumerate(results.items()):
        color = MODE_COLORS[mode]
        offset = (i - (n_modes - 1) / 2) * width

        mean_lr = []
        std_lr = []
        mean_fa = []
        std_total = []
        for pct in pcts:
            lr_group = [lr for _, pp, _, lr, _ in entries if pp == pct]
            fa_group = [fa for _, pp, _, _, fa in entries if pp == pct]
            if lr_group:
                lr_arr = np.array(lr_group)
                fa_arr = np.array(fa_group)
                tot_arr = lr_arr + fa_arr
                mean_lr.append(float(lr_arr.mean()))
                std_lr.append(float(lr_arr.std(ddof=0)))
                mean_fa.append(float(fa_arr.mean()))
                std_total.append(float(tot_arr.std(ddof=0)))
            else:
                mean_lr.append(0.0)
                std_lr.append(0.0)
                mean_fa.append(0.0)
                std_total.append(0.0)

        ax.bar(x + offset, mean_lr, width, yerr=std_lr, capsize=3,
               color=color, edgecolor=color, linewidth=0.5, alpha=1.0,
               label=f"{mode} (layer refresh)")
        ax.bar(x + offset, mean_fa, width, bottom=mean_lr, yerr=std_total, capsize=3,
               color=color, edgecolor=color, linewidth=0.5, alpha=0.45,
               label=f"{mode} (file access)")

    ax.set_xlabel("Partition size (%)")
    ax.set_ylabel("Access time (s)")
    ax.set_title(f"refresh-layer time (mean ± stddev, n={CFG.refresh_n_runs} runs)")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{p}%" for p in pcts])
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
    clear_artifacts(SCRIPT_DIR)
    ensure_buildkit()
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info(f"Modes: {MODES}")
    log.info(f"Partition percents: {PARTITION_PERCENTS}")
    log.info(f"CFG.refresh_n_runs: {CFG.refresh_n_runs}")

    for model, base_image, max_allowed_splits in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} (max_splits={max_allowed_splits}) =====")

        chunk_paths = prepare_chunks(model, max_allowed_splits)

        prepare_local_registry(base_image, registry(CFG))

        all_digests_per_mode: dict[str, list[dict[int, str]]] = {}

        clear_registry(CFG, preserve_base=True)
        for mode in MODES:
            log.info(f"\n=== Preparing mode: {mode} ===")
            artifacts_dir = refresh_artifacts_dir(SCRIPT_DIR, execution_ts, model, base_image, build_mode(mode))
            all_digests_per_mode[mode] = prepare_refresh(chunk_paths, base_image, CFG, mode, max_allowed_splits, artifacts_dir)

        results = measure_refresh(base_image, CFG, all_digests_per_mode, max_allowed_splits)

        clear_registry(CFG, preserve_base=True)

        print_results(results)
        save_results_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, execution_ts)

    if RUN_MANUAL_UPDATE_BASELINE:
        manual_update_main(execution_ts)

    clear_artifacts(SCRIPT_DIR)


if __name__ == "__main__":
    main()
