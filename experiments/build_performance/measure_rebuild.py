import csv
import os
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt

from shared import log
from shared.registry import prepare_local_registry, registry, image_slug
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_base as bb
from build_performance import build_stargz as bs
from build_performance.prepare import prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(SCRIPT_DIR, "chunks")
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", "rebuild")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts", "rebuild")

MODEL = "openai-community/gpt2"
BASE_IMAGE = "docker.io/library/python:3.12-slim"
N_SPLITS = 10
IS_LOCAL = True
VERBOSE = False
SLEEP_SECONDS = 5
DIRECTIONS = ["top_to_bottom", "bottom_to_top"]
R_VALUES = [1]

METHODS = [
    ("2dfs", lambda n: b2.build_only(n, IS_LOCAL, BASE_IMAGE), lambda: b2.clear_cache(IS_LOCAL)),
    ("2dfs_stargz", lambda n: b2s.build_only(n, IS_LOCAL, BASE_IMAGE), lambda: b2s.clear_cache(IS_LOCAL)),
    ("stargz", lambda n: bs.build_only(n), lambda: bs.clear_cache()),
    ("base", lambda n: bb.build_only(n), lambda: bb.clear_cache()),
]


def mutate_chunk(path: str) -> None:
    with open(path, "r+b") as f:
        data = bytearray(f.read())
        for j in range(len(data)):
            data[j] ^= 0xFF
        f.seek(0)
        f.write(data)


def get_chunks_to_mutate(chunk_paths: list[str], r: int, direction: str) -> list[str]:
    if direction == "top_to_bottom":
        return chunk_paths[-r:]
    return chunk_paths[:r]


def measure_rebuilds(chunk_paths: list[str]) -> list[dict]:
    results = []

    for r in R_VALUES:
        for direction in DIRECTIONS:
            targets = get_chunks_to_mutate(chunk_paths, r, direction)

            for method_name, build_fn, clear_fn in METHODS:
                log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                     f"=== r={r}, {direction}, {method_name} ===")

                # clean slate: clear cache, build v1
                clear_fn()
                build_fn(N_SPLITS)

                # mutate r chunks
                for path in targets:
                    mutate_chunk(path)

                # rebuild v2 (timed)
                elapsed = build_fn(N_SPLITS)

                # restore chunks
                for path in targets:
                    mutate_chunk(path)

                results.append({
                    "r": r,
                    "direction": direction,
                    "method": method_name,
                    "rebuild_s": elapsed,
                })

                log.result(f"Rebuild time: {elapsed:.2f}s")
                time.sleep(SLEEP_SECONDS)

    return results



def save_csv(results: list[dict], model: str, n: int, base_image: str) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"{slug}_{img_slug}_rebuild_n{n}_{ts}.csv")
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["r", "direction", "method", "rebuild_s"])
        writer.writeheader()
        for row in results:
            writer.writerow({**row, "rebuild_s": f"{row['rebuild_s']:.4f}"})
    log.result(f"Results saved to {path}")


def plot(results: list[dict], model: str, n: int, base_image: str) -> None:
    os.makedirs(CHARTS_DIR, exist_ok=True)
    slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    colors = {"2dfs": "#1f77b4", "2dfs_stargz": "#ff7f0e", "stargz": "#2ca02c", "base": "#d62728"}
    labels = {"2dfs": "2dfs", "2dfs_stargz": "2dfs+stargz", "stargz": "stargz", "base": "base"}

    fig, (ax_ttb, ax_btt) = plt.subplots(1, 2, figsize=(14, 5), sharey=True)

    for ax, direction, title in [
        (ax_ttb, "top_to_bottom", "Top to Bottom"),
        (ax_btt, "bottom_to_top", "Bottom to Top"),
    ]:
        for method_name in ["2dfs", "2dfs_stargz", "stargz", "base"]:
            subset = [row for row in results if row["direction"] == direction and row["method"] == method_name]
            rs = [row["r"] for row in subset]
            times = [row["rebuild_s"] for row in subset]
            ax.plot(rs, times, marker="o", label=labels[method_name], color=colors[method_name])

        ax.set_xlabel("Chunks mutated (r)")
        ax.set_title(f"{title}")
        ax.set_xticks(R_VALUES)
        ax.legend()
        ax.grid(True, linestyle="--", alpha=0.5)

    ax_ttb.set_ylabel("Rebuild time (s)")
    fig.suptitle(f"Incremental rebuild performance (n={n})")
    fig.text(0.01, 0.01, f"model: {model}\nbase image: {base_image}",
             fontsize=8, verticalalignment="bottom", family="monospace")
    fig.tight_layout()

    path = os.path.join(CHARTS_DIR, f"{slug}_{img_slug}_rebuild_n{n}_{ts}.png")
    fig.savefig(path, dpi=150)
    log.result(f"Chart saved to {path}")


def main():
    log.set_verbose(VERBOSE)

    prepare_local_registry(BASE_IMAGE, registry(IS_LOCAL))

    log.info(f"Preparing model with {N_SPLITS} splits...")
    chunk_paths = prepare(MODEL, N_SPLITS, BASE_IMAGE, IS_LOCAL)

    results = measure_rebuilds(chunk_paths)

    save_csv(results, MODEL, N_SPLITS, BASE_IMAGE)
    plot(results, MODEL, N_SPLITS, BASE_IMAGE)

    log.result(f"\n{'r':>4}  {'direction':<16}  {'method':<14}  {'rebuild_s':>10}")
    log.result("-" * 50)
    for row in results:
        log.result(f"{row['r']:>4}  {row['direction']:<16}  {row['method']:<14}  {row['rebuild_s']:>10.2f}")


if __name__ == "__main__":
    main()
