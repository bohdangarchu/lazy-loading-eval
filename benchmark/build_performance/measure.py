import csv
import os
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import numpy as np
import psutil

from shared import log
from shared.build_result import BuildResult
from build_performance.paths import (
    build_csv_path, build_chart_path,
    resource_csv_path, resource_chart_path,
    resource_cpu_charts_run_dir, resource_ram_charts_run_dir,
    build_artifacts_dir,
    build_merged_csv_path, resource_merged_csv_path,
    build_run_metadata_path,
)
from shared.artifacts import snapshot_artifacts, clear_artifacts
from shared.config import load_config
from shared.charts import MODE_COLORS, figure_footer, bar_group_xticks, save_figure, write_csv
from shared.registry import prepare_local_registry, registry, image_slug
from shared.run_metadata import write_run_json
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_2dfs_stargz_zstd as b2sz
from build_performance import build_stargz as bs
from build_performance import build_base as bb
from build_performance.prepare import (
    generate_build_artifacts, prepare_model_splits, print_packing_table,
    packing_preview_data, split_stats,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    # ("openai-community/gpt2",        "docker.io/library/python:3.12-slim"),  # ~0.5GB     ~50 MB
    # ("Qwen/Qwen2-1.5B",              "docker.io/library/python:3.12-slim"),  # ~3.09 GB     ~3.4 GB
    # ("EleutherAI/pythia-1.4b", "docker.io/library/python:3.12-slim"), # 3 GB
    ("openlm-research/open_llama_3b", "docker.io/ollama/ollama")    # ~6.0 GB     ~3.4 GB
    # ("openlm-research/open_llama_7b", "docker.io/ollama/ollama")                # 14 GB
]
CFG = load_config()
VERBOSE = False
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
CAPACITIES = [0, 25, 50, 75, 100]
SCHEMA_VERSION = 1

def num_layers_for_capacity(capacity: int, max_allowed_splits: int) -> int:
    if capacity <= 0:
        return 1
    return max(1, max_allowed_splits * capacity // 100)


@dataclass(frozen=True)
class BuildRow:
    schema_version: int
    model: str
    base_image: str
    max_allowed_splits: int
    run: int
    capacity: int
    num_layers: int
    mode: str
    total_s: float


@dataclass
class RunSamples:
    """CPU and RAM samples collected during one (capacity, mode, run) window."""
    cpu: list[float] = field(default_factory=list)
    mem: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class ResourceRow:
    schema_version: int
    model: str
    base_image: str
    max_allowed_splits: int
    timestamp_ms: int
    cpu_percent: float
    mem_mb: float
    mode: str
    capacity: int | None
    run: int | None


class ResourceMonitor:
    def __init__(self, model: str, base_image: str, max_allowed_splits: int):
        self._samples: list[ResourceRow] = []
        self._model = model
        self._base_image = base_image
        self._max_allowed_splits = max_allowed_splits
        self._mode: str = "idle"
        self._capacity: int | None = None
        self._run: int | None = None
        self._stop = threading.Event()

    def set_context(self, mode: str, capacity: int, run: int) -> None:
        self._mode = mode
        self._capacity = capacity
        self._run = run

    def set_idle(self) -> None:
        self._mode = "idle"
        self._capacity = None
        self._run = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self) -> list[ResourceRow]:
        self._stop.set()
        self._thread.join()
        return self._samples

    def _poll(self) -> None:
        while not self._stop.is_set():
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().used / (1024 * 1024)  # MB
            ts = int(time.time() * 1000)
            self._samples.append(ResourceRow(
                schema_version=SCHEMA_VERSION,
                model=self._model,
                base_image=self._base_image,
                max_allowed_splits=self._max_allowed_splits,
                timestamp_ms=ts,
                cpu_percent=cpu,
                mem_mb=mem,
                mode=self._mode,
                capacity=self._capacity,
                run=self._run,
            ))


def _clear_cache(mode: str, cfg) -> None:
    if mode == "2dfs":
        b2.clear_cache(cfg)
    elif mode == "2dfs-stargz":
        b2s.clear_cache(cfg)
    elif mode == "2dfs-stargz-zstd":
        b2sz.clear_cache(cfg)
    elif mode == "stargz":
        bs.clear_cache()
    elif mode == "base":
        bb.clear_cache()
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _run_one(mode: str, n: int, cfg, source_image: str) -> BuildResult:
    if mode == "2dfs":
        return b2.run_one(n, cfg, source_image)
    elif mode == "2dfs-stargz":
        return b2s.run_one(n, cfg, source_image)
    elif mode == "2dfs-stargz-zstd":
        return b2sz.run_one(n, cfg, source_image)
    elif mode == "stargz":
        return bs.run_one(n, cfg)
    elif mode == "base":
        return bb.run_one(n, cfg)
    raise ValueError(f"Unknown mode: {mode}")


def measure_builds(
    model: str, chunks_dir: str, max_allowed_splits: int, source_image: str, cfg=CFG,
    monitor: ResourceMonitor | None = None, execution_ts: str = "",
) -> list[BuildRow]:
    results: list[BuildRow] = []

    for run in range(cfg.build_n_runs):
        log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Run {run + 1}/{cfg.build_n_runs} ===")
        for cap in CAPACITIES:
            num_layers = num_layers_for_capacity(cap, max_allowed_splits)
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Preparing capacity={cap}% ({num_layers} layer(s)) ===")
            generate_build_artifacts(chunks_dir, num_layers, source_image, cfg)
            if execution_ts:
                snapshot_artifacts(
                    SCRIPT_DIR,
                    build_artifacts_dir(SCRIPT_DIR, execution_ts, model, source_image, cap),
                )
            for i, mode in enumerate(MODES):
                if monitor:
                    monitor.set_context(mode, cap, run)
                log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === {mode}: capacity={cap}% ({num_layers} layer(s)) ===")
                _clear_cache(mode, cfg)
                br = _run_one(mode, num_layers, cfg, source_image)
                if monitor:
                    monitor.set_idle()
                results.append(BuildRow(
                    schema_version=SCHEMA_VERSION,
                    model=model,
                    base_image=source_image,
                    max_allowed_splits=max_allowed_splits,
                    run=run,
                    capacity=cap,
                    num_layers=num_layers,
                    mode=mode,
                    total_s=br.total_s,
                ))

                is_last = (i == len(MODES) - 1) and (cap == CAPACITIES[-1]) and (run == cfg.build_n_runs - 1)
                if not is_last:
                    log.info(f"\nSleeping {cfg.build_cooldown}s before next...")
                    time.sleep(cfg.build_cooldown)

    return results


def _write_build_rows(output_path: str, results: list[BuildRow]) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = [f.name for f in fields(BuildRow)]
    rows = [{**asdict(r), "total_s": f"{r.total_s:.4f}"} for r in results]
    write_csv(output_path, fieldnames, rows)


def save_csv(results: list[BuildRow], model: str, base_image: str, execution_ts: str) -> None:
    _write_build_rows(build_csv_path(SCRIPT_DIR, model, base_image, execution_ts), results)


def save_merged_csv(results: list[BuildRow], execution_ts: str) -> None:
    _write_build_rows(build_merged_csv_path(SCRIPT_DIR, execution_ts), results)


def plot(results: list[BuildRow], model: str, base_image: str, max_allowed_splits: int, execution_ts: str) -> None:
    capacities = sorted(set(r.capacity for r in results))

    fig, ax = plt.subplots(figsize=(max(8, len(capacities) * 2), 5))

    for mode in MODES:
        means = []
        stds = []
        for cap in capacities:
            vals = [r.total_s for r in results if r.mode == mode and r.capacity == cap]
            means.append(float(np.mean(vals)) if vals else float("nan"))
            stds.append(float(np.std(vals, ddof=0)) if vals else 0.0)
        ax.errorbar(capacities, means, yerr=stds, label=mode, color=MODE_COLORS[mode],
                    marker="o", capsize=3, linewidth=1.5)

    ax.set_xticks(capacities)
    ax.set_xlabel("Split capacity (%)")
    ax.set_ylabel("Total build time (s)")
    ax.set_title(f"Build performance (mean ± std, n={CFG.build_n_runs} runs)")
    ax.legend(loc="best", fontsize="small")
    ax.grid(True, linestyle="--", alpha=0.5)
    fig.tight_layout()
    figure_footer(fig, model, base_image, max_allowed_splits=max_allowed_splits)
    path = build_chart_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_figure(fig, path)


def _write_resource_rows(output_path: str, samples: list[ResourceRow]) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = [f.name for f in fields(ResourceRow)]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in samples:
            row = asdict(s)
            if row["capacity"] is None:
                row["capacity"] = ""
            if row["run"] is None:
                row["run"] = ""
            writer.writerow(row)
    log.result(f"Resource CSV saved to {output_path}")


def save_resource_csv(
    samples: list[ResourceRow], model: str, base_image: str, execution_ts: str,
) -> None:
    _write_resource_rows(resource_csv_path(SCRIPT_DIR, model, base_image, execution_ts), samples)


def save_merged_resource_csv(samples: list[ResourceRow], execution_ts: str) -> None:
    _write_resource_rows(resource_merged_csv_path(SCRIPT_DIR, execution_ts), samples)


def plot_resource(
    samples: list[ResourceRow],
    model: str, base_image: str, max_allowed_splits: int, execution_ts: str,
) -> None:
    if not samples:
        return

    colors = {mode: MODE_COLORS[mode] for mode in MODES}
    labels = {mode: mode for mode in MODES}

    # (capacity, mode) -> {run_index: RunSamples}
    by_cap_mode: dict[tuple[int, str], dict[int, RunSamples]] = defaultdict(
        lambda: defaultdict(RunSamples)
    )

    for s in samples:
        if s.mode == "idle" or s.capacity is None or s.run is None:
            continue
        bucket = by_cap_mode[(s.capacity, s.mode)][s.run]
        bucket.cpu.append(s.cpu_percent)
        bucket.mem.append(s.mem_mb)

    capacities = sorted({cap for (cap, _) in by_cap_mode.keys()})
    if not capacities:
        return

    x_labels = [f"{c}" for c in capacities]
    x = range(len(capacities))
    n_modes = len(MODES)
    bar_width = 0.8 / n_modes

    fig, (ax_cpu, ax_mem) = plt.subplots(2, 1, figsize=(max(8, len(capacities) * 2), 8))

    for i, mode in enumerate(MODES):
        cpu_run_medians_by_cap = []
        mem_run_medians_by_cap = []
        for cap in capacities:
            runs = by_cap_mode.get((cap, mode), {})
            cpu_run_medians_by_cap.append(
                [float(np.median(rs.cpu)) for rs in runs.values() if rs.cpu]
            )
            mem_run_medians_by_cap.append(
                [float(np.median(rs.mem)) for rs in runs.values() if rs.mem]
            )

        offsets = [pos + i * bar_width for pos in x]
        cpu_bar_heights = [float(np.mean(v)) if v else 0.0 for v in cpu_run_medians_by_cap]
        mem_bar_heights = [float(np.mean(v)) if v else 0.0 for v in mem_run_medians_by_cap]
        cpu_errs = [float(np.std(v, ddof=0)) if v else 0.0 for v in cpu_run_medians_by_cap]
        mem_errs = [float(np.std(v, ddof=0)) if v else 0.0 for v in mem_run_medians_by_cap]

        ax_cpu.bar(offsets, cpu_bar_heights, bar_width, yerr=cpu_errs, label=labels[mode],
                   color=colors[mode], edgecolor="black", linewidth=0.5,
                   error_kw={"ecolor": "black", "capsize": 3, "elinewidth": 1})
        ax_mem.bar(offsets, mem_bar_heights, bar_width, yerr=mem_errs, label=labels[mode],
                   color=colors[mode], edgecolor="black", linewidth=0.5,
                   error_kw={"ecolor": "black", "capsize": 3, "elinewidth": 1})

    bar_group_xticks(ax_cpu, len(capacities), n_modes, bar_width, x_labels)
    ax_cpu.set_ylabel("CPU Usage (%)")
    ax_cpu.set_title(f"Resource usage during builds (mean ± std, n={CFG.build_n_runs} runs)")
    ax_cpu.legend(fontsize="small")
    ax_cpu.grid(True, linestyle="--", alpha=0.5, axis="y")

    bar_group_xticks(ax_mem, len(capacities), n_modes, bar_width, x_labels)
    ax_mem.set_xlabel("Split capacity (%)")
    ax_mem.set_ylabel("Memory Usage (MB)")
    ax_mem.legend(fontsize="small")
    ax_mem.grid(True, linestyle="--", alpha=0.5, axis="y")

    output_path = resource_chart_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.tight_layout()
    figure_footer(fig, model, base_image, max_allowed_splits=max_allowed_splits)
    save_figure(fig, output_path)


def plot_resource_individual(
    samples: list[ResourceRow],
    model: str, base_image: str, execution_ts: str, max_allowed_splits: int,
) -> None:
    if not samples:
        return

    series: dict[tuple[str, int, int], list[tuple[int, float, float]]] = defaultdict(list)

    for s in samples:
        if s.mode == "idle" or s.capacity is None or s.run is None:
            continue
        series[(s.mode, s.capacity, s.run)].append((s.timestamp_ms, s.cpu_percent, s.mem_mb))

    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    cpu_dir = resource_cpu_charts_run_dir(SCRIPT_DIR, execution_ts)
    ram_dir = resource_ram_charts_run_dir(SCRIPT_DIR, execution_ts)
    os.makedirs(cpu_dir, exist_ok=True)
    os.makedirs(ram_dir, exist_ok=True)

    for (mode_name, cap, run), points in sorted(series.items()):
        points.sort(key=lambda p: p[0])
        t0 = points[0][0]
        t_sec = [(p[0] - t0) / 1000.0 for p in points]
        cpu_vals = [p[1] for p in points]
        mem_vals = [p[2] for p in points]

        mode_slug = mode_name.replace("-", "_")
        file_stem = f"{model_slug}_{img_slug}_{mode_slug}_run{run + 1}_cap{cap}"

        def _add_run_footer(fig) -> None:
            figure_footer(fig, model, base_image, max_allowed_splits=max_allowed_splits)
            fig.text(
                0.99, 0.01,
                f"mode: {mode_name}  |  run: {run + 1}  |  capacity: {cap}%",
                fontsize=8,
                verticalalignment="bottom",
                horizontalalignment="right",
                family="monospace",
            )

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(t_sec, cpu_vals, color=MODE_COLORS.get(mode_name, "#888888"), linewidth=1)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("CPU (%)")
        ax.set_title("CPU usage over time")
        ax.grid(True, linestyle="--", alpha=0.5)
        fig.tight_layout()
        _add_run_footer(fig)
        save_figure(fig, os.path.join(cpu_dir, f"{file_stem}.png"), log_path=False)

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(t_sec, mem_vals, color=MODE_COLORS.get(mode_name, "#888888"), linewidth=1)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Memory (MB)")
        ax.set_title("RAM usage over time")
        ax.grid(True, linestyle="--", alpha=0.5)
        fig.tight_layout()
        _add_run_footer(fig)
        save_figure(fig, os.path.join(ram_dir, f"{file_stem}.png"), log_path=False)

    log.result(f"Per-run CPU charts saved to {cpu_dir}/")
    log.result(f"Per-run RAM charts saved to {ram_dir}/")


def main():
    log.set_verbose(VERBOSE)
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_started = datetime.now(timezone.utc)

    all_results: list[BuildRow] = []
    all_samples: list[ResourceRow] = []
    experiments_meta: list[dict] = []
    for model, base_image in EXPERIMENTS:
        chunks_dir, max_allowed_splits = prepare_model_splits(model)
        log.result(f"\n===== Experiment: {model} / {base_image} (max_allowed_splits={max_allowed_splits}) =====")

        generate_build_artifacts(chunks_dir, max_allowed_splits, base_image, CFG)
        num_layers_list = [num_layers_for_capacity(c, max_allowed_splits) for c in CAPACITIES]
        labels = [f"{c}%" for c in CAPACITIES]
        print_packing_table(chunks_dir, model, max_allowed_splits, labels, num_layers_list)

        experiments_meta.append({
            "model": model,
            "base_image": base_image,
            "max_allowed_splits": max_allowed_splits,
            "splits": split_stats(chunks_dir),
            "packing_preview": packing_preview_data(chunks_dir, labels, num_layers_list, CAPACITIES),
        })

        prepare_local_registry(base_image, registry(CFG))

        monitor = None
        if CFG.build_with_resource:
            monitor = ResourceMonitor(model, base_image, max_allowed_splits)
            monitor.start()

        results = measure_builds(model, chunks_dir, max_allowed_splits, base_image, CFG, monitor=monitor, execution_ts=execution_ts)

        if monitor:
            samples = monitor.stop()
            save_resource_csv(samples, model, base_image, execution_ts)
            plot_resource(samples, model, base_image, max_allowed_splits, execution_ts)
            plot_resource_individual(samples, model, base_image, execution_ts, max_allowed_splits)
            all_samples.extend(samples)

        save_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, max_allowed_splits, execution_ts)
        all_results.extend(results)

    if all_results:
        save_merged_csv(all_results, execution_ts)
    if all_samples:
        save_merged_resource_csv(all_samples, execution_ts)

    write_run_json(
        build_run_metadata_path(SCRIPT_DIR, execution_ts),
        execution_ts=execution_ts,
        started_at=run_started,
        config={
            "registry": registry(CFG),
            "tdfs_binary": CFG.tdfs_binary,
            "build_n_runs": CFG.build_n_runs,
            "build_cooldown": CFG.build_cooldown,
            "build_with_resource": CFG.build_with_resource,
        },
        sections={
            "modes": MODES,
            "capacities": CAPACITIES,
            "experiments": experiments_meta,
        },
    )

    clear_artifacts(SCRIPT_DIR)


if __name__ == "__main__":
    main()
