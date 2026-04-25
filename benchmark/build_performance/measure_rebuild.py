import os
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import numpy as np

from shared import log
from shared.build_result import BuildResult
from build_performance.paths import rebuild_charts_run_dir, rebuild_csv_path, rebuild_chart_path
from shared.charts import MODE_COLORS, figure_footer, add_run_dots, bar_group_xticks, save_figure, write_csv
from shared.config import load_config
from shared.artifacts import mutate_chunk
from shared.registry import prepare_local_registry, registry, image_slug
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_2dfs_stargz_zstd as b2sz
from build_performance import build_base as bb
from build_performance import build_stargz as bs
from build_performance.prepare import prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5GB     ~50 MB
    ("facebook/opt-350m", "docker.io/tensorflow/tensorflow"),                # ~1.4 GB     ~700 MB
    ("Qwen/Qwen2-1.5B", "docker.io/ollama/ollama"),                      # ~3.09 GB     ~3.4 GB
    ("openlm-research/open_llama_3b", "docker.io/ollama/ollama"),    # ~6.0 GB     ~3.4 GB
]
CFG = load_config()
VERBOSE = True
DIRECTIONS = ["top_to_bottom", "bottom_to_top"]
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
# MODES = ["2dfs-stargz"]

def make_methods(base_image: str):
    all_methods = [
        ("2dfs",             lambda n, bi=base_image: b2.build_only(n, CFG, bi),   lambda: b2.clear_cache(CFG)),
        ("2dfs-stargz",      lambda n, bi=base_image: b2s.build_only(n, CFG, bi),  lambda: b2s.clear_cache(CFG)),
        ("2dfs-stargz-zstd", lambda n, bi=base_image: b2sz.build_only(n, CFG, bi), lambda: b2sz.clear_cache(CFG)),
        ("stargz",           lambda n: bs.build_only(n, CFG),                      lambda: bs.clear_cache()),
        ("base",             lambda n: bb.build_only(n, CFG),                      lambda: bb.clear_cache()),
    ]
    return [(name, bf, cf) for name, bf, cf in all_methods if name in MODES]


def get_chunks_to_mutate(chunk_paths: list[str], r: int, direction: str) -> list[str]:
    if direction == "top_to_bottom":
        return chunk_paths[-r:]
    return chunk_paths[:r]


def measure_rebuilds(chunk_paths: list[str], methods: list) -> list[dict]:
    results = []

    for run in range(CFG.rebuild_n_runs):
        log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Run {run + 1}/{CFG.rebuild_n_runs} ===")
        for r in CFG.rebuild_r_values:
            for direction in DIRECTIONS:
                targets = get_chunks_to_mutate(chunk_paths, r, direction)

                for method_name, build_fn, clear_fn in methods:
                    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                         f"=== r={r}, {direction}, {method_name} ===")

                    # clean slate: clear cache, build v1
                    t0 = time.time()
                    clear_fn()
                    log.info(f"Cache clear took {time.time() - t0:.2f}s")
                    build_fn(CFG.rebuild_n_splits)

                    # mutate r chunks
                    for path in targets:
                        mutate_chunk(path)

                    # rebuild v2 (timed)
                    br: BuildResult = build_fn(CFG.rebuild_n_splits)

                    # restore chunks
                    for path in targets:
                        mutate_chunk(path)

                    results.append({
                        "run": run,
                        "r": r,
                        "direction": direction,
                        "method": method_name,
                        "rebuild_s": br.total_s,
                        "pull_s": br.pull_s,
                        "context_transfer_s": br.context_transfer_s,
                        "build_s": br.build_s,
                        "export_s": br.export_s,
                    })

                    log.result(f"Rebuild time: {br.total_s:.2f}s")
                    log.info(f"\nSleeping {CFG.build_cooldown}s before next...")
                    time.sleep(CFG.build_cooldown)

    return results


def save_csv(results: list[dict], model: str, n: int, base_image: str, execution_ts: str) -> None:
    path = rebuild_csv_path(SCRIPT_DIR, model, base_image, n, execution_ts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = ["run", "r", "direction", "method", "rebuild_s",
                  "pull_s", "context_transfer_s", "build_s", "export_s"]
    rows = [{
        **row,
        "rebuild_s": f"{row['rebuild_s']:.4f}",
        "pull_s": f"{row['pull_s']:.4f}",
        "context_transfer_s": f"{row['context_transfer_s']:.4f}",
        "build_s": f"{row['build_s']:.4f}",
        "export_s": f"{row['export_s']:.4f}",
    } for row in results]
    write_csv(path, fieldnames, rows)


def plot(results: list[dict], model: str, n: int, base_image: str, execution_ts: str) -> None:
    os.makedirs(rebuild_charts_run_dir(SCRIPT_DIR, execution_ts), exist_ok=True)

    n_modes = len(MODES)
    bar_width = 0.8 / n_modes

    fig, (ax_ttb, ax_btt) = plt.subplots(1, 2, figsize=(14, 5), sharey=True)

    for ax, direction, title in [
        (ax_ttb, "top_to_bottom", "Top to Bottom"),
        (ax_btt, "bottom_to_top", "Bottom to Top"),
    ]:
        for i, mode in enumerate(MODES):
            for j, r in enumerate(CFG.rebuild_r_values):
                group = [
                    row for row in results
                    if row["direction"] == direction and row["method"] == mode and row["r"] == r
                ]
                rebuild_vals = [row["rebuild_s"] - row["pull_s"] for row in group]
                if not rebuild_vals:
                    continue

                x = j + i * bar_width
                x_center = x + bar_width / 2
                median_val = float(np.median(rebuild_vals))
                label = mode if j == 0 else None
                ax.bar(x, median_val, bar_width, color=MODE_COLORS[mode], label=label,
                       edgecolor="black", linewidth=0.5)

                add_run_dots(ax, x_center, rebuild_vals)

        bar_group_xticks(ax, len(CFG.rebuild_r_values), n_modes, bar_width, [str(r) for r in CFG.rebuild_r_values])
        ax.set_xlabel("Chunks mutated (r)")
        ax.set_title(f"{title}")
        ax.legend(fontsize="small")
        ax.grid(True, linestyle="--", alpha=0.5, axis="y")

    ax_ttb.set_ylabel("Rebuild time (s)")
    fig.suptitle(f"Incremental rebuild performance (n={n}, median, {CFG.rebuild_n_runs} runs, dots = individual runs)")
    figure_footer(fig, model, base_image)
    fig.tight_layout()

    path = rebuild_chart_path(SCRIPT_DIR, model, base_image, n, execution_ts)
    save_figure(fig, path)


def main():
    log.set_verbose(VERBOSE)
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for model, base_image in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} =====")
        prepare_local_registry(base_image, registry(CFG))

        methods = make_methods(base_image)

        log.info(f"Preparing model with {CFG.rebuild_n_splits} splits...")
        chunk_paths = prepare(model, CFG.rebuild_n_splits, base_image, CFG)

        results = measure_rebuilds(chunk_paths, methods)

        save_csv(results, model, CFG.rebuild_n_splits, base_image, execution_ts)
        plot(results, model, CFG.rebuild_n_splits, base_image, execution_ts)

        log.result(f"\n{'run':>4}  {'r':>4}  {'direction':<16}  {'method':<14}  {'rebuild':>8}  {'pull':>6}  {'ctx':>6}  {'build':>6}  {'export':>6}")
        log.result("-" * 84)
        for row in results:
            log.result(f"{row['run']:>4}  {row['r']:>4}  {row['direction']:<16}  {row['method']:<14}  "
                       f"{row['rebuild_s']:>8.2f}  {row['pull_s']:>6.2f}  {row['context_transfer_s']:>6.2f}  "
                       f"{row['build_s']:>6.2f}  {row['export_s']:>6.2f}")


if __name__ == "__main__":
    main()
