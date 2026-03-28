import csv
import os
import threading
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import psutil

import build_2dfs as b2
import build_2dfs_stargz as b2s
import build_stargz as bs
import build_base as bb

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")
RESULTS_BUILD_DIR = os.path.join(RESULTS_DIR, "build")
RESULTS_RESOURCE_DIR = os.path.join(RESULTS_DIR, "resource")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts")
CHARTS_BUILD_DIR = os.path.join(CHARTS_DIR, "build")
CHARTS_RESOURCE_DIR = os.path.join(CHARTS_DIR, "resource")

# MODEL = "openai-community/gpt2"  # ~500 MB safetensors
MODEL = "openai-community/gpt2-medium"  # ~1.5 GB safetensors
MAX_SPLITS = 1
IS_LOCAL = False
WITH_RESOURCE = True
SLEEP_SECONDS = 5


class ResourceMonitor:
    def __init__(self):
        self._samples: list[tuple[int, float, float, str]] = []  # (timestamp_ms, cpu%, mem%, mode)
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
            mem = psutil.virtual_memory().percent
            ts = int(time.time() * 1000)
            self._samples.append((ts, cpu, mem, self._mode))


def measure_builds(
    model: str, max_splits: int, is_local: bool = IS_LOCAL, monitor: ResourceMonitor | None = None,
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
            print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === 2dfs: {n} split(s) ===")
            elapsed = b2.run_one(model, n, is_local)
            results_2dfs.append((n, elapsed))

            monitor.set_mode("idle")
            print(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
            time.sleep(SLEEP_SECONDS)

            monitor.set_mode(f"2dfs_stargz_splits_{n}")
            print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === 2dfs+stargz: {n} split(s) ===")
            elapsed = b2s.run_one(model, n, is_local)
            results_2dfs_stargz.append((n, elapsed))

            monitor.set_mode("idle")
            print(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
            time.sleep(SLEEP_SECONDS)

            monitor.set_mode(f"stargz_splits_{n}")
            print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === stargz: {n} split(s) ===")
            elapsed = bs.run_one(model, n)
            results_stargz.append((n, elapsed))

            monitor.set_mode("idle")
            print(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
            time.sleep(SLEEP_SECONDS)

            monitor.set_mode(f"base_splits_{n}")
            print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === base: {n} split(s) ===")
            elapsed = bb.run_one(model, n)
            results_base.append((n, elapsed))

            if n < max_splits:
                monitor.set_mode("idle")
                print(f"\nSleeping {SLEEP_SECONDS}s before next split count...")
                time.sleep(SLEEP_SECONDS)

        return results_2dfs, results_2dfs_stargz, results_stargz, results_base

    # Non-monitored path: original sequential mode-by-mode execution
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Running 2dfs builds ===")
    results_2dfs = b2.run(model, max_splits, is_local)

    print(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
    time.sleep(SLEEP_SECONDS)

    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Running 2dfs+stargz builds ===")
    results_2dfs_stargz = b2s.run(model, max_splits, is_local)

    print(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
    time.sleep(SLEEP_SECONDS)

    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Running stargz builds ===")
    results_stargz = bs.run(model, max_splits)

    print(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
    time.sleep(SLEEP_SECONDS)

    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Running base builds ===")
    results_base = bb.run(model, max_splits)

    return results_2dfs, results_2dfs_stargz, results_stargz, results_base


def save_csv(
    splits: list[int],
    times_2dfs: list[float],
    times_2dfs_stargz: list[float],
    times_stargz: list[float],
    times_base: list[float],
    model: str,
) -> None:
    os.makedirs(RESULTS_BUILD_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(RESULTS_BUILD_DIR, f"{model_slug}_splits_{len(splits)}_{ts}.csv")
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["splits", "2dfs_s", "2dfs_stargz_s", "stargz_s", "base_s"])
        for row in zip(splits, times_2dfs, times_2dfs_stargz, times_stargz, times_base):
            writer.writerow([row[0], f"{row[1]:.4f}", f"{row[2]:.4f}", f"{row[3]:.4f}", f"{row[4]:.4f}"])
    print(f"Results saved to {output_path}")


def plot(
    results_2dfs: list[tuple[int, float]],
    results_2dfs_stargz: list[tuple[int, float]],
    results_stargz: list[tuple[int, float]],
    results_base: list[tuple[int, float]],
    model: str,
) -> None:
    splits = [n for n, _ in results_2dfs]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(splits, [t for _, t in results_2dfs], marker="o", label="2dfs")
    ax.plot(splits, [t for _, t in results_2dfs_stargz], marker="o", label="2dfs+stargz")
    ax.plot(splits, [t for _, t in results_stargz], marker="o", label="stargz")
    ax.plot(splits, [t for _, t in results_base], marker="o", label="base")
    ax.set_xlabel("Number of splits")
    ax.set_ylabel("Build time (s)")
    ax.set_title(f"tdfs build performance — {model}")
    ax.set_xticks(splits)
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.5)
    os.makedirs(CHARTS_BUILD_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(CHARTS_BUILD_DIR, f"{model_slug}_splits_{len(splits)}_{ts}.png")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    print(f"Chart saved to {output_path}")


def save_resource_csv(
    samples: list[tuple[int, float, float, str]], model: str, max_splits: int,
) -> None:
    os.makedirs(RESULTS_RESOURCE_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(RESULTS_RESOURCE_DIR, f"{model_slug}_resource_splits_{max_splits}_{ts}.csv")
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_ms", "cpu_percent", "mem_percent", "mode"])
        for row in samples:
            writer.writerow(row)
    print(f"Resource CSV saved to {output_path}")


def plot_resource(
    samples: list[tuple[int, float, float, str]], model: str, max_splits: int,
) -> None:
    if not samples:
        return

    # Collect all non-idle modes that appear in the data
    seen_modes = []
    for _, _, _, m in samples:
        if m != "idle" and m not in seen_modes:
            seen_modes.append(m)

    base_colors = {"2dfs": "#1f77b4", "2dfs_stargz": "#ff7f0e", "stargz": "#2ca02c", "base": "#d62728"}
    base_labels = {"2dfs": "2dfs", "2dfs_stargz": "2dfs+stargz", "stargz": "stargz", "base": "base"}

    t0 = samples[0][0]

    fig, (ax_cpu, ax_mem) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    for mode in seen_modes:
        mode_samples = [(ts, cpu, mem) for ts, cpu, mem, m in samples if m == mode]
        if not mode_samples:
            continue

        # Parse base tool name and split count from e.g. "2dfs_stargz_splits_3"
        parts = mode.rsplit("_splits_", 1)
        base = parts[0]
        n_splits = parts[1] if len(parts) == 2 else "?"
        color = base_colors.get(base, "#888888")
        label = f"{base_labels.get(base, base)} ({n_splits} splits)"

        elapsed = [(ts - t0) / 1000 for ts, _, _ in mode_samples]
        cpus = [cpu for _, cpu, _ in mode_samples]
        mems = [mem for _, _, mem in mode_samples]
        ax_cpu.plot(elapsed, cpus, label=label, color=color, alpha=0.4 + 0.6 * int(n_splits) / 10 if n_splits.isdigit() else 1, linewidth=1)
        ax_mem.plot(elapsed, mems, label=label, color=color, alpha=0.4 + 0.6 * int(n_splits) / 10 if n_splits.isdigit() else 1, linewidth=1)

    ax_cpu.set_ylabel("CPU Usage (%)")
    ax_cpu.set_title(f"Resource usage during builds — {model}")
    ax_cpu.legend(fontsize="small", ncol=2)
    ax_cpu.grid(True, linestyle="--", alpha=0.5)

    ax_mem.set_xlabel("Elapsed time (s)")
    ax_mem.set_ylabel("Memory Usage (%)")
    ax_mem.legend(fontsize="small", ncol=2)
    ax_mem.grid(True, linestyle="--", alpha=0.5)

    os.makedirs(CHARTS_RESOURCE_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(CHARTS_RESOURCE_DIR, f"{model_slug}_resource_splits_{max_splits}_{ts}.png")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    print(f"Resource chart saved to {output_path}")


def main():
    monitor = None
    if WITH_RESOURCE:
        monitor = ResourceMonitor()
        monitor.start()

    results_2dfs, results_2dfs_stargz, results_stargz, results_base = measure_builds(
        MODEL, MAX_SPLITS, IS_LOCAL, monitor=monitor,
    )

    if monitor:
        samples = monitor.stop()
        save_resource_csv(samples, MODEL, MAX_SPLITS)
        plot_resource(samples, MODEL, MAX_SPLITS)

    splits = [n for n, _ in results_2dfs]
    times_2dfs = [t for _, t in results_2dfs]
    times_2dfs_stargz = [t for _, t in results_2dfs_stargz]
    times_stargz = [t for _, t in results_stargz]
    times_base = [t for _, t in results_base]

    print("\n=== Comparison ===")
    print(f"{'splits':>8}  {'2dfs (s)':>12}  {'2dfs+stargz (s)':>16}  {'stargz (s)':>12}  {'base (s)':>10}")
    print("-" * 68)
    for n, t1, t2, t3, t4 in zip(splits, times_2dfs, times_2dfs_stargz, times_stargz, times_base):
        print(f"{n:>8}  {t1:>12.2f}  {t2:>16.2f}  {t3:>12.2f}  {t4:>10.2f}")

    save_csv(splits, times_2dfs, times_2dfs_stargz, times_stargz, times_base, MODEL)
    plot(results_2dfs, results_2dfs_stargz, results_stargz, results_base, MODEL)


if __name__ == "__main__":
    main()
