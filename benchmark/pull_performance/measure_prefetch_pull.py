import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

from shared import log
from shared.charts import figure_footer, save_figure, write_csv
from shared.config import load_config
from shared.registry import prepare_local_registry, clear_registry, registry
from shared.services import clear_stargz_cache, save_stargz_run_log, collect_stargz_journal_since
from shared.stargz_config import read_base_config, apply_overrides, apply_stargz_config
from shared.model import cleanup_pull_experiment
from pull_performance.measure import _timed_pull, _timed_run, _run_cmd
from pull_performance.paths import (
    prefetch_pull_charts_run_dir, prefetch_pull_csv_path, prefetch_pull_chart_path,
    prefetch_pull_log_path,
)
from pull_performance.prepare import prepare_chunks
from pull_performance.prefetch_common import (
    poll_until_prefetch_done, prefetch_span, bg_fetch_spans,
    pull_name, prepare_mode,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    ("openai-community/gpt2-large", "docker.io/library/python:3.12-slim"),    # ~3.25 GB    ~50 MB
]
MODES = ["2dfs-stargz"]
BASE_SPLITS = [6, 8]
N_SPLITS = 10
N_RUNS = 1
CONFIG_OPTIONS: list[tuple[dict, str]] = [
    ({"noprefetch": True, "prefetch_async_size": 0, "no_background_fetch": True, "log_file_access": True}, "no prefetch"),
    # ({"noprefetch": False, "prefetch_async_size": 0, "no_background_fetch": True, "log_file_access": True}, "prefetch"),
    # ({"noprefetch": False, "prefetch_async_size": 1, "no_background_fetch": True, "log_file_access": True}, "prefetch, async"),
    ({"noprefetch": False, "prefetch_async_size": 1, "no_background_fetch": False, "log_file_access": True}, "prefetch, async, bg fetch"),
]
CFG = load_config()
VERBOSE = True

PULL_COLOR = "#1f77b4"
PREFETCH_COLOR = "#9467bd"
BG_DOWNLOAD_COLOR = "#ff7f0e"
RUN_COLOR = "#2ca02c"


# ── data structures ────────────────────────────────────────────────


@dataclass
class PullPrefetchSpan:
    run: int
    mode: str
    n_allotments: int
    config_label: str
    pull_start_s: float
    pull_end_s: float
    prefetch_start_s: float | None
    prefetch_end_s: float | None
    bg_download_start_s: float | None
    bg_download_end_s: float | None
    run_start_s: float
    run_end_s: float


# ── prepare ────────────────────────────────────────────────────────


def _prepare_all_images(source_image: str, cfg, chunk_paths: list[str]) -> None:
    log.info("\n=== Preparing images ===")
    for mode in MODES:
        log.info(f"\n--- Preparing mode: {mode} ---")
        prepare_local_registry(source_image, registry(cfg))
        prepare_mode(mode, chunk_paths, source_image, cfg)


# ── measure ────────────────────────────────────────────────────────


def _measure_config_option(
    mode: str, source_image: str, cfg,
    config_label: str, run_idx: int, model: str, base_image: str, execution_ts: str,
) -> list[PullPrefetchSpan]:
    results = []
    for n in BASE_SPLITS:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info(f"\n[{ts}] === {mode}: {n} allotments ===")
        clear_stargz_cache()

        image = pull_name(mode, source_image, cfg, n)
        pull_start_s = time.time()
        pull_t = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", "--use-containerd-labels", image])
        pull_end_s = pull_start_s + pull_t
        log.result(f"  pull: {pull_t:.2f}s")

        name = f"run-stargz-pull-{uuid.uuid4().hex[:8]}"
        run_start_s = time.time()
        run_t = _timed_run([
            "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
            image, name, *_run_cmd(n),
        ])
        run_end_s = run_start_s + run_t
        log.result(f"  run: {run_t:.2f}s")

        # Wait for any background prefetch to settle, then parse the journal.
        events = poll_until_prefetch_done(pull_start_s)
        span = prefetch_span(events)
        if span:
            log.result(f"  prefetch span: {span[0] - pull_start_s:.2f}s → {span[1] - pull_start_s:.2f}s ({len(events)} layers)")
        else:
            log.result(f"  prefetch span: none")

        raw_entries = collect_stargz_journal_since(pull_start_s)
        dl_span = bg_fetch_spans(raw_entries)
        if dl_span:
            log.result(f"  bg download span: {dl_span[0] - pull_start_s:.2f}s → {dl_span[1] - pull_start_s:.2f}s")
        save_stargz_run_log(
            pull_start_s,
            max(run_end_s, span[1] if span else run_end_s, dl_span[1] if dl_span else run_end_s),
            prefetch_pull_log_path(SCRIPT_DIR, model, base_image, mode, config_label, n, run_idx, execution_ts),
        )

        results.append(PullPrefetchSpan(
            run=run_idx,
            mode=mode,
            n_allotments=n,
            config_label=config_label,
            pull_start_s=pull_start_s,
            pull_end_s=pull_end_s,
            prefetch_start_s=span[0] if span else None,
            prefetch_end_s=span[1] if span else None,
            bg_download_start_s=dl_span[0] if dl_span else None,
            bg_download_end_s=dl_span[1] if dl_span else None,
            run_start_s=run_start_s,
            run_end_s=run_end_s,
        ))
        log.info(f"\nSleeping {cfg.pull_cooldown}s before next...")
        time.sleep(cfg.pull_cooldown)
    return results


# ── orchestration ──────────────────────────────────────────────────


def measure(
    chunk_paths: list[str], source_image: str, cfg, model: str, base_image: str, execution_ts: str,
) -> dict[tuple[str, str], list[PullPrefetchSpan]]:
    results: dict[tuple[str, str], list[PullPrefetchSpan]] = {}

    base_config = read_base_config()

    try:
        for overrides, label in CONFIG_OPTIONS:
            log.info(f"\n=== Config option: {label} ===")
            clear_registry(cfg, preserve_base=True)
            _prepare_all_images(source_image, cfg, chunk_paths)
            config_content = apply_overrides(base_config, overrides)
            apply_stargz_config(config_content)

            for mode in MODES:
                key = (mode, label)
                results[key] = []
                for run in range(N_RUNS):
                    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                             f"=== Run {run + 1}/{N_RUNS} | {mode} | {label} ===")
                    results[key].extend(_measure_config_option(
                        mode, source_image, cfg, label, run, model, base_image, execution_ts,
                    ))
    finally:
        log.info("\n=== Restoring base stargz config ===")
        apply_stargz_config(base_config)

    return results


# ── output ─────────────────────────────────────────────────────────


def save_csv(
    results: dict[tuple[str, str], list[PullPrefetchSpan]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    fieldnames = [
        "run", "mode", "config", "n_allotments",
        "pull_rel_start_s", "pull_rel_end_s",
        "prefetch_rel_start_s", "prefetch_rel_end_s",
        "bg_download_rel_start_s", "bg_download_rel_end_s",
        "run_rel_start_s", "run_rel_end_s",
    ]
    rows = []
    for (mode, label), entries in results.items():
        for s in entries:
            ref = s.pull_start_s
            rows.append({
                "run": s.run,
                "mode": mode,
                "config": label,
                "n_allotments": s.n_allotments,
                "pull_rel_start_s": "0.000",
                "pull_rel_end_s": f"{s.pull_end_s - ref:.3f}",
                "prefetch_rel_start_s": f"{s.prefetch_start_s - ref:.3f}" if s.prefetch_start_s is not None else "",
                "prefetch_rel_end_s": f"{s.prefetch_end_s - ref:.3f}" if s.prefetch_end_s is not None else "",
                "bg_download_rel_start_s": f"{s.bg_download_start_s - ref:.3f}" if s.bg_download_start_s is not None else "",
                "bg_download_rel_end_s": f"{s.bg_download_end_s - ref:.3f}" if s.bg_download_end_s is not None else "",
                "run_rel_start_s": f"{s.run_start_s - ref:.3f}",
                "run_rel_end_s": f"{s.run_end_s - ref:.3f}",
            })
    if not rows:
        log.info("No data to save.")
        return
    write_csv(prefetch_pull_csv_path(SCRIPT_DIR, model, base_image, execution_ts), fieldnames, rows)


def _median_span(entries: list[PullPrefetchSpan], n: int) -> PullPrefetchSpan | None:
    """Pick the run with median total elapsed time at this allotment count."""
    runs = [s for s in entries if s.n_allotments == n]
    if not runs:
        return None
    runs.sort(key=lambda s: s.run_end_s - s.pull_start_s)
    return runs[len(runs) // 2]


def plot(
    results: dict[tuple[str, str], list[PullPrefetchSpan]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    os.makedirs(prefetch_pull_charts_run_dir(SCRIPT_DIR, execution_ts), exist_ok=True)

    config_labels = [label for _, label in CONFIG_OPTIONS]
    splits = sorted({s.n_allotments for entries in results.values() for s in entries})

    bar_h = 0.20
    offsets = {"pull": -1.5 * bar_h, "prefetch": -0.5 * bar_h, "bg_download": 0.5 * bar_h, "run": 1.5 * bar_h}

    for mode in MODES:
        n_cols = len(config_labels)
        fig, axes = plt.subplots(
            1, n_cols,
            figsize=(max(14, 4 * n_cols), max(3.5, 0.7 * len(splits) + 1.5)),
            squeeze=False,
            sharey=True,
        )

        x_max = 0.0
        for col_idx, label in enumerate(config_labels):
            ax = axes[0][col_idx]
            entries = results.get((mode, label), [])

            for y, n in enumerate(splits):
                s = _median_span(entries, n)
                if s is None:
                    continue
                ref = s.pull_start_s

                pull_left = 0.0
                pull_w = s.pull_end_s - ref
                ax.barh(y + offsets["pull"], pull_w, left=pull_left, height=bar_h * 0.9,
                        color=PULL_COLOR, edgecolor=PULL_COLOR)

                if s.prefetch_start_s is not None and s.prefetch_end_s is not None:
                    pf_left = s.prefetch_start_s - ref
                    pf_w = s.prefetch_end_s - s.prefetch_start_s
                    ax.barh(y + offsets["prefetch"], pf_w, left=pf_left, height=bar_h * 0.9,
                            color=PREFETCH_COLOR, edgecolor=PREFETCH_COLOR)

                if s.bg_download_start_s is not None and s.bg_download_end_s is not None:
                    ax.barh(y + offsets["bg_download"],
                            s.bg_download_end_s - s.bg_download_start_s,
                            left=s.bg_download_start_s - ref, height=bar_h * 0.9,
                            color=BG_DOWNLOAD_COLOR, edgecolor=BG_DOWNLOAD_COLOR)

                run_left = s.run_start_s - ref
                run_w = s.run_end_s - s.run_start_s
                ax.barh(y + offsets["run"], run_w, left=run_left, height=bar_h * 0.9,
                        color=RUN_COLOR, edgecolor=RUN_COLOR)

                row_max = max(
                    s.pull_end_s - ref,
                    s.run_end_s - ref,
                    (s.prefetch_end_s - ref) if s.prefetch_end_s is not None else 0.0,
                    (s.bg_download_end_s - ref) if s.bg_download_end_s is not None else 0.0,
                )
                x_max = max(x_max, row_max)

            ax.set_yticks(range(len(splits)))
            ax.set_yticklabels([str(s) for s in splits])
            ax.set_xlabel("Time since pull start (s)")
            ax.set_title(label, fontsize=10)
            ax.grid(True, linestyle="--", alpha=0.3, axis="x")
            ax.invert_yaxis()

        for col_idx in range(n_cols):
            axes[0][col_idx].set_xlim(0, x_max * 1.05)
        axes[0][0].set_ylabel("Number of allotments pulled")

        legend_handles = [
            mpatches.Patch(color=PULL_COLOR, label="pull"),
            mpatches.Patch(color=PREFETCH_COLOR, label="prefetch"),
            mpatches.Patch(color=BG_DOWNLOAD_COLOR, label="bg download"),
            mpatches.Patch(color=RUN_COLOR, label="run"),
        ]
        axes[0][-1].legend(handles=legend_handles, loc="lower right", fontsize=8)

        fig.suptitle(
            f"Pull / Prefetch / Run timeline by stargz config ({mode}, "
            f"median run, n={N_RUNS} runs)",
            fontsize=11,
        )
        figure_footer(fig, model, base_image)
        fig.tight_layout(rect=[0, 0.08, 1, 1])
        save_figure(fig, prefetch_pull_chart_path(SCRIPT_DIR, model, base_image, mode, execution_ts))


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info(f"Modes: {MODES}")
    log.info(f"Config options: {[label for _, label in CONFIG_OPTIONS]}")
    log.info(f"Splits (total): {N_SPLITS}")
    log.info(f"Splits (measured): {BASE_SPLITS}")
    log.info(f"Runs: {N_RUNS}")

    log.info("Pre-run cleanup...")
    for model, _ in EXPERIMENTS:
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)

    for model, base_image in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} =====")
        prepare_local_registry(base_image, registry(CFG))

        chunk_paths = prepare_chunks(model, N_SPLITS)
        results = measure(chunk_paths, base_image, CFG, model, base_image, execution_ts)

        save_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, execution_ts)
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)


if __name__ == "__main__":
    main()
