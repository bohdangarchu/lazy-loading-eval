import os
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import numpy as np

from shared import log
from shared.build_result import BuildResult
from build_performance.paths import rebuild_charts_run_dir, rebuild_csv_path, rebuild_chart_path, rebuild_artifacts_dir
from shared.charts import MODE_COLORS, figure_footer, save_figure, write_csv
from shared.config import load_config
from shared.artifacts import mutate_chunk, snapshot_artifacts, clear_artifacts
from shared.registry import prepare_local_registry, registry, image_slug
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_2dfs_stargz_zstd as b2sz
from build_performance import build_base as bb
from build_performance import build_stargz as bs
from build_performance.prepare import prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    ("openai-community/gpt2",        "docker.io/library/python:3.12-slim", 12),  # ~0.5GB     ~50 MB
    ("Qwen/Qwen2-1.5B",              "docker.io/library/python:3.12-slim",            12),  # ~3.09 GB     ~3.4 GB
    # ("openlm-research/open_llama_3b", "docker.io/ollama/ollama",           12),  # ~6.0 GB     ~3.4 GB
]
CFG = load_config()
VERBOSE = True
DIRECTIONS = ["top_to_bottom", "bottom_to_top"]
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
MUTATION_PERCENTS = [25, 50, 75, 100]


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


def measure_rebuilds(chunk_paths: list[str], methods: list, max_allowed_splits: int) -> list[dict]:
    results = []

    for run in range(CFG.rebuild_n_runs):
        log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Run {run + 1}/{CFG.rebuild_n_runs} ===")
        for pct in MUTATION_PERCENTS:
            r = max(1, max_allowed_splits * pct // 100)
            for direction in DIRECTIONS:
                targets = get_chunks_to_mutate(chunk_paths, r, direction)

                for method_name, build_fn, clear_fn in methods:
                    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                         f"=== mutation={pct}% (r={r}), {direction}, {method_name} ===")

                    t0 = time.time()
                    clear_fn()
                    log.info(f"Cache clear took {time.time() - t0:.2f}s")
                    build_fn(max_allowed_splits)

                    for path in targets:
                        mutate_chunk(path)

                    br: BuildResult = build_fn(max_allowed_splits)

                    for path in targets:
                        mutate_chunk(path)

                    results.append({
                        "run": run,
                        "mutation_pct": pct,
                        "r": r,
                        "direction": direction,
                        "method": method_name,
                        "total_s": br.total_s,
                    })

                    log.result(f"Total time: {br.total_s:.2f}s")
                    log.info(f"\nSleeping {CFG.build_cooldown}s before next...")
                    time.sleep(CFG.build_cooldown)

    return results


def save_csv(results: list[dict], model: str, base_image: str, execution_ts: str) -> None:
    path = rebuild_csv_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = ["run", "mutation_pct", "r", "direction", "method", "total_s"]
    rows = [{
        **row,
        "total_s": f"{row['total_s']:.4f}",
    } for row in results]
    write_csv(path, fieldnames, rows)


def plot(results: list[dict], model: str, base_image: str, max_allowed_splits: int, execution_ts: str) -> None:
    os.makedirs(rebuild_charts_run_dir(SCRIPT_DIR, execution_ts), exist_ok=True)

    fig, (ax_ttb, ax_btt) = plt.subplots(1, 2, figsize=(14, 5), sharey=True)

    for ax, direction, title in [
        (ax_ttb, "top_to_bottom", "Top to Bottom"),
        (ax_btt, "bottom_to_top", "Bottom to Top"),
    ]:
        for mode in MODES:
            means = []
            stds = []
            for pct in MUTATION_PERCENTS:
                vals = [
                    row["total_s"] for row in results
                    if row["direction"] == direction and row["method"] == mode and row["mutation_pct"] == pct
                ]
                means.append(float(np.mean(vals)) if vals else float("nan"))
                stds.append(float(np.std(vals, ddof=0)) if vals else 0.0)
            ax.errorbar(MUTATION_PERCENTS, means, yerr=stds, label=mode, color=MODE_COLORS[mode],
                        marker="o", capsize=3, linewidth=1.5)

        ax.set_xticks(MUTATION_PERCENTS)
        ax.set_xlabel("% of splits updated")
        ax.set_title(f"{title}")
        ax.legend(fontsize="small")
        ax.grid(True, linestyle="--", alpha=0.5)

    ax_ttb.set_ylabel("Total rebuild time (s)")
    fig.suptitle(f"Incremental rebuild performance (mean ± std, n={CFG.rebuild_n_runs} runs)")
    fig.tight_layout()
    figure_footer(fig, model, base_image, max_allowed_splits=max_allowed_splits)

    path = rebuild_chart_path(SCRIPT_DIR, model, base_image, execution_ts)
    save_figure(fig, path)


def main():
    log.set_verbose(VERBOSE)
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for model, base_image, max_allowed_splits in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} (max_allowed_splits={max_allowed_splits}) =====")
        prepare_local_registry(base_image, registry(CFG))

        methods = make_methods(base_image)

        log.info(f"Preparing model at full capacity ({max_allowed_splits} layers)...")
        chunk_paths = prepare(model, max_allowed_splits, max_allowed_splits, base_image, CFG)
        snapshot_artifacts(
            SCRIPT_DIR,
            rebuild_artifacts_dir(SCRIPT_DIR, execution_ts, model, base_image),
        )

        results = measure_rebuilds(chunk_paths, methods, max_allowed_splits)

        save_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, max_allowed_splits, execution_ts)

        log.result(f"\n{'run':>4}  {'pct':>4}  {'r':>4}  {'direction':<16}  {'method':<14}  {'total':>8}")
        log.result("-" * 60)
        for row in results:
            log.result(f"{row['run']:>4}  {row['mutation_pct']:>3}%  {row['r']:>4}  {row['direction']:<16}  {row['method']:<14}  "
                       f"{row['total_s']:>8.2f}")

    clear_artifacts(SCRIPT_DIR)


if __name__ == "__main__":
    main()
