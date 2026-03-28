import csv
import os
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt

import build_2dfs as b2
import build_2dfs_stargz as b2s
import build_stargz as bs
import build_base as bb

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts")

MODEL = "openai-community/gpt2"  # ~500 MB safetensors
# MODEL = "openai-community/gpt2-medium"  # ~1.5 GB safetensors
MAX_SPLITS = 10
IS_LOCAL = True


def measure_builds(
    model: str, max_splits: int, is_local: bool = IS_LOCAL
) -> tuple[list[tuple[int, float]], list[tuple[int, float]], list[tuple[int, float]], list[tuple[int, float]]]:
    print("=== Running 2dfs builds ===")
    results_2dfs = b2.run(model, max_splits, is_local)

    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] Sleeping 60s before next mode...")
    time.sleep(60)

    print("\n=== Running 2dfs+stargz builds ===")
    results_2dfs_stargz = b2s.run(model, max_splits, is_local)

    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] Sleeping 60s before next mode...")
    time.sleep(60)

    print("\n=== Running stargz builds ===")
    results_stargz = bs.run(model, max_splits)

    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] Sleeping 60s before next mode...")
    time.sleep(60)

    print("\n=== Running base builds ===")
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
    os.makedirs(RESULTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    output_path = os.path.join(RESULTS_DIR, f"{model_slug}_splits_{len(splits)}.csv")
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
    os.makedirs(CHARTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    output_path = os.path.join(CHARTS_DIR, f"{model_slug}_splits_{len(splits)}.png")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    print(f"Chart saved to {output_path}")


def main():
    results_2dfs, results_2dfs_stargz, results_stargz, results_base = measure_builds(MODEL, MAX_SPLITS, IS_LOCAL)

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
