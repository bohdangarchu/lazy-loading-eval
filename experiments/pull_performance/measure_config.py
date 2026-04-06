import csv
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone

import matplotlib.pyplot as plt

from shared import log
from shared.artifacts import write_2dfs_json
from shared.config import load_config
from shared.registry import (
    prepare_local_registry, registry, image_slug,
    stargz_base_image, zstd_base_image, tdfs_cmd,
)
from pull_performance.measure import clear_cache, _timed_pull, _timed_run, _run_cmd
from pull_performance.prepare import _clear_2dfs_cache, prepare_chunks

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", "config")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts", "config")

EXPERIMENTS = [
    ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5 GB     ~50 MB
    # ("openai-community/gpt2-medium", "docker.io/tensorflow/tensorflow"),     # ~1.52 GB    ~700 MB
    # ("openai-community/gpt2-large", "docker.io/ollama/ollama"),              # ~3.25 GB    ~3.4 GB
    # ("openai-community/gpt2-xl", "docker.io/library/python:3.12-slim"),      # ~6.0 GB     ~50 MB
    ("openai-community/gpt2-medium", "docker.io/library/python:3.12-slim"),    # ~1.52 GB    ~50 MB
    ("openai-community/gpt2-large", "docker.io/library/python:3.12-slim"),     # ~3.25 GB    ~50 MB
]
NUM_SPLITS = 10
BASE_SPLITS = [2, 4, 6, 8, 10]
CFG = load_config()
VERBOSE = True
MODE = "2dfs-stargz"  # or "2dfs-stargz-zstd"
FLAG_OPTIONS: list[tuple[str, str]] = [
    ("--stargz-chunk-size 2097152",  "chunk-size 2 MiB"),
    ("--stargz-chunk-size 4194304",  "chunk-size 4 MiB"),
    ("--stargz-chunk-size 8388608",  "chunk-size 8 MiB"),
    ("--stargz-chunk-size 16777216", "chunk-size 16 MiB"),
    ("--stargz-chunk-size 33554432", "chunk-size 32 MiB"),
    ("--stargz-chunk-size 67108864", "chunk-size 64 MiB"),
    ("--stargz-chunk-size 134217728", "chunk-size 128 MiB"),
]

_FLAG_LABELS: dict[str, str] = {flags: label for flags, label in FLAG_OPTIONS}
_FLAG_LABELS["--stargz-chunk-size 4194304"] = "chunk-size 4 MiB (default)"


# ── image naming ───────────────────────────────────────────────────


def _build_name(source_image: str, cfg, label: str) -> str:
    slug = label.replace(" ", "-").lower()
    return f"{registry(cfg)}/{image_slug(source_image)}-{MODE}-{slug}:latest"


def _pull_name(source_image: str, cfg, label: str, n: int) -> str:
    end_col = n - 1
    slug = label.replace(" ", "-").lower()
    return f"{registry(cfg)}/library/{image_slug(source_image)}-{MODE}-{slug}:latest--0.0.0.{end_col}"


# ── prepare ────────────────────────────────────────────────────────


def _prepare_option(chunk_paths: list[str], source_image: str, cfg, flags: str, label: str) -> None:
    write_2dfs_json(chunk_paths, SCRIPT_DIR)
    target = _build_name(source_image, cfg, label)

    if MODE == "2dfs-stargz":
        base = stargz_base_image(source_image, cfg)
        mode_flags = ["--enable-stargz"]
    elif MODE == "2dfs-stargz-zstd":
        base = zstd_base_image(source_image, cfg)
        mode_flags = ["--enable-stargz", "--use-zstd"]
    else:
        raise ValueError(f"Unknown mode: {MODE}")

    cmd = tdfs_cmd(cfg, SCRIPT_DIR) + [
        "build",
        "--platforms", "linux/amd64",
        *mode_flags,
        *flags.split(),
        "--force-http",
        "-f", "2dfs.json",
        base,
        target,
    ]
    log.info(f"Building {MODE} image ({label}): {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built {target}")

    push_cmd = tdfs_cmd(cfg, SCRIPT_DIR) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")


# ── measure ────────────────────────────────────────────────────────


def _measure_option(
    source_image: str, cfg, flags: str, label: str,
) -> list[tuple[int, float, float]]:
    results = []
    for n in BASE_SPLITS:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info(f"\n[{ts}] === {label}: {n} allotments ===")
        clear_cache(cfg)

        image = _pull_name(source_image, cfg, label, n)
        pull_t = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
        log.result(f"  pull: {pull_t:.2f}s")

        name = f"run-config-{uuid.uuid4().hex[:8]}"
        run_t = _timed_run([
            "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
            image, name, *_run_cmd(n),
        ])
        log.result(f"  run: {run_t:.2f}s")

        results.append((n, pull_t, run_t))
    return results


# ── orchestration ──────────────────────────────────────────────────


def measure(
    chunk_paths: list[str], source_image: str, cfg,
) -> dict[str, list[tuple[int, float, float]]]:
    results: dict[str, list[tuple[int, float, float]]] = {}

    for flags, label in FLAG_OPTIONS:
        log.info(f"\n=== Preparing {MODE} ({label}) ===")
        _clear_2dfs_cache(cfg)
        _prepare_option(chunk_paths, source_image, cfg, flags, label)
        results[flags] = _measure_option(source_image, cfg, flags, label)

    return results


# ── output ─────────────────────────────────────────────────────────


def save_csv(
    results: dict[str, list[tuple[int, float, float]]],
    model: str,
    base_image: str,
) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    mode_slug = MODE.replace("-", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(RESULTS_DIR, f"{model_slug}_{img_slug}_{mode_slug}_{ts}.csv")

    header = ["splits"]
    for flags, label in FLAG_OPTIONS:
        slug = label.replace(" ", "_")
        header.extend([f"{slug}_pull_s", f"{slug}_run_s", f"{slug}_total_s"])

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for i, n in enumerate(BASE_SPLITS):
            row: list = [n]
            for flags, label in FLAG_OPTIONS:
                _, pull_t, run_t = results[flags][i]
                row.extend([f"{pull_t:.4f}", f"{run_t:.4f}", f"{pull_t + run_t:.4f}"])
            writer.writerow(row)
    log.result(f"Results saved to {output_path}")


def plot(
    results: dict[str, list[tuple[int, float, float]]],
    model: str,
    base_image: str,
) -> None:
    os.makedirs(CHARTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    mode_slug = MODE.replace("-", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    fig, ax = plt.subplots(figsize=(8, 5))
    for flags, flag_results in results.items():
        totals = [pull_t + run_t for _, pull_t, run_t in flag_results]
        ax.plot(BASE_SPLITS, totals, marker="o", label=_FLAG_LABELS[flags])

    ax.set_xlabel("Number of allotments pulled")
    ax.set_ylabel("Time (s)")
    ax.set_title(f"Pull + run performance by config ({MODE})")
    ax.set_xticks(BASE_SPLITS)
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.5)
    fig.text(0.01, 0.01, f"model: {model}\nbase image: {base_image}",
             fontsize=8, verticalalignment="bottom", family="monospace")
    fig.tight_layout()

    output_path = os.path.join(CHARTS_DIR, f"{model_slug}_{img_slug}_{mode_slug}_{ts}.png")
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    log.result(f"Chart saved to {output_path}")


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    log.info(f"Mode: {MODE}")
    log.info(f"Flag options: {[f'{label} ({flags})' for flags, label in FLAG_OPTIONS]}")
    log.info(f"Splits: {NUM_SPLITS}")
    log.info(f"Base splits: {BASE_SPLITS}")

    for model, base_image in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} =====")
        prepare_local_registry(base_image, registry(CFG))

        chunk_paths = prepare_chunks(model, NUM_SPLITS)
        results = measure(chunk_paths, base_image, CFG)

        log.result(f"\n=== Results ({MODE}) ===")
        col = max(len(label) for _, label in FLAG_OPTIONS) + 2
        header_flags = "  ".join(f"{label:>{col}}" for _, label in FLAG_OPTIONS)
        log.result(f"{'splits':>8}  {header_flags}")
        log.result("-" * (10 + (col + 2) * len(FLAG_OPTIONS)))
        for i, n in enumerate(BASE_SPLITS):
            row = "  ".join(
                f"{results[flags][i][1] + results[flags][i][2]:>{col}.2f}"
                for flags, _ in FLAG_OPTIONS
            )
            log.result(f"{n:>8}  {row}")

        save_csv(results, model, base_image)
        plot(results, model, base_image)


if __name__ == "__main__":
    main()
