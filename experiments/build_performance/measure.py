import csv
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import numpy as np
import psutil

from shared import log
from shared.registry import prepare_local_registry, registry, image_slug
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_stargz as bs
from build_performance import build_base as bb

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")
RESULTS_BUILD_DIR = os.path.join(RESULTS_DIR, "build")
RESULTS_RESOURCE_DIR = os.path.join(RESULTS_DIR, "resource")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts")
CHARTS_BUILD_DIR = os.path.join(CHARTS_DIR, "build")
CHARTS_RESOURCE_DIR = os.path.join(CHARTS_DIR, "resource")

MODEL = "openai-community/gpt2"  # ~500 MB safetensors
# MODEL = "openai-community/gpt2-medium"  # ~1.5 GB safetensors
BASE_IMAGE = "docker.io/library/python:3.12-slim"
MAX_SPLITS = 2
IS_LOCAL = True
WITH_RESOURCE = False
VERBOSE = False
SLEEP_SECONDS = 5


class ResourceMonitor:
    def __init__(self):
        self._samples: list[tuple[int, float, float, str]] = []  # (timestamp_ms, cpu%, mem_mb, mode)
        self._mode = "idle"
        self._stop = threading.Event()

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self) -> list[tuple[int, float, float, str]]:
        self._stop.set()
        self._thread.join()
        return self._samples

    def _poll(self) -> None:
        while not self._stop.is_set():
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().used / (1024 * 1024)  # MB
            ts = int(time.time() * 1000)
            self._samples.append((ts, cpu, mem, self._mode))


def measure_builds(
    model: str, max_splits: int, source_image: str, is_local: bool = IS_LOCAL,
    monitor: ResourceMonitor | None = None,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]], list[tuple[int, float]], list[tuple[int, float]]]:
    if monitor:
        results_2dfs = []
        results_2dfs_stargz = []
        results_stargz = []
        results_base = []

        b2.clear_cache(is_local)
        b2s.clear_cache(is_local)
        bs.clear_cache()
        bb.clear_cache()

        for n in range(1, max_splits + 1):
            monitor.set_mode(f"2dfs_splits_{n}")
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === 2dfs: {n} split(s) ===")
            elapsed = b2.run_one(model, n, is_local, source_image)
            results_2dfs.append((n, elapsed))

            monitor.set_mode("idle")
            log.info(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
            time.sleep(SLEEP_SECONDS)

            monitor.set_mode(f"2dfs_stargz_splits_{n}")
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === 2dfs+stargz: {n} split(s) ===")
            elapsed = b2s.run_one(model, n, is_local, source_image)
            results_2dfs_stargz.append((n, elapsed))

            monitor.set_mode("idle")
            log.info(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
            time.sleep(SLEEP_SECONDS)

            monitor.set_mode(f"stargz_splits_{n}")
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === stargz: {n} split(s) ===")
            elapsed = bs.run_one(model, n, source_image)
            results_stargz.append((n, elapsed))

            monitor.set_mode("idle")
            log.info(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
            time.sleep(SLEEP_SECONDS)

            monitor.set_mode(f"base_splits_{n}")
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === base: {n} split(s) ===")
            elapsed = bb.run_one(model, n, source_image)
            results_base.append((n, elapsed))

            if n < max_splits:
                monitor.set_mode("idle")
                log.info(f"\nSleeping {SLEEP_SECONDS}s before next split count...")
                time.sleep(SLEEP_SECONDS)

        return results_2dfs, results_2dfs_stargz, results_stargz, results_base

    # Non-monitored path: original sequential mode-by-mode execution
    log.info(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Running 2dfs builds ===")
    results_2dfs = b2.run(model, max_splits, is_local, source_image)

    log.info(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
    time.sleep(SLEEP_SECONDS)

    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Running 2dfs+stargz builds ===")
    results_2dfs_stargz = b2s.run(model, max_splits, is_local, source_image)

    log.info(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
    time.sleep(SLEEP_SECONDS)

    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Running stargz builds ===")
    results_stargz = bs.run(model, max_splits, source_image)

    log.info(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
    time.sleep(SLEEP_SECONDS)

    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Running base builds ===")
    results_base = bb.run(model, max_splits, source_image)

    return results_2dfs, results_2dfs_stargz, results_stargz, results_base



def save_csv(
    splits: list[int],
    times_2dfs: list[float],
    times_2dfs_stargz: list[float],
    times_stargz: list[float],
    times_base: list[float],
    model: str,
    base_image: str,
) -> None:
    os.makedirs(RESULTS_BUILD_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(RESULTS_BUILD_DIR, f"{model_slug}_{img_slug}_splits_{len(splits)}_{ts}.csv")
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["splits", "2dfs_s", "2dfs_stargz_s", "stargz_s", "base_s"])
        for row in zip(splits, times_2dfs, times_2dfs_stargz, times_stargz, times_base):
            writer.writerow([row[0], f"{row[1]:.4f}", f"{row[2]:.4f}", f"{row[3]:.4f}", f"{row[4]:.4f}"])
    log.result(f"Results saved to {output_path}")


def plot(
    results_2dfs: list[tuple[int, float]],
    results_2dfs_stargz: list[tuple[int, float]],
    results_stargz: list[tuple[int, float]],
    results_base: list[tuple[int, float]],
    model: str,
    base_image: str,
) -> None:
    splits = [n for n, _ in results_2dfs]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(splits, [t for _, t in results_2dfs], marker="o", label="2dfs")
    ax.plot(splits, [t for _, t in results_2dfs_stargz], marker="o", label="2dfs+stargz")
    ax.plot(splits, [t for _, t in results_stargz], marker="o", label="stargz")
    ax.plot(splits, [t for _, t in results_base], marker="o", label="base")
    ax.set_xlabel("Number of splits")
    ax.set_ylabel("Build time (s)")
    ax.set_title("tdfs build performance")
    ax.set_xticks(splits)
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.5)
    fig.text(0.01, 0.01, f"model: {model}\nbase image: {base_image}",
             fontsize=8, verticalalignment="bottom", family="monospace")
    os.makedirs(CHARTS_BUILD_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(CHARTS_BUILD_DIR, f"{model_slug}_{img_slug}_splits_{len(splits)}_{ts}.png")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    log.result(f"Chart saved to {output_path}")


def save_resource_csv(
    samples: list[tuple[int, float, float, str]], model: str, max_splits: int,
    base_image: str,
) -> None:
    os.makedirs(RESULTS_RESOURCE_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(RESULTS_RESOURCE_DIR, f"{model_slug}_{img_slug}_resource_splits_{max_splits}_{ts}.csv")
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_ms", "cpu_percent", "mem_mb", "mode"])
        for row in samples:
            writer.writerow(row)
    log.result(f"Resource CSV saved to {output_path}")


def plot_resource(
    samples: list[tuple[int, float, float, str]], model: str, max_splits: int,
    base_image: str,
) -> None:
    if not samples:
        return

    modes_order = ["2dfs", "2dfs_stargz", "stargz", "base"]
    colors = {"2dfs": "#1f77b4", "2dfs_stargz": "#ff7f0e", "stargz": "#2ca02c", "base": "#d62728"}
    labels = {"2dfs": "2dfs", "2dfs_stargz": "2dfs+stargz", "stargz": "stargz", "base": "base"}

    # Compute mean CPU and MEM per (base_mode, n_splits), skipping idle
    cpu_by_split: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    mem_by_split: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for _, cpu, mem, mode in samples:
        if mode == "idle":
            continue
        parts = mode.rsplit("_splits_", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        base = parts[0]
        n = int(parts[1])
        cpu_by_split[n][base].append(cpu)
        mem_by_split[n][base].append(mem)

    split_counts = sorted(cpu_by_split.keys())
    if not split_counts:
        return

    x_labels = [str(n) for n in split_counts]
    x = range(len(split_counts))
    n_modes = len(modes_order)
    bar_width = 0.8 / n_modes

    fig, (ax_cpu, ax_mem) = plt.subplots(2, 1, figsize=(max(8, len(split_counts) * 2), 8))

    for i, mode in enumerate(modes_order):
        cpu_means = []
        mem_means = []
        cpu_stds = []
        mem_stds = []
        for n in split_counts:
            cpu_vals = cpu_by_split[n].get(mode, [])
            mem_vals = mem_by_split[n].get(mode, [])
            cpu_means.append(np.mean(cpu_vals) if cpu_vals else 0)
            mem_means.append(np.mean(mem_vals) if mem_vals else 0)
            cpu_stds.append(np.std(cpu_vals) if cpu_vals else 0)
            mem_stds.append(np.std(mem_vals) if mem_vals else 0)

        offsets = [pos + i * bar_width for pos in x]
        ax_cpu.bar(offsets, cpu_means, bar_width, yerr=cpu_stds, label=labels[mode],
                   color=colors[mode], edgecolor="black", linewidth=0.5, capsize=3)
        ax_mem.bar(offsets, mem_means, bar_width, yerr=mem_stds, label=labels[mode],
                   color=colors[mode], edgecolor="black", linewidth=0.5, capsize=3)

    center_offset = (n_modes - 1) * bar_width / 2
    ax_cpu.set_xticks([pos + center_offset for pos in x])
    ax_cpu.set_xticklabels(x_labels)
    ax_cpu.set_ylabel("CPU Usage (%)")
    ax_cpu.set_title("Resource usage during builds")
    ax_cpu.legend(fontsize="small")
    ax_cpu.grid(True, linestyle="--", alpha=0.5, axis="y")

    ax_mem.set_xticks([pos + center_offset for pos in x])
    ax_mem.set_xticklabels(x_labels)
    ax_mem.set_xlabel("Number of splits")
    ax_mem.set_ylabel("Memory Usage (MB)")
    ax_mem.legend(fontsize="small")
    ax_mem.grid(True, linestyle="--", alpha=0.5, axis="y")

    fig.text(0.01, 0.01, f"model: {model}\nbase image: {base_image}",
             fontsize=8, verticalalignment="bottom", family="monospace")

    os.makedirs(CHARTS_RESOURCE_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(CHARTS_RESOURCE_DIR, f"{model_slug}_{img_slug}_resource_splits_{max_splits}_{ts}.png")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    log.result(f"Resource chart saved to {output_path}")


def main():
    log.set_verbose(VERBOSE)

    prepare_local_registry(BASE_IMAGE, registry(IS_LOCAL))

    monitor = None
    if WITH_RESOURCE:
        monitor = ResourceMonitor()
        monitor.start()

    results_2dfs, results_2dfs_stargz, results_stargz, results_base = measure_builds(
        MODEL, MAX_SPLITS, BASE_IMAGE, IS_LOCAL, monitor=monitor,
    )

    if monitor:
        samples = monitor.stop()
        save_resource_csv(samples, MODEL, MAX_SPLITS, BASE_IMAGE)
        plot_resource(samples, MODEL, MAX_SPLITS, BASE_IMAGE)

    splits = [n for n, _ in results_2dfs]
    times_2dfs = [t for _, t in results_2dfs]
    times_2dfs_stargz = [t for _, t in results_2dfs_stargz]
    times_stargz = [t for _, t in results_stargz]
    times_base = [t for _, t in results_base]

    log.result("\n=== Comparison ===")
    log.result(f"{'splits':>8}  {'2dfs (s)':>12}  {'2dfs+stargz (s)':>16}  {'stargz (s)':>12}  {'base (s)':>10}")
    log.result("-" * 68)
    for n, t1, t2, t3, t4 in zip(splits, times_2dfs, times_2dfs_stargz, times_stargz, times_base):
        log.result(f"{n:>8}  {t1:>12.2f}  {t2:>16.2f}  {t3:>12.2f}  {t4:>10.2f}")

    save_csv(splits, times_2dfs, times_2dfs_stargz, times_stargz, times_base, MODEL, BASE_IMAGE)
    plot(results_2dfs, results_2dfs_stargz, results_stargz, results_base, MODEL, BASE_IMAGE)


if __name__ == "__main__":
    main()
