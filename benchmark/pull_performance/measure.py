import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from shared import log
from shared.charts import MODE_COLORS, figure_footer, save_figure, write_csv
from pull_performance.paths import pull_csv_path, pull_chart_path, pull_artifacts_dir, pull_stargz_config_path, pull_merged_csv_path, pull_run_metadata_path
from shared.config import load_config
from shared.run_metadata import write_run_json
from shared.registry import prepare_local_registry, clear_registry, registry
from shared.services import ensure_buildkit, clear_stargz_cache
from shared.artifacts import clear_artifacts
from shared.model import cleanup_pull_experiment
from shared.stargz_config import read_base_config
from pull_performance.prepare import (
    prepare_model_splits,
    build_and_push_2dfs, build_and_push_2dfs_stargz, build_and_push_2dfs_stargz_zstd,
    build_and_push_stargz, build_and_push_base,
)
from shared.services import clear_2dfs_cache
from pull_performance.images import (
    pull_name_2dfs, pull_name_2dfs_stargz, pull_name_2dfs_stargz_zstd,
    pull_name_stargz, pull_name_base,
)

EXPERIMENTS = [
    # ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5GB     ~50 MB
    # ("Qwen/Qwen2-1.5B", "docker.io/library/python:3.12-slim"),                      # ~3.09 GB     ~3.4 GB
    # ("openlm-research/open_llama_3b", "docker.io/ollama/ollama"),    # ~6.0 GB     ~3.4 GB
    # ("EleutherAI/pythia-1.4b", "docker.io/library/python:3.12-slim"),
    ("openlm-research/open_llama_3b", "docker.io/ollama/ollama")    # ~6.0 GB     ~3.4 GB
]
CFG = load_config()
VERBOSE = True
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
PARTITION_PERCENTS = [25, 50, 75, 100]
SCHEMA_VERSION = 1

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class PullRow:
    schema_version: int
    model: str
    base_image: str
    max_allowed_splits: int
    run: int
    partition_pct: int
    num_splits: int
    mode: str
    pull_s: float
    run_s: float
    total_s: float

# ── helpers ────────────────────────────────────────────────────────


def _next_container_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _timed_pull(cmd: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - start


def _timed_run(cmd: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=not log.VERBOSE)
    return time.perf_counter() - start


def _run_cmd(allotments: list[list[str]], n: int) -> list[str]:
    files = " ".join(
        f"/{os.path.basename(p)}"
        for a in allotments[:n] for p in a
    )
    log.info(f"  reading files: {files}")
    return ["sh", "-c", f"cat {files} > /dev/null"]


# ── pull functions ─────────────────────────────────────────────────


def pull_base(source_image: str, cfg, num_splits: int) -> float:
    image = pull_name_base(source_image, cfg, num_splits)
    log.info(f"Pulling base image: {image}")
    elapsed = _timed_pull(["sudo", "ctr", "images", "pull", "--plain-http", image])
    log.result(f"  base pull ({num_splits} splits): {elapsed:.2f}s")
    return elapsed


def pull_stargz(source_image: str, cfg) -> float:
    image = pull_name_stargz(source_image, cfg)
    log.info(f"Pulling stargz image: {image}")
    elapsed = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
    log.result(f"  stargz pull: {elapsed:.2f}s")
    return elapsed


def pull_2dfs(source_image: str, cfg, num_allotments: int) -> float:
    image = pull_name_2dfs(source_image, cfg, num_allotments)
    log.info(f"Pulling 2dfs ({num_allotments} allotments): {image}")
    elapsed = _timed_pull(["sudo", "ctr", "images", "pull", "--plain-http", image])
    log.result(f"  2dfs pull ({num_allotments} allotments): {elapsed:.2f}s")
    return elapsed


def pull_2dfs_stargz(source_image: str, cfg, num_allotments: int) -> float:
    image = pull_name_2dfs_stargz(source_image, cfg, num_allotments)
    log.info(f"Pulling 2dfs-stargz ({num_allotments} allotments): {image}")
    elapsed = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
    log.result(f"  2dfs-stargz pull ({num_allotments} allotments): {elapsed:.2f}s")
    return elapsed


def pull_2dfs_stargz_zstd(source_image: str, cfg, num_allotments: int) -> float:
    image = pull_name_2dfs_stargz_zstd(source_image, cfg, num_allotments)
    log.info(f"Pulling 2dfs-stargz-zstd ({num_allotments} allotments): {image}")
    elapsed = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
    log.result(f"  2dfs-stargz-zstd pull ({num_allotments} allotments): {elapsed:.2f}s")
    return elapsed


# ── run functions ──────────────────────────────────────────────────


def run_base(image: str, allotments: list[list[str]], n: int) -> float:
    name = _next_container_name("run-base")
    log.info(f"Running base container: {name} (reading {n} allotments)")
    elapsed = _timed_run([
        "sudo", "ctr", "run", "--rm", image, name, *_run_cmd(allotments, n),
    ])
    log.result(f"  base run: {elapsed:.2f}s")
    return elapsed


def run_stargz(image: str, allotments: list[list[str]], n: int) -> float:
    name = _next_container_name("run-stargz")
    log.info(f"Running stargz container: {name} (reading {n} allotments)")
    elapsed = _timed_run([
        "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
        image, name, *_run_cmd(allotments, n),
    ])
    log.result(f"  stargz run: {elapsed:.2f}s")
    return elapsed


def run_2dfs(image: str, allotments: list[list[str]], n: int) -> float:
    name = _next_container_name("run-2dfs")
    log.info(f"Running 2dfs container: {name} (reading {n} allotments)")
    elapsed = _timed_run([
        "sudo", "ctr", "run", "--rm", image, name, *_run_cmd(allotments, n),
    ])
    log.result(f"  2dfs run: {elapsed:.2f}s")
    return elapsed


def run_2dfs_stargz(image: str, allotments: list[list[str]], n: int) -> float:
    name = _next_container_name("run-2dfs-stargz")
    log.info(f"Running 2dfs-stargz container: {name} (reading {n} allotments)")
    elapsed = _timed_run([
        "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
        image, name, *_run_cmd(allotments, n),
    ])
    log.result(f"  2dfs-stargz run: {elapsed:.2f}s")
    return elapsed


def run_2dfs_stargz_zstd(image: str, allotments: list[list[str]], n: int) -> float:
    name = _next_container_name("run-2dfs-stargz-zstd")
    log.info(f"Running 2dfs-stargz-zstd container: {name} (reading {n} allotments)")
    elapsed = _timed_run([
        "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
        image, name, *_run_cmd(allotments, n),
    ])
    log.result(f"  2dfs-stargz-zstd run: {elapsed:.2f}s")
    return elapsed


# ── orchestration ──────────────────────────────────────────────────


def _prepare_mode(
    mode: str, allotments: list[list[str]], base_splits: list[int],
    source_image: str, cfg, model: str, execution_ts: str,
) -> None:
    def art(n: int | None = None) -> str:
        return pull_artifacts_dir(SCRIPT_DIR, execution_ts, model, source_image, mode, n)
    if mode == "base":
        build_and_push_base(allotments, base_splits, source_image, cfg, artifacts_dir_fn=art)
    elif mode == "stargz":
        build_and_push_stargz(allotments, source_image, cfg, artifacts_dir=art())
    elif mode == "2dfs":
        clear_2dfs_cache(cfg)
        build_and_push_2dfs(allotments, source_image, cfg, artifacts_dir=art())
    elif mode == "2dfs-stargz":
        clear_2dfs_cache(cfg)
        build_and_push_2dfs_stargz(allotments, source_image, cfg, artifacts_dir=art())
    elif mode == "2dfs-stargz-zstd":
        clear_2dfs_cache(cfg)
        build_and_push_2dfs_stargz_zstd(allotments, source_image, cfg, artifacts_dir=art())
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _measure_one(mode: str, allotments: list[list[str]], n: int, source_image: str, cfg) -> tuple[int, float, float]:
    if mode == "base":
        pull_t = pull_base(source_image, cfg, n)
        run_t = run_base(pull_name_base(source_image, cfg, n), allotments, n)
    elif mode == "stargz":
        pull_t = pull_stargz(source_image, cfg)
        run_t = run_stargz(pull_name_stargz(source_image, cfg), allotments, n)
    elif mode == "2dfs":
        pull_t = pull_2dfs(source_image, cfg, n)
        run_t = run_2dfs(pull_name_2dfs(source_image, cfg, n), allotments, n)
    elif mode == "2dfs-stargz":
        pull_t = pull_2dfs_stargz(source_image, cfg, n)
        run_t = run_2dfs_stargz(pull_name_2dfs_stargz(source_image, cfg, n), allotments, n)
    elif mode == "2dfs-stargz-zstd":
        pull_t = pull_2dfs_stargz_zstd(source_image, cfg, n)
        run_t = run_2dfs_stargz_zstd(pull_name_2dfs_stargz_zstd(source_image, cfg, n), allotments, n)
    else:
        raise ValueError(f"Unknown mode: {mode}")
    return (n, pull_t, run_t)


def _splits_for(max_allowed_splits: int) -> list[int]:
    return [max(1, max_allowed_splits * pct // 100) for pct in PARTITION_PERCENTS]


def split_stats(allotments: list[list[str]]) -> dict:
    """Aggregate split-pool stats (sizes in MB) for run metadata."""
    sizes = [
        os.path.getsize(p)
        for a in allotments for p in a if p.endswith(".safetensors")
    ]
    return {
        "num_safetensors": len(sizes),
        "total_mb": round(sum(sizes) / (1024 ** 2), 1),
        "max_file_mb": round(max(sizes) / (1024 ** 2), 1) if sizes else 0.0,
    }


def packing_preview_data(allotments: list[list[str]], max_allowed_splits: int) -> list[dict]:
    """Structured per-partition-pct: #allotments read + their sizes (MB).

    Cumulative: each pct reads the first n allotments."""
    out: list[dict] = []
    for pct in PARTITION_PERCENTS:
        n = max(1, max_allowed_splits * pct // 100)
        sizes_mb = [
            round(sum(os.path.getsize(p) for p in a) / (1024 ** 2), 1)
            for a in allotments[:n]
        ]
        out.append({
            "partition_pct": pct,
            "num_splits": n,
            "allotment_sizes_mb": sizes_mb,
        })
    return out


def print_packing_table(allotments: list[list[str]], model: str, max_allowed_splits: int) -> None:
    """Print per-partition-pct (#allotments read, allotment sizes in MB)."""
    log.result(
        f"\n=== Packing preview: {model} "
        f"(max_allowed_splits={max_allowed_splits}) ==="
    )
    log.result(f"{'pct':>10}  {'allotments':>11}  sizes (MB)")
    log.result("-" * 60)
    for e in packing_preview_data(allotments, max_allowed_splits):
        sizes_str = "[" + ", ".join(f"{s:.1f}" for s in e["allotment_sizes_mb"]) + "]"
        log.result(f"{e['partition_pct']:>9}%  {e['num_splits']:>11}  {sizes_str}")


def measure(
    allotments: list[list[str]], max_allowed_splits: int, source_image: str, cfg,
    model: str, execution_ts: str,
) -> list[PullRow]:
    results: list[PullRow] = []

    base_splits = _splits_for(max_allowed_splits)

    clear_registry(cfg, preserve_base=True, verbose=False)
    for mode in MODES:
        log.info(f"\n=== Preparing mode: {mode} ===")
        prepare_local_registry(source_image, registry(cfg))
        _prepare_mode(mode, allotments, base_splits, source_image, cfg, model, execution_ts)

        for run in range(CFG.pull_n_runs):
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Run {run + 1}/{CFG.pull_n_runs} ===")
            for pct in PARTITION_PERCENTS:
                n = max(1, max_allowed_splits * pct // 100)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                log.info(f"\n[{ts}] === {mode}: {pct}% ({n} allotments) ===")
                clear_stargz_cache()
                _, pull_t, run_t = _measure_one(mode, allotments, n, source_image, cfg)
                results.append(PullRow(
                    schema_version=SCHEMA_VERSION,
                    model=model,
                    base_image=source_image,
                    max_allowed_splits=max_allowed_splits,
                    run=run,
                    partition_pct=pct,
                    num_splits=n,
                    mode=mode,
                    pull_s=pull_t,
                    run_s=run_t,
                    total_s=pull_t + run_t,
                ))
                log.info(f"\nSleeping {cfg.pull_cooldown}s before next...")
                time.sleep(cfg.pull_cooldown)

        clear_registry(cfg, preserve_base=True)

    return results


# ── output ─────────────────────────────────────────────────────────


def print_results(results: list[PullRow]) -> None:
    pcts = sorted({r.partition_pct for r in results})
    col = 26
    header_modes = "  ".join(f"{m:>{col}}" for m in MODES)
    subheader = "  ".join(f"{'pull(m±s) run(m±s)':>{col}}" for _ in MODES)
    log.result(f"\n=== Pull + Run Performance Results (mean ± stddev, n={CFG.pull_n_runs} runs) ===")
    log.result(f"{'pct':>8}  {header_modes}")
    log.result(f"{'':>8}  {subheader}")
    log.result("-" * (10 + (col + 2) * len(MODES)))
    for pct in pcts:
        def fmt(mode: str) -> str:
            group = [(r.pull_s, r.run_s) for r in results if r.mode == mode and r.partition_pct == pct]
            if not group:
                return "N/A"
            pull_arr = np.array([g[0] for g in group])
            run_arr = np.array([g[1] for g in group])
            return (
                f"{pull_arr.mean():.1f}±{pull_arr.std(ddof=0):.1f} "
                f"{run_arr.mean():.1f}±{run_arr.std(ddof=0):.1f}"
            )
        row = "  ".join(f"{fmt(m):>{col}}" for m in MODES)
        log.result(f"{pct:>7}%  {row}")


def _write_pull_rows(output_path: str, results: list[PullRow]) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = [f.name for f in fields(PullRow)]
    rows = [{
        **asdict(r),
        "pull_s": f"{r.pull_s:.4f}",
        "run_s": f"{r.run_s:.4f}",
        "total_s": f"{r.total_s:.4f}",
    } for r in results]
    write_csv(output_path, fieldnames, rows)


def save_csv(results: list[PullRow], model: str, base_image: str, execution_ts: str) -> None:
    pcts = sorted({r.partition_pct for r in results})
    output_path = pull_csv_path(SCRIPT_DIR, model, base_image, len(pcts), execution_ts)
    _write_pull_rows(output_path, results)


def save_merged_csv(results: list[PullRow], execution_ts: str) -> None:
    _write_pull_rows(pull_merged_csv_path(SCRIPT_DIR, execution_ts), results)


def plot(results: list[PullRow], model: str, base_image: str, execution_ts: str) -> None:
    pcts = sorted({r.partition_pct for r in results})
    x = np.arange(len(pcts))
    n_modes = len(MODES)
    width = min(0.8 / n_modes, 0.15)

    fig, ax = plt.subplots(figsize=(max(10, n_modes * 2), 6))

    for i, mode in enumerate(MODES):
        color = MODE_COLORS[mode]
        offset = (i - (n_modes - 1) / 2) * width
        mean_pulls, std_pulls = [], []
        mean_runs = []
        std_totals = []
        for pct in pcts:
            group = [(r.pull_s, r.run_s) for r in results if r.mode == mode and r.partition_pct == pct]
            if group:
                pull_arr = np.array([g[0] for g in group])
                run_arr = np.array([g[1] for g in group])
                tot_arr = pull_arr + run_arr
                mean_pulls.append(float(pull_arr.mean()))
                std_pulls.append(float(pull_arr.std(ddof=0)))
                mean_runs.append(float(run_arr.mean()))
                std_totals.append(float(tot_arr.std(ddof=0)))
            else:
                mean_pulls.append(0.0)
                std_pulls.append(0.0)
                mean_runs.append(0.0)
                std_totals.append(0.0)
        ax.bar(x + offset, mean_pulls, width, yerr=std_pulls, capsize=3,
               color=color, alpha=0.5, hatch="//", edgecolor=color, linewidth=0.5)
        ax.bar(x + offset, mean_runs, width, bottom=mean_pulls, yerr=std_totals, capsize=3,
               color=color, edgecolor=color, linewidth=0.5, label=mode)

    ax.set_xlabel("Partition size (%)")
    ax.set_ylabel("Time (s)")
    ax.set_title(f"Pull + Run Performance (mean ± stddev, n={CFG.pull_n_runs} runs)")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{p}%" for p in pcts])
    ax.grid(True, linestyle="--", alpha=0.3, axis="y")

    method_handles = [mpatches.Patch(facecolor=MODE_COLORS[m], edgecolor=MODE_COLORS[m], label=m)
                      for m in MODES]
    pull_patch = mpatches.Patch(facecolor="gray", alpha=0.5, hatch="//",
                                edgecolor="gray", label="pull")
    run_patch = mpatches.Patch(facecolor="gray", edgecolor="gray", label="run")
    ax.legend(handles=method_handles + [pull_patch, run_patch], loc="upper left")

    output_path = pull_chart_path(SCRIPT_DIR, model, base_image, len(pcts), execution_ts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.tight_layout()
    figure_footer(fig, model, base_image)
    save_figure(fig, output_path)


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    clear_artifacts(SCRIPT_DIR)
    ensure_buildkit()
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_started = datetime.now(timezone.utc)
    log.info(f"Modes: {MODES}")
    log.info(f"Partition percents: {PARTITION_PERCENTS}")
    log.info(f"Runs: {CFG.pull_n_runs}")

    log.info("Pre-run cleanup...")
    for model, _ in EXPERIMENTS:
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)

    stargz_config_path = pull_stargz_config_path(SCRIPT_DIR, execution_ts)
    os.makedirs(os.path.dirname(stargz_config_path), exist_ok=True)
    with open(stargz_config_path, "w") as f:
        f.write(read_base_config())
    log.result(f"Stargz config snapshot saved to {stargz_config_path}")

    all_results: list[PullRow] = []
    experiments_meta: list[dict] = []
    for model, base_image in EXPERIMENTS:
        allotments, max_allowed_splits = prepare_model_splits(model)
        log.result(f"\n===== Experiment: {model} / {base_image} (max_splits={max_allowed_splits}) =====")
        print_packing_table(allotments, model, max_allowed_splits)

        experiments_meta.append({
            "model": model,
            "base_image": base_image,
            "max_allowed_splits": max_allowed_splits,
            "splits": split_stats(allotments),
            "packing_preview": packing_preview_data(allotments, max_allowed_splits),
        })

        results = measure(allotments, max_allowed_splits, base_image, CFG, model, execution_ts)

        print_results(results)
        save_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, execution_ts)
        all_results.extend(results)
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)

    if all_results:
        save_merged_csv(all_results, execution_ts)

    write_run_json(
        pull_run_metadata_path(SCRIPT_DIR, execution_ts),
        execution_ts=execution_ts,
        started_at=run_started,
        config={
            "registry": registry(CFG),
            "tdfs_binary": CFG.tdfs_binary,
            "pull_n_runs": CFG.pull_n_runs,
            "pull_cooldown": CFG.pull_cooldown,
        },
        sections={
            "modes": MODES,
            "partition_percents": PARTITION_PERCENTS,
            "experiments": experiments_meta,
        },
    )

    clear_artifacts(SCRIPT_DIR)


if __name__ == "__main__":
    main()
