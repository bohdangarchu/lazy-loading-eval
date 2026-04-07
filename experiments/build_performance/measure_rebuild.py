import os
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import numpy as np

from shared import log
from shared.build_result import BuildResult
from shared.charts import MODE_COLORS, figure_footer, add_run_dots, bar_group_xticks, save_figure, write_csv
from shared.config import load_config
from shared.registry import prepare_local_registry, registry, image_slug
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_2dfs_stargz_zstd as b2sz
from build_performance import build_base as bb
from build_performance import build_stargz as bs
from build_performance.prepare import prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(SCRIPT_DIR, "chunks")
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", "rebuild")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts", "rebuild")

EXPERIMENTS = [
    ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5GB     ~50 MB
    # ("facebook/opt-350m", "docker.io/tensorflow/tensorflow"),                # ~1.4 GB     ~700 MB
    # ("facebook/opt-1.3b", "docker.io/ollama/ollama"),                      # ~3.25 GB     ~3.4 GB
    # ("openai-community/gpt2-xl", "docker.io/library/python:3.12-slim"),    # ~6.0 GB     ~50 MB
]
N_SPLITS = 10
N_RUNS = 5
CFG = load_config()
VERBOSE = True
SLEEP_SECONDS = 5
DIRECTIONS = ["top_to_bottom", "bottom_to_top"]
R_VALUES = [2, 4, 6, 8, 10]
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


def mutate_chunk(path: str) -> None:
    with open(path, "r+b") as f:
        data = np.fromfile(f, dtype=np.uint8)
        np.bitwise_not(data, out=data)
        f.seek(0)
        data.tofile(f)


def get_chunks_to_mutate(chunk_paths: list[str], r: int, direction: str) -> list[str]:
    if direction == "top_to_bottom":
        return chunk_paths[-r:]
    return chunk_paths[:r]


def measure_rebuilds(chunk_paths: list[str], methods: list) -> list[dict]:
    results = []

    for run in range(N_RUNS):
        log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Run {run + 1}/{N_RUNS} ===")
        for r in R_VALUES:
            for direction in DIRECTIONS:
                targets = get_chunks_to_mutate(chunk_paths, r, direction)

                for method_name, build_fn, clear_fn in methods:
                    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                         f"=== r={r}, {direction}, {method_name} ===")

                    # clean slate: clear cache, build v1
                    t0 = time.time()
                    clear_fn()
                    log.info(f"Cache clear took {time.time() - t0:.2f}s")
                    build_fn(N_SPLITS)

                    # mutate r chunks
                    for path in targets:
                        mutate_chunk(path)

                    # rebuild v2 (timed)
                    br: BuildResult = build_fn(N_SPLITS)

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
                    time.sleep(SLEEP_SECONDS)

    return results


def save_csv(results: list[dict], model: str, n: int, base_image: str) -> None:
    slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"{slug}_{img_slug}_rebuild_n{n}_{ts}.csv")
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


def plot(results: list[dict], model: str, n: int, base_image: str) -> None:
    os.makedirs(CHARTS_DIR, exist_ok=True)
    slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    n_modes = len(MODES)
    bar_width = 0.8 / n_modes

    fig, (ax_ttb, ax_btt) = plt.subplots(1, 2, figsize=(14, 5), sharey=True)

    for ax, direction, title in [
        (ax_ttb, "top_to_bottom", "Top to Bottom"),
        (ax_btt, "bottom_to_top", "Bottom to Top"),
    ]:
        for i, mode in enumerate(MODES):
            for j, r in enumerate(R_VALUES):
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

        bar_group_xticks(ax, len(R_VALUES), n_modes, bar_width, [str(r) for r in R_VALUES])
        ax.set_xlabel("Chunks mutated (r)")
        ax.set_title(f"{title}")
        ax.legend(fontsize="small")
        ax.grid(True, linestyle="--", alpha=0.5, axis="y")

    ax_ttb.set_ylabel("Rebuild time (s)")
    fig.suptitle(f"Incremental rebuild performance (n={n}, median, {N_RUNS} runs, dots = individual runs)")
    figure_footer(fig, model, base_image)
    fig.tight_layout()

    path = os.path.join(CHARTS_DIR, f"{slug}_{img_slug}_rebuild_n{n}_{ts}.png")
    save_figure(fig, path)


def main():
    log.set_verbose(VERBOSE)

    for model, base_image in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} =====")
        prepare_local_registry(base_image, registry(CFG))

        methods = make_methods(base_image)

        log.info(f"Preparing model with {N_SPLITS} splits...")
        chunk_paths = prepare(model, N_SPLITS, base_image, CFG)

        results = measure_rebuilds(chunk_paths, methods)

        save_csv(results, model, N_SPLITS, base_image)
        plot(results, model, N_SPLITS, base_image)

        log.result(f"\n{'run':>4}  {'r':>4}  {'direction':<16}  {'method':<14}  {'rebuild':>8}  {'pull':>6}  {'ctx':>6}  {'build':>6}  {'export':>6}")
        log.result("-" * 84)
        for row in results:
            log.result(f"{row['run']:>4}  {row['r']:>4}  {row['direction']:<16}  {row['method']:<14}  "
                       f"{row['rebuild_s']:>8.2f}  {row['pull_s']:>6.2f}  {row['context_transfer_s']:>6.2f}  "
                       f"{row['build_s']:>6.2f}  {row['export_s']:>6.2f}")


if __name__ == "__main__":
    main()
