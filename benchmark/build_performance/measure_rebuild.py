import os
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import numpy as np

from shared import log
from shared.build_result import BuildResult
from build_performance.paths import rebuild_charts_run_dir, rebuild_csv_path, rebuild_chart_path, rebuild_artifacts_dir, rebuild_merged_csv_path
from shared.charts import MODE_COLORS, figure_footer, save_figure, write_csv
from shared.config import load_config
from shared.artifacts import mutate_safetensors, snapshot_artifacts, clear_artifacts
from shared.registry import prepare_local_registry, registry, image_slug
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_2dfs_stargz_zstd as b2sz
from build_performance import build_base as bb
from build_performance import build_stargz as bs
from build_performance.prepare import generate_build_artifacts, prepare_model_splits, print_packing_table

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    # ("openai-community/gpt2",        "docker.io/library/python:3.12-slim"),  # ~0.5GB     ~50 MB
    # ("Qwen/Qwen2-1.5B",              "docker.io/library/python:3.12-slim"),  # ~3.09 GB     ~3.4 GB
    # ("EleutherAI/pythia-1.4b", "docker.io/library/python:3.12-slim"),
    ("openlm-research/open_llama_3b", "docker.io/library/python:3.12-slim")    # ~6.0 GB     ~3.4 GB
]
CFG = load_config()
VERBOSE = False
DIRECTIONS = ["top_to_bottom", "bottom_to_top"]
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
LAYERS_MUTATED_PERCENTS = [25, 50, 75, 100]
# v2: 
# - n_chunks_mutated -> n_layers_mutated
# - mutation_pct -> layers_mutated_pct
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class RebuildRow:
    schema_version: int
    model: str
    base_image: str
    max_allowed_splits: int
    run: int
    layers_mutated_pct: int
    n_layers_mutated: int
    direction: str
    mode: str
    total_s: float


def make_methods(base_image: str):
    all_methods = [
        ("2dfs",             lambda n, bi=base_image: b2.build_only(n, CFG, bi),   lambda: b2.clear_cache(CFG)),
        ("2dfs-stargz",      lambda n, bi=base_image: b2s.build_only(n, CFG, bi),  lambda: b2s.clear_cache(CFG)),
        ("2dfs-stargz-zstd", lambda n, bi=base_image: b2sz.build_only(n, CFG, bi), lambda: b2sz.clear_cache(CFG)),
        ("stargz",           lambda n: bs.build_only(n, CFG),                      lambda: bs.clear_cache()),
        ("base",             lambda n: bb.build_only(n, CFG),                      lambda: bb.clear_cache()),
    ]
    return [(name, bf, cf) for name, bf, cf in all_methods if name in MODES]


def get_buckets_to_mutate(
    groups: list[list[str]], n_buckets: int, direction: str,
) -> list[list[str]]:
    """Pick which buckets (allotments) to mutate.

    groups[0] is the bottommost image layer (first COPY in Dockerfile);
    groups[-1] is the topmost. Direction:
      - top_to_bottom: groups[-n:]  — mutate the top n layers
      - bottom_to_top: groups[:n]   — mutate the bottom n layers
    """
    if direction == "top_to_bottom":
        return groups[-n_buckets:]
    return groups[:n_buckets]


def print_mutation_table(groups: list[list[str]]) -> None:
    """Print mutated MB per (layers_mutated_pct, direction)."""
    def mutated_mb(buckets: list[list[str]]) -> float:
        return sum(
            os.path.getsize(p)
            for b in buckets for p in b if p.endswith(".safetensors")
        ) / (1024 ** 2)

    log.result(f"\n=== Mutation preview: {len(groups)} allotment(s) ===")
    log.result(f"{'pct':>5}  {'n_buckets':>9}  {'top_to_bottom (MB)':>20}  {'bottom_to_top (MB)':>20}")
    log.result("-" * 62)
    for pct in LAYERS_MUTATED_PERCENTS:
        n = max(1, len(groups) * pct // 100)
        ttb = mutated_mb(get_buckets_to_mutate(groups, n, "top_to_bottom"))
        btt = mutated_mb(get_buckets_to_mutate(groups, n, "bottom_to_top"))
        log.result(f"{pct:>4}%  {n:>9}  {ttb:>20.1f}  {btt:>20.1f}")


def _mutate_buckets(buckets: list[list[str]]) -> list[str]:
    """Mutate every .safetensors file in the picked buckets. Returns the
    flat list of mutated paths so the caller can reverse with a second call."""
    mutated: list[str] = []
    for bucket in buckets:
        for path in bucket:
            if path.endswith(".safetensors"):
                mutate_safetensors(path)
                mutated.append(path)
    return mutated


def measure_rebuilds(
    groups: list[list[str]], methods: list, model: str, base_image: str, max_allowed_splits: int,
) -> list[RebuildRow]:
    results: list[RebuildRow] = []

    for run in range(CFG.rebuild_n_runs):
        log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Run {run + 1}/{CFG.rebuild_n_runs} ===")
        for pct in LAYERS_MUTATED_PERCENTS:
            n_layers_mutated = max(1, len(groups) * pct // 100)
            for direction in DIRECTIONS:
                target_buckets = get_buckets_to_mutate(groups, n_layers_mutated, direction)

                for mode_name, build_fn, clear_fn in methods:
                    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                         f"=== mutation={pct}% (n_layers_mutated={n_layers_mutated}/{len(groups)}), {direction}, {mode_name} ===")

                    t0 = time.time()
                    clear_fn()
                    log.info(f"Cache clear took {time.time() - t0:.2f}s")
                    build_fn(max_allowed_splits)

                    mutated = _mutate_buckets(target_buckets)
                    try:
                        br: BuildResult = build_fn(max_allowed_splits)
                    finally:
                        for path in mutated:
                            mutate_safetensors(path)

                    results.append(RebuildRow(
                        schema_version=SCHEMA_VERSION,
                        model=model,
                        base_image=base_image,
                        max_allowed_splits=max_allowed_splits,
                        run=run,
                        layers_mutated_pct=pct,
                        n_layers_mutated=n_layers_mutated,
                        direction=direction,
                        mode=mode_name,
                        total_s=br.total_s,
                    ))

                    log.result(f"Total time: {br.total_s:.2f}s")
                    log.info(f"\nSleeping {CFG.build_cooldown}s before next...")
                    time.sleep(CFG.build_cooldown)

    return results


def _write_rebuild_rows(output_path: str, results: list[RebuildRow]) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = [f.name for f in fields(RebuildRow)]
    rows = [{**asdict(r), "total_s": f"{r.total_s:.4f}"} for r in results]
    write_csv(output_path, fieldnames, rows)


def save_csv(results: list[RebuildRow], model: str, base_image: str, execution_ts: str) -> None:
    _write_rebuild_rows(rebuild_csv_path(SCRIPT_DIR, model, base_image, execution_ts), results)


def save_merged_csv(results: list[RebuildRow], execution_ts: str) -> None:
    _write_rebuild_rows(rebuild_merged_csv_path(SCRIPT_DIR, execution_ts), results)


def plot(results: list[RebuildRow], model: str, base_image: str, max_allowed_splits: int, execution_ts: str) -> None:
    os.makedirs(rebuild_charts_run_dir(SCRIPT_DIR, execution_ts), exist_ok=True)

    fig, (ax_ttb, ax_btt) = plt.subplots(1, 2, figsize=(14, 5), sharey=True)

    for ax, direction, title in [
        (ax_ttb, "top_to_bottom", "Top to Bottom"),
        (ax_btt, "bottom_to_top", "Bottom to Top"),
    ]:
        for mode in MODES:
            means = []
            stds = []
            for pct in LAYERS_MUTATED_PERCENTS:
                vals = [
                    row.total_s for row in results
                    if row.direction == direction and row.mode == mode and row.layers_mutated_pct == pct
                ]
                means.append(float(np.mean(vals)) if vals else float("nan"))
                stds.append(float(np.std(vals, ddof=0)) if vals else 0.0)
            ax.errorbar(LAYERS_MUTATED_PERCENTS, means, yerr=stds, label=mode, color=MODE_COLORS[mode],
                        marker="o", capsize=3, linewidth=1.5)

        ax.set_xticks(LAYERS_MUTATED_PERCENTS)
        ax.set_xlabel("% of Layers/Allotments Updated")
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

    all_results: list[RebuildRow] = []
    for model, base_image in EXPERIMENTS:
        chunks_dir, max_allowed_splits = prepare_model_splits(model)
        log.result(f"\n===== Experiment: {model} / {base_image} (max_allowed_splits={max_allowed_splits}) =====")
        prepare_local_registry(base_image, registry(CFG))

        methods = make_methods(base_image)

        log.info(f"Preparing model at full capacity ({max_allowed_splits} buckets)...")
        groups = generate_build_artifacts(chunks_dir, max_allowed_splits, base_image, CFG)
        print_packing_table(chunks_dir, model, max_allowed_splits, ["full"], [max_allowed_splits])
        print_mutation_table(groups)
        snapshot_artifacts(
            SCRIPT_DIR,
            rebuild_artifacts_dir(SCRIPT_DIR, execution_ts, model, base_image),
        )

        results = measure_rebuilds(groups, methods, model, base_image, max_allowed_splits)

        save_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, max_allowed_splits, execution_ts)
        all_results.extend(results)

        log.result(f"\n{'run':>4}  {'pct':>4}  {'n_mut':>5}  {'direction':<16}  {'mode':<14}  {'total':>8}")
        log.result("-" * 60)
        for row in results:
            log.result(f"{row.run:>4}  {row.layers_mutated_pct:>3}%  {row.n_layers_mutated:>5}  {row.direction:<16}  {row.mode:<14}  "
                       f"{row.total_s:>8.2f}")

    if all_results:
        save_merged_csv(all_results, execution_ts)

    clear_artifacts(SCRIPT_DIR)


if __name__ == "__main__":
    main()
