import csv
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from shared import log
from shared.charts import MODE_COLORS, figure_footer, add_run_dots, bar_group_xticks, save_figure
from pull_performance.paths import (
    stargz_config_charts_run_dir, stargz_config_csv_path, stargz_config_chart_path, stargz_config_log_path,
)
from shared.config import load_config
from shared.registry import prepare_local_registry, clear_registry, registry, image_slug
from pull_performance.measure import _timed_pull, _timed_run, _run_cmd
from shared.services import clear_stargz_cache, save_stargz_run_log
from pull_performance.prepare import (
    prepare_2dfs_stargz, prepare_2dfs_stargz_zstd, prepare_chunks,
)
from shared.model import cleanup_pull_experiment
from pull_performance.images import pull_name_2dfs_stargz, pull_name_2dfs_stargz_zstd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STARGZ_CONFIG_PATH = "/etc/containerd-stargz-grpc/config.toml"

EXPERIMENTS = [
    # ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5 GB     ~50 MB
    # ("openai-community/gpt2-medium", "docker.io/library/python:3.12-slim"),   # ~1.52 GB    ~50 MB
    ("openai-community/gpt2-large", "docker.io/library/python:3.12-slim"),    # ~3.25 GB    ~50 MB
    # ("openlm-research/open_llama_3b", "docker.io/library/python:3.12-slim"),    # ~6 GB    ~50 MB
]
MODES = ["2dfs-stargz", "2dfs-stargz-zstd"]
CONFIG_OPTIONS: list[tuple[dict, str]] = [
    ({"noprefetch": True, "prefetch_async_size": 0, "no_background_fetch": True}, "no prefetch"),
    ({"noprefetch": False, "prefetch_async_size": 0, "no_background_fetch": True}, "prefetch"),
    ({"noprefetch": False, "prefetch_async_size": 1, "no_background_fetch": True}, "prefetch, async"),
    ({"noprefetch": False, "prefetch_async_size": 1, "no_background_fetch": False}, "prefetch, async, bg fetch"),
]
CFG = load_config()
VERBOSE = True


# ── TOML config helpers ────────────────────────────────────────────


def _to_toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    return str(v)


def _read_base_config() -> str:
    result = subprocess.run(
        ["sudo", "cat", STARGZ_CONFIG_PATH],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _apply_overrides(base_content: str, overrides: dict) -> str:
    """Replace existing key=value lines or append new ones for each override."""
    content = base_content
    for key, value in overrides.items():
        toml_val = _to_toml_value(value)
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        replacement = f"{key} = {toml_val}"
        if pattern.search(content):
            content = pattern.sub(replacement, content)
        else:
            # Insert before the first section header so the key stays top-level.
            section_match = re.search(r"^\[", content, re.MULTILINE)
            if section_match:
                idx = section_match.start()
                content = content[:idx].rstrip("\n") + f"\n{replacement}\n\n" + content[idx:]
            else:
                content = content.rstrip("\n") + f"\n{replacement}\n"
    return content


def apply_stargz_config(config_content: str) -> None:
    """Stop service, write config, start service (mirrors local/apply-config.sh)."""
    current = _read_base_config()
    log.info("--- applying stargz config ---")
    log.info(f"BEFORE:\n{current}")
    log.info(f"AFTER:\n{config_content}")
    tmp = "/tmp/stargz-config-measure.toml"
    with open(tmp, "w") as f:
        f.write(config_content)
    subprocess.run(["sudo", "systemctl", "stop", "stargz-snapshotter"], check=True)
    subprocess.run(["sudo", "cp", tmp, STARGZ_CONFIG_PATH], check=True)
    subprocess.run(["sudo", "systemctl", "start", "stargz-snapshotter"], check=True)


# ── image naming ───────────────────────────────────────────────────


def _pull_name(mode: str, source_image: str, cfg, n: int) -> str:
    if mode == "2dfs-stargz":
        return pull_name_2dfs_stargz(source_image, cfg, n)
    elif mode == "2dfs-stargz-zstd":
        return pull_name_2dfs_stargz_zstd(source_image, cfg, n)
    else:
        raise ValueError(f"Unknown mode: {mode}")


# ── prepare ────────────────────────────────────────────────────────


def _prepare_mode(mode: str, chunk_paths: list[str], source_image: str, cfg) -> None:
    if mode == "2dfs-stargz":
        prepare_2dfs_stargz(chunk_paths, source_image, cfg)
    elif mode == "2dfs-stargz-zstd":
        prepare_2dfs_stargz_zstd(chunk_paths, source_image, cfg)
    else:
        raise ValueError(f"Unknown mode: {mode}")


# ── measure ────────────────────────────────────────────────────────


def _measure_config_option(
    mode: str, source_image: str, cfg,
    config_label: str, run_idx: int, model: str, base_image: str, execution_ts: str,
) -> list[tuple[int, float, float]]:
    results = []
    for n in cfg.stargz_config_base_splits:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info(f"\n[{ts}] === {mode}: {n} allotments ===")
        clear_stargz_cache()

        image = _pull_name(mode, source_image, cfg, n)
        pull_start_s = time.time()
        pull_t = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", "--use-containerd-labels", image])
        log.result(f"  pull: {pull_t:.2f}s")

        name = f"run-stargz-cfg-{uuid.uuid4().hex[:8]}"
        run_t = _timed_run([
            "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
            image, name, *_run_cmd(n),
        ])
        run_end_s = time.time()
        log.result(f"  run: {run_t:.2f}s")

        save_stargz_run_log(pull_start_s, run_end_s, stargz_config_log_path(SCRIPT_DIR, model, base_image, mode, config_label, n, run_idx, execution_ts))

        results.append((n, pull_t, run_t))
        log.info(f"\nSleeping {cfg.pull_cooldown}s before next...")
        time.sleep(cfg.pull_cooldown)
    return results


# ── orchestration ──────────────────────────────────────────────────


def measure(
    chunk_paths: list[str], source_image: str, cfg, model: str, base_image: str, execution_ts: str,
) -> dict[tuple[str, str], list[tuple[int, int, float, float]]]:
    # results[(mode, config_label)] = list of (run, n, pull_t, run_t)
    results: dict[tuple[str, str], list[tuple[int, int, float, float]]] = {}

    base_config = _read_base_config()

    def _prepare_all_images():
        log.info("\n=== Preparing images ===")
        for mode in MODES:
            log.info(f"\n--- Preparing mode: {mode} ---")
            prepare_local_registry(source_image, registry(cfg))
            _prepare_mode(mode, chunk_paths, source_image, cfg)

    try:
        for overrides, label in CONFIG_OPTIONS:
            log.info(f"\n=== Config option: {label} ===")
            clear_registry(cfg, preserve_base=True)
            _prepare_all_images()
            config_content = _apply_overrides(base_config, overrides)
            apply_stargz_config(config_content)

            for mode in MODES:
                key = (mode, label)
                results[key] = []
                for run in range(cfg.stargz_config_n_runs):
                    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                             f"=== Run {run + 1}/{cfg.stargz_config_n_runs} | {mode} | {label} ===")
                    for n, pull_t, run_t in _measure_config_option(
                        mode, source_image, cfg, label, run, model, base_image, execution_ts,
                    ):
                        results[key].append((run, n, pull_t, run_t))
    finally:
        log.info("\n=== Restoring base stargz config ===")
        apply_stargz_config(base_config)

    return results


# ── output ─────────────────────────────────────────────────────────


def save_csv(
    results: dict[tuple[str, str], list[tuple[int, int, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    output_path = stargz_config_csv_path(SCRIPT_DIR, model, base_image, execution_ts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    splits = sorted(set(n for entries in results.values() for _, n, _, _ in entries))

    header = ["run", "splits"]
    for mode in MODES:
        for _, label in CONFIG_OPTIONS:
            slug = f"{mode.replace('-', '_')}_{label.replace('-', '_')}"
            header += [f"{slug}_pull_s", f"{slug}_run_s", f"{slug}_total_s"]

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for run in range(CFG.stargz_config_n_runs):
            for n in splits:
                def row_vals(key: tuple[str, str]) -> list[str]:
                    match = [
                        (pull_t, run_t)
                        for r, n_val, pull_t, run_t in results.get(key, [])
                        if r == run and n_val == n
                    ]
                    if not match:
                        return ["", "", ""]
                    p, r_t = match[0]
                    return [f"{p:.4f}", f"{r_t:.4f}", f"{p + r_t:.4f}"]

                row: list = [run, n]
                for mode in MODES:
                    for _, label in CONFIG_OPTIONS:
                        row += row_vals((mode, label))
                writer.writerow(row)

    log.result(f"Results saved to {output_path}")


def plot(
    results: dict[tuple[str, str], list[tuple[int, int, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    os.makedirs(stargz_config_charts_run_dir(SCRIPT_DIR, execution_ts), exist_ok=True)

    config_labels = [label for _, label in CONFIG_OPTIONS]
    splits = sorted(set(n for entries in results.values() for _, n, _, _ in entries))
    n_configs = len(config_labels)
    width = min(0.8 / n_configs, 0.15)

    for mode in MODES:
        color = MODE_COLORS[mode]
        fig, ax = plt.subplots(figsize=(max(10, n_configs * 2), 6))
        x = np.arange(len(splits))

        for i, label in enumerate(config_labels):
            key = (mode, label)
            entries = results.get(key, [])
            offset = (i - (n_configs - 1) / 2) * width
            med_pulls = []
            med_runs = []
            for j, n in enumerate(splits):
                group = [(pull_t, run_t) for _, n_val, pull_t, run_t in entries if n_val == n]
                med_p = float(np.median([g[0] for g in group])) if group else 0.0
                med_r = float(np.median([g[1] for g in group])) if group else 0.0
                med_pulls.append(med_p)
                med_runs.append(med_r)
                x_center = x[j] + offset + width / 2
                add_run_dots(ax, x_center, [g[0] + g[1] for g in group])

            # vary lightness per config option so bars are distinguishable
            alpha = 0.4 + 0.6 * (i / max(n_configs - 1, 1))
            ax.bar(x + offset, med_pulls, width, color=color, alpha=alpha * 0.6,
                   hatch="//", edgecolor=color, linewidth=0.5)
            ax.bar(x + offset, med_runs, width, bottom=med_pulls, color=color,
                   alpha=alpha, edgecolor=color, linewidth=0.5, label=label)

        bar_group_xticks(ax, len(splits), n_configs, width, [str(s) for s in splits])
        ax.set_xlabel("Number of allotments pulled")
        ax.set_ylabel("Time (s)")
        ax.set_title(
            f"Pull + Run by stargz config ({mode}, "
            f"median, n={CFG.stargz_config_n_runs} runs, dots = individual runs)"
        )
        ax.grid(True, linestyle="--", alpha=0.3, axis="y")

        pull_patch = mpatches.Patch(facecolor="gray", alpha=0.5, hatch="//",
                                    edgecolor="gray", label="pull")
        run_patch = mpatches.Patch(facecolor="gray", edgecolor="gray", label="run")
        config_handles = [
            mpatches.Patch(facecolor=color,
                           alpha=0.4 + 0.6 * (i / max(n_configs - 1, 1)),
                           edgecolor=color, label=label)
            for i, label in enumerate(config_labels)
        ]
        ax.legend(handles=config_handles + [pull_patch, run_patch], loc="upper left")

        figure_footer(fig, model, base_image)
        fig.tight_layout()

        output_path = stargz_config_chart_path(SCRIPT_DIR, model, base_image, mode, execution_ts)
        save_figure(fig, output_path)


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info(f"Modes: {MODES}")
    log.info(f"Config options: {[label for _, label in CONFIG_OPTIONS]}")
    log.info(f"Splits (total): {CFG.stargz_config_n_splits}")
    log.info(f"Splits (measured): {CFG.stargz_config_base_splits}")
    log.info(f"Runs: {CFG.stargz_config_n_runs}")

    log.info("Pre-run cleanup...")
    for model, _ in EXPERIMENTS:
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)

    for model, base_image in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} =====")
        prepare_local_registry(base_image, registry(CFG))

        chunk_paths = prepare_chunks(model, CFG.stargz_config_n_splits)
        results = measure(chunk_paths, base_image, CFG, model, base_image, execution_ts)

        save_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, execution_ts)
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)


if __name__ == "__main__":
    main()
