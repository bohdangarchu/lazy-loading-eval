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
    resource_cores_charts_run_dir, resource_disk_charts_run_dir,
    build_artifacts_dir,
    build_merged_csv_path, resource_merged_csv_path,
    build_run_metadata_path,
)
from shared.artifacts import snapshot_artifacts, clear_artifacts
from shared.config import load_config, build_tmpdir
from shared.fs import physical_device
from shared.charts import MODE_COLORS, figure_footer, bar_group_xticks, save_figure, write_csv
from shared.registry import prepare_local_registry, registry, image_slug
from shared.services import ensure_buildkit, prune_buildkit, clear_2dfs_cache
from shared.run_metadata import write_run_json
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_2dfs_stargz_zstd as b2sz
from build_performance import build_stargz as bs
from build_performance import build_base as bb
from shared.split_llm import layers_for_percent
from build_performance.prepare import (
    generate_build_artifacts, prepare_model_splits, print_packing_table,
    packing_preview_data, split_stats,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    # ("openai-community/gpt2",        "docker.io/library/python:3.12-slim"),  # ~0.5GB     ~50 MB
    # ("Qwen/Qwen2-1.5B",              "docker.io/library/python:3.12-slim"),  # ~3.09 GB     ~3.4 GB
    ("EleutherAI/pythia-1.4b", "docker.io/library/python:3.12-slim"), # 3 GB
    # ("openlm-research/open_llama_3b", "docker.io/library/python:3.12-slim")    # ~6.0 GB     ~3.4 GB
    # ("openlm-research/open_llama_7b", "docker.io/ollama/ollama")                # 14 GB
]
CFG = load_config()
VERBOSE = False
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
CAPACITIES = [0, 25, 50, 75, 100]
BUILD_SCHEMA_VERSION = 2
RESOURCE_SCHEMA_VERSION = 4

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
    pull_s: float
    build_s: float
    export_s: float


@dataclass
class RunSamples:
    """Derived per-window rates collected during one (capacity, mode, run) window."""
    cpu: list[float] = field(default_factory=list)
    mem: list[float] = field(default_factory=list)
    disk_read_mb: list[float] = field(default_factory=list)
    disk_write_mb: list[float] = field(default_factory=list)
    disk_read_iops: list[float] = field(default_factory=list)
    disk_write_iops: list[float] = field(default_factory=list)
    disk_util: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class ResourceRow:
    """One sample. Disk fields are RAW cumulative-since-boot counters; all rates,
    util% and await are derived at plot/analysis time (see derive_samples)."""
    schema_version: int
    model: str
    base_image: str
    max_allowed_splits: int
    timestamp_ms: int
    cpu_percent: float
    cpu_per_core: str
    mem_mb: float
    disk_read_bytes: int
    disk_write_bytes: int
    disk_read_count: int
    disk_write_count: int
    disk_read_time_ms: int
    disk_write_time_ms: int
    disk_busy_time_ms: int
    mode: str
    capacity: int | None
    run: int | None


@dataclass
class DiskRates:
    """Rates derived from the delta between two consecutive raw ResourceRow counters."""
    read_mb_s: float = 0.0
    write_mb_s: float = 0.0
    read_iops: float = 0.0
    write_iops: float = 0.0
    util_pct: float = 0.0  # busy_time -> wall fraction the device was active (<=100%)


@dataclass
class DerivedSample:
    """A raw ResourceRow paired with the disk rates derived against the previous sample."""
    row: ResourceRow
    disk: DiskRates


def derive_samples(samples: list[ResourceRow]) -> list[DerivedSample]:
    """Attach per-window disk rates to each row, deriving from consecutive raw
    counters over the full continuous capture (so no window loses its first point)."""
    ordered = sorted(samples, key=lambda r: r.timestamp_ms)
    out: list[DerivedSample] = []
    prev: ResourceRow | None = None
    for row in ordered:
        rates = DiskRates()
        if prev is not None:
            dt = (row.timestamp_ms - prev.timestamp_ms) / 1000.0
            if dt > 0:
                def rate(attr: str) -> float:
                    return (getattr(row, attr) - getattr(prev, attr)) / dt
                rates = DiskRates(
                    read_mb_s=rate("disk_read_bytes") / (1024 * 1024),
                    write_mb_s=rate("disk_write_bytes") / (1024 * 1024),
                    read_iops=rate("disk_read_count"),
                    write_iops=rate("disk_write_count"),
                    util_pct=rate("disk_busy_time_ms") / 1000 * 100,
                )
        out.append(DerivedSample(row=row, disk=rates))
        prev = row
    return out


class ResourceMonitor:
    def __init__(self, model: str, base_image: str, max_allowed_splits: int, tmpdir: str):
        self._samples: list[ResourceRow] = []
        self._model = model
        self._base_image = base_image
        self._max_allowed_splits = max_allowed_splits
        self._mode: str = "idle"
        self._capacity: int | None = None
        self._run: int | None = None
        self._stop = threading.Event()
        # builds stage through this TMPDIR; sample the physical disk backing it
        self._disk_dev = physical_device(tmpdir)
        if self._disk_dev:
            log.info(f"Disk metrics sampling physical device: {self._disk_dev}")
        else:
            log.info("Disk device detection failed; disk util% is whole-system sum (may exceed 100%)")

    def _disk_counters(self):
        if self._disk_dev:
            return psutil.disk_io_counters(perdisk=True).get(self._disk_dev)
        return psutil.disk_io_counters()

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
            # percpu sample blocks for the interval; aggregate cpu = mean of cores
            per_core = psutil.cpu_percent(interval=1, percpu=True)
            cpu = sum(per_core) / len(per_core) if per_core else 0.0
            mem = psutil.virtual_memory().used / (1024 * 1024)  # MB

            # record RAW cumulative counters; rates/util/await derived at plot time
            disk = self._disk_counters()
            ts = int(time.time() * 1000)
            self._samples.append(ResourceRow(
                schema_version=RESOURCE_SCHEMA_VERSION,
                model=self._model,
                base_image=self._base_image,
                max_allowed_splits=self._max_allowed_splits,
                timestamp_ms=ts,
                cpu_percent=cpu,
                cpu_per_core="|".join(f"{c:.1f}" for c in per_core),
                mem_mb=mem,
                disk_read_bytes=disk.read_bytes if disk else 0,
                disk_write_bytes=disk.write_bytes if disk else 0,
                disk_read_count=disk.read_count if disk else 0,
                disk_write_count=disk.write_count if disk else 0,
                disk_read_time_ms=disk.read_time if disk else 0,
                disk_write_time_ms=disk.write_time if disk else 0,
                disk_busy_time_ms=disk.busy_time if disk else 0,
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
            num_layers = layers_for_percent(max_allowed_splits, cap)
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
                    schema_version=BUILD_SCHEMA_VERSION,
                    model=model,
                    base_image=source_image,
                    max_allowed_splits=max_allowed_splits,
                    run=run,
                    capacity=cap,
                    num_layers=num_layers,
                    mode=mode,
                    total_s=br.total_s,
                    pull_s=br.pull_s,
                    build_s=br.build_s,
                    export_s=br.export_s,
                ))

                is_last = (i == len(MODES) - 1) and (cap == CAPACITIES[-1]) and (run == cfg.build_n_runs - 1)
                if not is_last:
                    log.info(f"\nSleeping {cfg.build_cooldown}s before next...")
                    time.sleep(cfg.build_cooldown)

    return results


def _write_build_rows(output_path: str, results: list[BuildRow]) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = [f.name for f in fields(BuildRow)]
    rows = [
        {
            **asdict(r),
            "total_s": f"{r.total_s:.4f}",
            "pull_s": f"{r.pull_s:.4f}",
            "build_s": f"{r.build_s:.4f}",
            "export_s": f"{r.export_s:.4f}",
        }
        for r in results
    ]
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


def _bar_stats(by_cap_mode, capacities, mode: str, attr: str):
    """Per-capacity (mean-of-run-medians, std) for one RunSamples attribute."""
    heights, errs = [], []
    for cap in capacities:
        runs = by_cap_mode.get((cap, mode), {})
        medians = [float(np.median(getattr(rs, attr))) for rs in runs.values() if getattr(rs, attr)]
        heights.append(float(np.mean(medians)) if medians else 0.0)
        errs.append(float(np.std(medians, ddof=0)) if medians else 0.0)
    return heights, errs


def _grouped_bars(ax, x, capacities, x_labels, series, ylabel,
                  xlabel=None, ylim=None, title=None) -> None:
    """series: list of dicts with keys label, color, hatch, heights, errs."""
    n = len(series)
    width = 0.8 / n
    for i, s in enumerate(series):
        offsets = [pos + i * width for pos in x]
        ax.bar(offsets, s["heights"], width, yerr=s["errs"], label=s["label"],
               color=s["color"], hatch=s["hatch"], edgecolor="black", linewidth=0.5,
               error_kw={"ecolor": "black", "capsize": 3, "elinewidth": 1})
    bar_group_xticks(ax, len(capacities), n, width, x_labels)
    ax.set_ylabel(ylabel)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylim:
        ax.set_ylim(*ylim)
    if title:
        ax.set_title(title)
    ax.legend(fontsize="small")
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")


def plot_resource(
    samples: list[DerivedSample],
    model: str, base_image: str, max_allowed_splits: int, execution_ts: str,
) -> None:
    if not samples:
        return

    colors = {mode: MODE_COLORS[mode] for mode in MODES}

    # (capacity, mode) -> {run_index: RunSamples}
    by_cap_mode: dict[tuple[int, str], dict[int, RunSamples]] = defaultdict(
        lambda: defaultdict(RunSamples)
    )

    for s in samples:
        r = s.row
        if r.mode == "idle" or r.capacity is None or r.run is None:
            continue
        bucket = by_cap_mode[(r.capacity, r.mode)][r.run]
        bucket.cpu.append(r.cpu_percent)
        bucket.mem.append(r.mem_mb)
        bucket.disk_read_mb.append(s.disk.read_mb_s)
        bucket.disk_write_mb.append(s.disk.write_mb_s)
        bucket.disk_read_iops.append(s.disk.read_iops)
        bucket.disk_write_iops.append(s.disk.write_iops)
        bucket.disk_util.append(s.disk.util_pct)

    capacities = sorted({cap for (cap, _) in by_cap_mode.keys()})
    if not capacities:
        return

    x_labels = [f"{c}" for c in capacities]
    x = range(len(capacities))

    # read = solid fill, write = "//" hatch; color encodes mode. util = total only.
    cpu_series, mem_series, tput_series, util_series, iops_series = [], [], [], [], []
    for mode in MODES:
        color = colors[mode]

        def stat(attr: str):
            return _bar_stats(by_cap_mode, capacities, mode, attr)

        def entry(label, attr, hatch=None):
            heights, errs = stat(attr)
            return {"label": label, "color": color, "hatch": hatch, "heights": heights, "errs": errs}

        cpu_series.append(entry(mode, "cpu"))
        mem_series.append(entry(mode, "mem"))
        tput_series.append(entry(f"{mode} read", "disk_read_mb"))
        tput_series.append(entry(f"{mode} write", "disk_write_mb", hatch="//"))
        util_series.append(entry(f"{mode} total", "disk_util"))
        iops_series.append(entry(f"{mode} read", "disk_read_iops"))
        iops_series.append(entry(f"{mode} write", "disk_write_iops", hatch="//"))

    fig, (ax_cpu, ax_mem, ax_tput, ax_util, ax_iops) = plt.subplots(
        5, 1, figsize=(max(8, len(capacities) * 2), 18))

    _grouped_bars(ax_cpu, x, capacities, x_labels, cpu_series, "CPU Usage (%)",
                  title=f"Resource usage during builds (mean ± std, n={CFG.build_n_runs} runs)")
    _grouped_bars(ax_mem, x, capacities, x_labels, mem_series, "Memory Usage (MB)")
    _grouped_bars(ax_tput, x, capacities, x_labels, tput_series, "Disk throughput (MB/s)")
    _grouped_bars(ax_util, x, capacities, x_labels, util_series, "Disk util (%)")
    _grouped_bars(ax_iops, x, capacities, x_labels, iops_series, "Disk IOPS (ops/s)",
                  xlabel="Split capacity (%)")

    output_path = resource_chart_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.tight_layout()
    figure_footer(fig, model, base_image, max_allowed_splits=max_allowed_splits)
    save_figure(fig, output_path)


def plot_resource_individual(
    samples: list[DerivedSample],
    model: str, base_image: str, execution_ts: str, max_allowed_splits: int,
) -> None:
    if not samples:
        return

    series: dict[tuple[str, int, int], list[DerivedSample]] = defaultdict(list)

    def _per_core(per_core: str) -> list[float]:
        return [float(c) for c in per_core.split("|") if c]

    for s in samples:
        r = s.row
        if r.mode == "idle" or r.capacity is None or r.run is None:
            continue
        series[(r.mode, r.capacity, r.run)].append(s)

    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    cpu_dir = resource_cpu_charts_run_dir(SCRIPT_DIR, execution_ts)
    ram_dir = resource_ram_charts_run_dir(SCRIPT_DIR, execution_ts)
    cores_dir = resource_cores_charts_run_dir(SCRIPT_DIR, execution_ts)
    disk_dir = resource_disk_charts_run_dir(SCRIPT_DIR, execution_ts)
    os.makedirs(cpu_dir, exist_ok=True)
    os.makedirs(ram_dir, exist_ok=True)
    os.makedirs(cores_dir, exist_ok=True)
    os.makedirs(disk_dir, exist_ok=True)

    for (mode_name, cap, run), samps in sorted(series.items()):
        samps.sort(key=lambda s: s.row.timestamp_ms)
        rows = [s.row for s in samps]
        t0 = rows[0].timestamp_ms
        t_sec = [(r.timestamp_ms - t0) / 1000.0 for r in rows]
        cpu_vals = [r.cpu_percent for r in rows]
        mem_vals = [r.mem_mb for r in rows]
        per_core_rows = [_per_core(r.cpu_per_core) for r in rows]

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

        # per-core heatmap: rows = cores, cols = time; truncate to common core count
        ncores = min((len(r) for r in per_core_rows), default=0)
        if ncores > 0 and len(t_sec) > 1:
            heat = np.array([r[:ncores] for r in per_core_rows]).T  # [core, time]
            fig, ax = plt.subplots(figsize=(8, max(3, ncores * 0.18)))
            im = ax.imshow(
                heat, aspect="auto", origin="lower", cmap="inferno",
                vmin=0, vmax=100,
                extent=(t_sec[0], t_sec[-1], -0.5, ncores - 0.5),
            )
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Core index")
            ax.set_title("Per-core CPU usage over time")
            fig.colorbar(im, ax=ax, label="CPU (%)")
            fig.tight_layout()
            _add_run_footer(fig)
            save_figure(fig, os.path.join(cores_dir, f"{file_stem}.png"), log_path=False)

        # disk over time: throughput (read/write), util (total only), IOPS (read/write)
        read_c, write_c, total_c = "#1f77b4", "#d62728", "#999999"
        disk = [s.disk for s in samps]
        fig, (ax_t, ax_u, ax_i) = plt.subplots(3, 1, figsize=(8, 9), sharex=True)
        ax_t.plot(t_sec, [d.read_mb_s for d in disk], color=read_c, linewidth=1, label="read")
        ax_t.plot(t_sec, [d.write_mb_s for d in disk], color=write_c, linewidth=1, label="write")
        ax_t.set_ylabel("Throughput (MB/s)")
        ax_t.set_title("Disk activity over time")

        ax_u.plot(t_sec, [d.util_pct for d in disk], color=total_c, linewidth=1.5, label="total")
        ax_u.set_ylabel("Util (%)")

        ax_i.plot(t_sec, [d.read_iops for d in disk], color=read_c, linewidth=1, label="read")
        ax_i.plot(t_sec, [d.write_iops for d in disk], color=write_c, linewidth=1, label="write")
        ax_i.set_ylabel("IOPS (ops/s)")
        ax_i.set_xlabel("Time (s)")

        for axis in (ax_t, ax_u, ax_i):
            axis.grid(True, linestyle="--", alpha=0.5)
            axis.legend(fontsize="small")
        fig.tight_layout()
        _add_run_footer(fig)
        save_figure(fig, os.path.join(disk_dir, f"{file_stem}.png"), log_path=False)

    log.result(f"Per-run CPU charts saved to {cpu_dir}/")
    log.result(f"Per-run RAM charts saved to {ram_dir}/")
    log.result(f"Per-run per-core heatmaps saved to {cores_dir}/")
    log.result(f"Per-run disk charts saved to {disk_dir}/")


def main():
    log.set_verbose(VERBOSE)
    ensure_buildkit()
    prune_buildkit()
    clear_2dfs_cache(CFG)
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_started = datetime.now(timezone.utc)

    all_results: list[BuildRow] = []
    all_samples: list[ResourceRow] = []
    experiments_meta: list[dict] = []
    for model, base_image in EXPERIMENTS:
        chunks_dir, max_allowed_splits = prepare_model_splits(model)
        log.result(f"\n===== Experiment: {model} / {base_image} (max_allowed_splits={max_allowed_splits}) =====")

        generate_build_artifacts(chunks_dir, max_allowed_splits, base_image, CFG)
        num_layers_list = [layers_for_percent(max_allowed_splits, c) for c in CAPACITIES]
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
            monitor = ResourceMonitor(model, base_image, max_allowed_splits, build_tmpdir(CFG))
            monitor.start()

        results = measure_builds(model, chunks_dir, max_allowed_splits, base_image, CFG, monitor=monitor, execution_ts=execution_ts)

        if monitor:
            samples = monitor.stop()
            save_resource_csv(samples, model, base_image, execution_ts)  # raw counters
            enriched = derive_samples(samples)
            plot_resource(enriched, model, base_image, max_allowed_splits, execution_ts)
            plot_resource_individual(enriched, model, base_image, execution_ts, max_allowed_splits)
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
