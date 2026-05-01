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
    poll_until_prefetch_done, prefetch_span, bg_fetch_spans, passthrough_open_spans,
    pull_name, prepare_mode,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    # ("openai-community/gpt2-large", "docker.io/library/python:3.12-slim"),    # ~3.25 GB    ~50 MB
    ("openlm-research/open_llama_3b", "docker.io/library/python:3.12-slim"), 
]
MODES = ["2dfs-stargz"]
BASE_SPLITS = [2, 4, 6, 8]
N_SPLITS = 10
N_RUNS = 1
CONFIG_OPTIONS: list[tuple[dict, str]] = [
    # ({"noprefetch": True, "prefetch_async_size": 0, "no_background_fetch": True, "log_file_access": True}, "no prefetch"),
    ({"noprefetch": False, "prefetch_async_size": 0, "no_background_fetch": True, "log_file_access": True, "prefetch_timeout_sec": 60}, "prefetch"),
    ({"noprefetch": False, "prefetch_async_size": 1, "no_background_fetch": True, "log_file_access": True, "prefetch_timeout_sec": 60}, "prefetch, async"),
    ({"noprefetch": False, "prefetch_async_size": 1, "no_background_fetch": False, "log_file_access": True, "prefetch_timeout_sec": 60}, "prefetch, async, bg fetch"),
]
CFG = load_config()
VERBOSE = True

PULL_COLOR = "#ffd966"
PREFETCH_COLOR = "#9467bd"
BG_DOWNLOAD_COLOR = "#d5d5d5"
FILE_OPEN_ON_DEMAND_COLOR = "#d62728"
FILE_OPEN_CACHE_COLOR = "#17becf"
RUN_COLOR = "#f5b7b1"
LAYER_CMAP = plt.get_cmap("tab10")  # per-layer color (shared by prefetch + file_open)


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
    prefetch_layer_events: list[tuple[str, float, float]]
    bg_download_start_s: float | None
    bg_download_end_s: float | None
    file_open_cache_spans: list[tuple[str, float, float]]
    file_open_on_demand_spans: list[tuple[str, float, float]]
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
        file_open_cache_spans, file_open_on_demand_spans = passthrough_open_spans(raw_entries)
        log.result(f"  file_open cache: {len(file_open_cache_spans)}, on-demand: {len(file_open_on_demand_spans)}")
        ends = [run_end_s]
        if span:
            ends.append(span[1])
        if dl_span:
            ends.append(dl_span[1])
        if file_open_cache_spans:
            ends.append(max(e for _, _, e in file_open_cache_spans))
        if file_open_on_demand_spans:
            ends.append(max(e for _, _, e in file_open_on_demand_spans))
        save_stargz_run_log(
            pull_start_s,
            max(ends),
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
            prefetch_layer_events=[(e.layer_sha, e.start_s, e.end_s) for e in events],
            bg_download_start_s=dl_span[0] if dl_span else None,
            bg_download_end_s=dl_span[1] if dl_span else None,
            file_open_cache_spans=file_open_cache_spans,
            file_open_on_demand_spans=file_open_on_demand_spans,
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
        "file_open_cache_rel_events", "file_open_on_demand_rel_events",
        "run_rel_start_s", "run_rel_end_s",
    ]
    rows = []
    for (mode, label), entries in results.items():
        for s in entries:
            ref = s.pull_start_s
            file_open_cache_str = "|".join(f"{sha[:12]}:{a - ref:.3f}:{b - ref:.3f}" for sha, a, b in s.file_open_cache_spans)
            file_open_on_demand_str = "|".join(f"{sha[:12]}:{a - ref:.3f}:{b - ref:.3f}" for sha, a, b in s.file_open_on_demand_spans)
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
                "file_open_cache_rel_events": file_open_cache_str,
                "file_open_on_demand_rel_events": file_open_on_demand_str,
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

    bar_h = 0.14
    prefetch_slot_h = 5 * bar_h  # per-layer micro-lanes for prefetch (up to 10 layers)
    # Slot stack (top→bottom): pull(1), prefetch(5), file_open(1), bg(1), run(1) → total 9*bar_h
    offsets = {
        "pull": -4.0 * bar_h,
        "prefetch": -1.0 * bar_h,
        "file_open": 2.0 * bar_h,
        "bg_download": 3.0 * bar_h,
        "run": 4.0 * bar_h,
    }
    row_spacing = 9 * bar_h + 0.3  # row stack height + gap

    for mode in MODES:
        n_cols = len(config_labels)
        fig, axes = plt.subplots(
            1, n_cols,
            figsize=(max(14, 4 * n_cols), max(3.5, row_spacing * len(splits) + 1.5)),
            squeeze=False,
            sharey=True,
        )

        x_max = 0.0
        for col_idx, label in enumerate(config_labels):
            ax = axes[0][col_idx]
            entries = results.get((mode, label), [])

            for y_idx, n in enumerate(splits):
                y = y_idx * row_spacing
                s = _median_span(entries, n)
                if s is None:
                    continue
                ref = s.pull_start_s

                pull_left = 0.0
                pull_w = s.pull_end_s - ref
                ax.barh(y + offsets["pull"], pull_w, left=pull_left, height=bar_h * 0.9,
                        color=PULL_COLOR, edgecolor=PULL_COLOR)

                # Build per-layer color map from union of prefetch + file_open events,
                # ordered by first-seen time. Same color is used for that layer in both
                # the prefetch micro-lane and the file_open lane.
                first_seen: dict[str, float] = {}
                for sha, start, _ in s.prefetch_layer_events:
                    if sha and (sha not in first_seen or start < first_seen[sha]):
                        first_seen[sha] = start
                for sha, start, _ in s.file_open_cache_spans:
                    if sha and (sha not in first_seen or start < first_seen[sha]):
                        first_seen[sha] = start
                for sha, start, _ in s.file_open_on_demand_spans:
                    if sha and (sha not in first_seen or start < first_seen[sha]):
                        first_seen[sha] = start
                ordered_layers = [sha for sha, _ in sorted(first_seen.items(), key=lambda kv: kv[1])]
                layer_color = {sha: LAYER_CMAP(i % LAYER_CMAP.N) for i, sha in enumerate(ordered_layers)}

                # Per-layer prefetch micro-lanes (hatch = backslash).
                prefetch_events = sorted(s.prefetch_layer_events, key=lambda e: e[1])
                if prefetch_events:
                    n_layers = len(prefetch_events)
                    sub_h = prefetch_slot_h / n_layers
                    slot_top = y + offsets["prefetch"] - prefetch_slot_h / 2
                    for i, (sha, ev_start, ev_end) in enumerate(prefetch_events):
                        sub_y = slot_top + (i + 0.5) * sub_h
                        c = layer_color.get(sha, PREFETCH_COLOR)
                        ax.barh(sub_y, ev_end - ev_start, left=ev_start - ref,
                                height=sub_h * 0.9,
                                color=c, edgecolor="black", hatch="\\\\", linewidth=0.4)

                # Single shared file_open lane: color = layer, hatch = event type.
                for sha, ev_start, ev_end in s.file_open_cache_spans:
                    c = layer_color.get(sha, FILE_OPEN_CACHE_COLOR)
                    ax.barh(y + offsets["file_open"], ev_end - ev_start,
                            left=ev_start - ref, height=bar_h * 0.9,
                            color=c, edgecolor="black", hatch="//", linewidth=0.4)

                for sha, ev_start, ev_end in s.file_open_on_demand_spans:
                    c = layer_color.get(sha, FILE_OPEN_ON_DEMAND_COLOR)
                    ax.barh(y + offsets["file_open"], ev_end - ev_start,
                            left=ev_start - ref, height=bar_h * 0.9,
                            color=c, edgecolor="black", hatch="xx", linewidth=0.4)

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
                    max((e - ref for _, _, e in s.file_open_cache_spans), default=0.0),
                    max((e - ref for _, _, e in s.file_open_on_demand_spans), default=0.0),
                )
                x_max = max(x_max, row_max)

            ax.set_yticks([i * row_spacing for i in range(len(splits))])
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
            mpatches.Patch(color=BG_DOWNLOAD_COLOR, label="bg download"),
            mpatches.Patch(color=RUN_COLOR, label="run"),
            mpatches.Patch(facecolor="lightgray", edgecolor="black",
                           hatch="\\\\", linewidth=0.4, label="prefetch (\\\\)"),
            mpatches.Patch(facecolor="lightgray", edgecolor="black",
                           hatch="//", linewidth=0.4, label="file open cache (//)"),
            mpatches.Patch(facecolor="lightgray", edgecolor="black",
                           hatch="xx", linewidth=0.4, label="file open on-demand (xx)"),
        ]
        fig.legend(
            handles=legend_handles, loc="lower right",
            bbox_to_anchor=(0.99, 0.01), ncol=len(legend_handles),
            fontsize=8, frameon=False,
        )

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
