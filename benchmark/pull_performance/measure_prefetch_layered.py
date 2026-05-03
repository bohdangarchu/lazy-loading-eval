import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

from shared import log
from shared.charts import figure_footer, save_figure, write_csv
from shared.config import load_config
from shared.registry import prepare_local_registry, clear_registry, registry
from shared.services import clear_stargz_cache, save_stargz_run_log
from shared.stargz_config import read_base_config, apply_overrides, apply_stargz_config
from shared.model import cleanup_pull_experiment
from pull_performance.prepare import prepare_chunks
from pull_performance.paths import (
    prefetch_layered_csv_path, prefetch_layered_chart_path,
    prefetch_layered_charts_run_dir, prefetch_layered_log_path,
    prefetch_layered_artifacts_dir,
)
from shared.artifacts import clear_artifacts
from pull_performance.prefetch_common import (
    LayerPrefetchEvent,
    poll_until_prefetch_done,
    pull_name,
    rpull,
    rpull_noprefetch,
    prepare_mode,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),
    # ("openai-community/gpt2-large", "docker.io/library/python:3.12-slim"), # 271 mb per split
    # ("Qwen/Qwen2-1.5B", "docker.io/ollama/ollama"),
]
MODES = ["2dfs-stargz", "2dfs-stargz-zstd"]
ALLOTMENTS = [2, 4]
N_CHUNKS = 10  # number of chunks to build the image with (always max)

# Config overrides to enable prefetch for this experiment.
PREFETCH_CONFIG_OVERRIDES = {"noprefetch": False, "prefetch_async_size": 1, "no_background_fetch": False}
NOPREFETCH_CONFIG_OVERRIDES = {"noprefetch": True}

DOWNLOAD_COLOR = "#1f77b4"
DECOMPRESS_COLOR = "#2ca02c"

CFG = load_config()
VERBOSE = True


# ── data structures ────────────────────────────────────────────────


@dataclass
class PullPrefetchResult:
    mode: str
    n_allotments: int
    pull_start_s: float              # seconds since epoch
    pull_end_s: float                # seconds since epoch
    noprefetch_pull_duration_s: float  # duration of a plain pull (no prefetch)
    layers: list[LayerPrefetchEvent]


# ── measure ────────────────────────────────────────────────────────


def _measure_one(
    mode: str, n: int, source_image: str, cfg,
    base_config: str, prefetch_config_content: str,
    model: str, base_image: str, execution_ts: str,
) -> PullPrefetchResult:
    image = pull_name(mode, source_image, cfg, n)

    # Noprefetch pull for baseline duration
    apply_stargz_config(apply_overrides(base_config, NOPREFETCH_CONFIG_OVERRIDES))
    log.info(f"  Clearing stargz cache (noprefetch)...")
    clear_stargz_cache()
    log.info(f"  Pulling noprefetch {mode} ({n} allotments): {image}")
    np_start = time.time()
    rpull_noprefetch(image)
    noprefetch_pull_duration_s = time.time() - np_start
    log.info(f"  Noprefetch pull done in {noprefetch_pull_duration_s:.1f}s")

    log.info(f"  Sleeping {cfg.pull_cooldown}s...")
    time.sleep(cfg.pull_cooldown)

    # Prefetch pull
    apply_stargz_config(prefetch_config_content)
    log.info(f"  Clearing stargz cache (prefetch)...")
    clear_stargz_cache()
    log.info(f"  Pulling {mode} ({n} allotments): {image}")
    pull_start_s = time.time()
    rpull(image)
    pull_end_s = time.time()
    log.info(f"  Pull done in {pull_end_s - pull_start_s:.1f}s, waiting for prefetch to finish...")

    events = poll_until_prefetch_done(pull_start_s)
    log.result(f"  Prefetch events: {len(events)} layers")
    for ev in events:
        log.info(
            f"    {ev.layer_sha[7:19]} total={ev.total_ms:.0f}ms "
            f"dl={ev.download_ms:.0f}ms decomp={ev.decompress_ms:.0f}ms "
            f"size={ev.prefetch_size_bytes // 1024 // 1024}MB"
        )

    save_stargz_run_log(
        pull_start_s, time.time(),
        prefetch_layered_log_path(SCRIPT_DIR, model, base_image, mode, n, execution_ts),
    )

    return PullPrefetchResult(
        mode=mode,
        n_allotments=n,
        pull_start_s=pull_start_s,
        pull_end_s=pull_end_s,
        noprefetch_pull_duration_s=noprefetch_pull_duration_s,
        layers=events,
    )


def measure(
    chunk_paths: list[str], source_image: str, cfg,
    model: str, base_image: str, execution_ts: str,
) -> list[PullPrefetchResult]:
    results = []
    base_config = read_base_config()
    prefetch_config_content = apply_overrides(base_config, PREFETCH_CONFIG_OVERRIDES)

    try:
        clear_stargz_cache()

        for mode in MODES:
            log.info(f"\n=== Preparing mode: {mode} ===")
            prepare_local_registry(source_image, registry(cfg))
            artifacts_dir = prefetch_layered_artifacts_dir(SCRIPT_DIR, execution_ts, model, base_image, mode)
            prepare_mode(mode, chunk_paths, source_image, cfg, artifacts_dir)

            for n in ALLOTMENTS:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                log.info(f"\n[{ts}] === {mode}: {n} allotments ===")
                result = _measure_one(
                    mode, n, source_image, cfg, base_config, prefetch_config_content,
                    model, base_image, execution_ts,
                )
                results.append(result)
                log.info(f"\nSleeping {cfg.pull_cooldown}s...")
                time.sleep(cfg.pull_cooldown)

            clear_registry(cfg, preserve_base=True)
    finally:
        log.info("\n=== Restoring original stargz config ===")
        apply_stargz_config(base_config)

    return results


# ── output ─────────────────────────────────────────────────────────


def save_csv(results: list[PullPrefetchResult], model: str, base_image: str, execution_ts: str) -> None:
    rows = []
    for r in results:
        ref = r.pull_start_s
        for ev in r.layers:
            rows.append({
                "mode": r.mode,
                "n_allotments": r.n_allotments,
                "layer_sha": ev.layer_sha,
                "start_rel_s": f"{ev.start_s - ref:.3f}",
                "download_end_rel_s": f"{ev.download_end_s - ref:.3f}",
                "end_rel_s": f"{ev.end_s - ref:.3f}",
                "total_ms": f"{ev.total_ms:.1f}",
                "download_ms": f"{ev.download_ms:.1f}",
                "decompress_ms": f"{ev.decompress_ms:.1f}",
                "prefetch_size_bytes": ev.prefetch_size_bytes,
                "pull_end_rel_s": f"{r.pull_end_s - ref:.3f}",
                "noprefetch_pull_duration_s": f"{r.noprefetch_pull_duration_s:.3f}",
            })
    if not rows:
        log.info("No prefetch data to save.")
        return
    write_csv(prefetch_layered_csv_path(SCRIPT_DIR, model, base_image, execution_ts), list(rows[0].keys()), rows)


def plot(results: list[PullPrefetchResult], model: str, base_image: str, execution_ts: str) -> None:
    os.makedirs(prefetch_layered_charts_run_dir(SCRIPT_DIR, execution_ts), exist_ok=True)

    for mode in MODES:
        mode_results = [r for r in results if r.mode == mode]
        if not mode_results:
            continue

        n_rows = len(mode_results)
        fig, axes = plt.subplots(
            n_rows, 1,
            figsize=(14, max(3, n_rows * 2.5)),
            squeeze=False,
        )

        for row_idx, result in enumerate(mode_results):
            ax = axes[row_idx][0]
            ref = result.pull_start_s

            if not result.layers:
                ax.set_title(f"{result.n_allotments} allotments — no prefetch data")
                continue

            for i, ev in enumerate(result.layers):
                dl_start = ev.start_s - ref
                dl_dur = ev.download_ms / 1000
                decomp_dur = ev.decompress_ms / 1000

                ax.barh(i, dl_dur, left=dl_start, height=0.6,
                        color=DOWNLOAD_COLOR, alpha=0.85)
                ax.barh(i, decomp_dur, left=dl_start + dl_dur, height=0.6,
                        color=DECOMPRESS_COLOR, alpha=0.85)

            ax.axvline(x=result.pull_end_s - ref, color="red", linestyle="--", linewidth=1.2)
            ax.axvline(x=result.noprefetch_pull_duration_s, color="orange", linestyle="--", linewidth=1.2)

            layer_labels = [
                f"{ev.layer_sha[7:19]} ({ev.prefetch_size_bytes // 1024 // 1024}MB)"
                for ev in result.layers
            ]
            ax.set_yticks(range(len(result.layers)))
            ax.set_yticklabels(layer_labels, fontsize=7, family="monospace")
            ax.set_xlabel("Time since pull start (s)")
            ax.set_title(f"{result.n_allotments} allotments — {len(result.layers)} layers prefetched")
            ax.grid(True, linestyle="--", alpha=0.3, axis="x")

        dl_patch = mpatches.Patch(color=DOWNLOAD_COLOR, alpha=0.85, label="download")
        dc_patch = mpatches.Patch(color=DECOMPRESS_COLOR, alpha=0.85, label="decompress")
        pull_end_line = mlines.Line2D([], [], color="red", linestyle="--", label="pull end (prefetch)")
        noprefetch_end_line = mlines.Line2D([], [], color="orange", linestyle="--", label="pull end (no prefetch)")
        axes[0][0].legend(handles=[dl_patch, dc_patch, pull_end_line, noprefetch_end_line], loc="upper right", fontsize=8)

        fig.suptitle(f"Per-layer Prefetch Timeline — {mode}", fontsize=12)
        figure_footer(fig, model, base_image)
        fig.tight_layout()
        save_figure(fig, prefetch_layered_chart_path(SCRIPT_DIR, model, base_image, mode, execution_ts))


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    clear_artifacts(SCRIPT_DIR)
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for model, base_image in EXPERIMENTS:
        chunk_paths = prepare_chunks(model, N_CHUNKS)
        results = measure(chunk_paths, base_image, CFG, model, base_image, execution_ts)
        save_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, execution_ts)
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)

    clear_artifacts(SCRIPT_DIR)


if __name__ == "__main__":
    main()
