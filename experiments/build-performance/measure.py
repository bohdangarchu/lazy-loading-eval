import os

import matplotlib.pyplot as plt

import build_2dfs as b2
import build_2dfs_stargz as b2s
import build_stargz as bs

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")

# MODEL = "openai-community/gpt2"  # ~500 MB safetensors
MODEL = "openai-community/gpt2-medium"  # ~1.5 GB safetensors
MAX_SPLITS = 10


def measure_builds(
    model: str, max_splits: int
) -> tuple[list[tuple[int, float]], list[tuple[int, float]], list[tuple[int, float]]]:
    print("=== Running 2dfs builds ===")
    results_2dfs = b2.run(model, max_splits)

    print("\n=== Running 2dfs+stargz builds ===")
    results_2dfs_stargz = b2s.run(model, max_splits)

    print("\n=== Running stargz builds ===")
    results_stargz = bs.run(model, max_splits)

    return results_2dfs, results_2dfs_stargz, results_stargz


def plot(
    results_2dfs: list[tuple[int, float]],
    results_2dfs_stargz: list[tuple[int, float]],
    results_stargz: list[tuple[int, float]],
    model: str,
) -> None:
    splits = [n for n, _ in results_2dfs]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(splits, [t for _, t in results_2dfs], marker="o", label="2dfs")
    ax.plot(splits, [t for _, t in results_2dfs_stargz], marker="o", label="2dfs+stargz")
    ax.plot(splits, [t for _, t in results_stargz], marker="o", label="stargz")
    ax.set_xlabel("Number of splits")
    ax.set_ylabel("Build time (s)")
    ax.set_title(f"tdfs build performance — {model}")
    ax.set_xticks(splits)
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.5)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    output_path = os.path.join(RESULTS_DIR, f"{model_slug}_splits_{len(splits)}.png")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    print(f"\nChart saved to {output_path}")


def main():
    results_2dfs, results_2dfs_stargz, results_stargz = measure_builds(MODEL, MAX_SPLITS)

    splits = [n for n, _ in results_2dfs]
    times_2dfs = [t for _, t in results_2dfs]
    times_2dfs_stargz = [t for _, t in results_2dfs_stargz]
    times_stargz = [t for _, t in results_stargz]

    print("\n=== Comparison ===")
    print(f"{'splits':>8}  {'2dfs (s)':>12}  {'2dfs+stargz (s)':>16}  {'stargz (s)':>12}")
    print("-" * 56)
    for n, t1, t2, t3 in zip(splits, times_2dfs, times_2dfs_stargz, times_stargz):
        print(f"{n:>8}  {t1:>12.2f}  {t2:>16.2f}  {t3:>12.2f}")

    plot(results_2dfs, results_2dfs_stargz, results_stargz, MODEL)


if __name__ == "__main__":
    main()
