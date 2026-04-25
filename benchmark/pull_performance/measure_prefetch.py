import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

from shared import log
from shared.charts import figure_footer, save_figure, write_csv
from shared.config import load_config
from shared.registry import prepare_local_registry, clear_registry, registry
from shared.services import clear_stargz_cache, collect_stargz_journal_since
from shared.model import cleanup_pull_experiment
from pull_performance.prepare import prepare_chunks, prepare_2dfs_stargz, prepare_2dfs_stargz_zstd
from pull_performance.images import pull_name_2dfs_stargz, pull_name_2dfs_stargz_zstd
from pull_performance.paths import prefetch_csv_path, prefetch_chart_path, prefetch_charts_dir
from pull_performance.measure_stargz_config import (
    _read_base_config, _apply_overrides, apply_stargz_config,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    # ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),
    # ("facebook/opt-350m", "docker.io/tensorflow/tensorflow"),
    ("Qwen/Qwen2-1.5B", "docker.io/ollama/ollama"),
]
MODES = ["2dfs-stargz", "2dfs-stargz-zstd"]
ALLOTMENTS = [2, 4]
N_CHUNKS = 10  # number of chunks to build the image with (always max)

# Config overrides to enable prefetch for this experiment.
PREFETCH_CONFIG_OVERRIDES = {
    "noprefetch": False,
    # "prefetch_size": 3 * 1024 * 1024 * 1024 # 3 GB
}
NOPREFETCH_CONFIG_OVERRIDES = {"noprefetch": True}

DOWNLOAD_COLOR = "#1f77b4"
DECOMPRESS_COLOR = "#2ca02c"

POLL_INTERVAL_S = 2        # how often to check journal during prefetch
QUIESCENCE_S = 30          # stop when no new prefetch_total events for this long
PREFETCH_TIMEOUT_S = 300   # hard stop

CFG = load_config()
VERBOSE = True


# ── data structures ────────────────────────────────────────────────


@dataclass
class LayerPrefetchEvent:
    layer_sha: str
    start_s: float          # seconds since epoch (prefetch() entry)
    download_end_s: float   # seconds since epoch (download complete)
    end_s: float            # seconds since epoch (decompress complete)
    total_ms: float
    download_ms: float
    decompress_ms: float
    prefetch_size_bytes: int


@dataclass
class PullPrefetchResult:
    mode: str
    n_allotments: int
    pull_start_s: float              # seconds since epoch
    pull_end_s: float                # seconds since epoch
    noprefetch_pull_duration_s: float  # duration of a plain pull (no prefetch)
    layers: list[LayerPrefetchEvent]


# ── log parsing ────────────────────────────────────────────────────

_VALUE_MS_RE = re.compile(r'value=([\d.]+)\s+milliseconds')
_PREFETCH_SIZE_RE = re.compile(r'prefetch_size=(\d+)\s+bytes')


def _count_prefetch_total(entries: list[dict]) -> int:
    count = 0
    for entry in entries:
        try:
            msg = json.loads(entry.get("MESSAGE", ""))
            if msg.get("operation") == "prefetch_total" and msg.get("layer_sha"):
                count += 1
        except (json.JSONDecodeError, AttributeError):
            pass
    return count


def _poll_until_prefetch_done(pull_start_s: float) -> list[LayerPrefetchEvent]:
    """Poll until no new prefetch_total events appear for QUIESCENCE_S seconds."""
    deadline = time.time() + PREFETCH_TIMEOUT_S
    last_seen = 0
    last_change_t = time.time()

    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_S)
        entries = collect_stargz_journal_since(pull_start_s)
        seen = _count_prefetch_total(entries)
        if seen != last_seen:
            last_seen = seen
            last_change_t = time.time()
            log.info(f"  prefetch_total events seen: {seen}")
        elif time.time() - last_change_t >= QUIESCENCE_S:
            log.info(f"  Quiescent after {seen} prefetch_total events")
            return _parse_prefetch_events(entries)

    log.info(f"  Prefetch timeout ({PREFETCH_TIMEOUT_S}s), collecting what we have")
    entries = collect_stargz_journal_since(pull_start_s)
    return _parse_prefetch_events(entries)


def _parse_prefetch_events(entries: list[dict]) -> list[LayerPrefetchEvent]:
    """Reconstruct per-layer prefetch timelines from journal entries."""
    download: dict[str, tuple[float, float]] = {}
    decompress: dict[str, tuple[float, float]] = {}
    total: dict[str, tuple[float, float, int]] = {}

    for entry in entries:
        try:
            msg = json.loads(entry.get("MESSAGE", ""))
        except (json.JSONDecodeError, AttributeError):
            continue
        if msg.get("metrics") != "latency":
            continue
        operation = msg.get("operation", "")
        layer_sha = msg.get("layer_sha", "")
        if not layer_sha or operation not in ("prefetch_download", "prefetch_decompress", "prefetch_total"):
            continue

        ts_us = int(entry.get("__REALTIME_TIMESTAMP", 0))
        end_s = ts_us / 1_000_000

        msg_text = msg.get("msg", "")
        vm = _VALUE_MS_RE.search(msg_text)
        if not vm:
            continue
        ms = float(vm.group(1))

        if operation == "prefetch_download":
            download[layer_sha] = (end_s, ms)
        elif operation == "prefetch_decompress":
            decompress[layer_sha] = (end_s, ms)
        elif operation == "prefetch_total":
            ps_m = _PREFETCH_SIZE_RE.search(msg_text)
            ps = int(ps_m.group(1)) if ps_m else 0
            total[layer_sha] = (end_s, ms, ps)

    events = []
    for layer_sha, (total_end_s, total_ms, prefetch_size) in total.items():
        start_s = total_end_s - total_ms / 1000
        dl_end_s, dl_ms = download.get(layer_sha, (start_s, 0.0))
        _, decomp_ms = decompress.get(layer_sha, (dl_end_s, 0.0))
        events.append(LayerPrefetchEvent(
            layer_sha=layer_sha,
            start_s=start_s,
            download_end_s=dl_end_s,
            end_s=total_end_s,
            total_ms=total_ms,
            download_ms=dl_ms,
            decompress_ms=decomp_ms,
            prefetch_size_bytes=prefetch_size,
        ))
    events.sort(key=lambda e: e.start_s)
    return [e for e in events if e.prefetch_size_bytes > 0]


# ── measure ────────────────────────────────────────────────────────


def _pull_name(mode: str, source_image: str, cfg, n: int) -> str:
    if mode == "2dfs-stargz":
        return pull_name_2dfs_stargz(source_image, cfg, n)
    elif mode == "2dfs-stargz-zstd":
        return pull_name_2dfs_stargz_zstd(source_image, cfg, n)
    raise ValueError(f"Unknown mode: {mode}")


def _rpull(image: str) -> None:
    subprocess.run(
        ["sudo", "ctr-remote", "images", "rpull", "--plain-http", "--use-containerd-labels", image],
        check=True, capture_output=not log.VERBOSE,
    )


def _rpull_noprefetch(image: str) -> None:
    subprocess.run(
        ["sudo", "ctr-remote", "images", "rpull", "--plain-http", image],
        check=True, capture_output=not log.VERBOSE,
    )


def _measure_one(mode: str, n: int, source_image: str, cfg, base_config: str, prefetch_config_content: str) -> PullPrefetchResult:
    image = _pull_name(mode, source_image, cfg, n)

    # Noprefetch pull for baseline duration
    apply_stargz_config(_apply_overrides(base_config, NOPREFETCH_CONFIG_OVERRIDES))
    log.info(f"  Clearing stargz cache (noprefetch)...")
    clear_stargz_cache()
    log.info(f"  Pulling noprefetch {mode} ({n} allotments): {image}")
    np_start = time.time()
    _rpull_noprefetch(image)
    noprefetch_pull_duration_s = time.time() - np_start
    log.info(f"  Noprefetch pull done in {noprefetch_pull_duration_s:.1f}s")

    log.info(f"  Sleeping {cfg.pull_cooldown}s...")
    time.sleep(cfg.pull_cooldown)

    # Prefetch pull
    apply_stargz_config(prefetch_config_content)
    log.info(f"  Clearing stargz cache (prefetch)...")
    clear_stargz_cache()
    log.info(f"  Pulling {mode} ({n} allotments): {image}")
    pull_start_s = time.time()
    _rpull(image)
    pull_end_s = time.time()
    log.info(f"  Pull done in {pull_end_s - pull_start_s:.1f}s, waiting for prefetch to finish...")

    events = _poll_until_prefetch_done(pull_start_s)
    log.result(f"  Prefetch events: {len(events)} layers")
    for ev in events:
        log.info(
            f"    {ev.layer_sha[7:19]} total={ev.total_ms:.0f}ms "
            f"dl={ev.download_ms:.0f}ms decomp={ev.decompress_ms:.0f}ms "
            f"size={ev.prefetch_size_bytes // 1024 // 1024}MB"
        )

    return PullPrefetchResult(
        mode=mode,
        n_allotments=n,
        pull_start_s=pull_start_s,
        pull_end_s=pull_end_s,
        noprefetch_pull_duration_s=noprefetch_pull_duration_s,
        layers=events,
    )


def _prepare_mode(mode: str, chunk_paths: list[str], source_image: str, cfg) -> None:
    if mode == "2dfs-stargz":
        prepare_2dfs_stargz(chunk_paths, source_image, cfg)
    elif mode == "2dfs-stargz-zstd":
        prepare_2dfs_stargz_zstd(chunk_paths, source_image, cfg)
    else:
        raise ValueError(f"Unknown mode: {mode}")


def measure(chunk_paths: list[str], source_image: str, cfg) -> list[PullPrefetchResult]:
    results = []
    base_config = _read_base_config()
    prefetch_config_content = _apply_overrides(base_config, PREFETCH_CONFIG_OVERRIDES)

    try:
        clear_stargz_cache()

        for mode in MODES:
            log.info(f"\n=== Preparing mode: {mode} ===")
            prepare_local_registry(source_image, registry(cfg))
            _prepare_mode(mode, chunk_paths, source_image, cfg)

            for n in ALLOTMENTS:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                log.info(f"\n[{ts}] === {mode}: {n} allotments ===")
                result = _measure_one(mode, n, source_image, cfg, base_config, prefetch_config_content)
                results.append(result)
                log.info(f"\nSleeping {cfg.pull_cooldown}s...")
                time.sleep(cfg.pull_cooldown)

            clear_registry(cfg, preserve_base=True)
    finally:
        log.info("\n=== Restoring original stargz config ===")
        apply_stargz_config(base_config)

    return results


# ── output ─────────────────────────────────────────────────────────


def save_csv(results: list[PullPrefetchResult], model: str, base_image: str) -> None:
    rows = []
    for r in results:
        ref = r.pull_start_s
        for ev in r.layers:
            rows.append({
                "mode": r.mode,
                "n_allotments": r.n_allotments,
                "layer_sha": ev.layer_sha,
                "start_rel_s": f"{ev.start_s - ref:.3f}",
                "download_end_rel_s": f"{ev.download_end_s - ref:.3f}",
                "end_rel_s": f"{ev.end_s - ref:.3f}",
                "total_ms": f"{ev.total_ms:.1f}",
                "download_ms": f"{ev.download_ms:.1f}",
                "decompress_ms": f"{ev.decompress_ms:.1f}",
                "prefetch_size_bytes": ev.prefetch_size_bytes,
                "pull_end_rel_s": f"{r.pull_end_s - ref:.3f}",
                "noprefetch_pull_duration_s": f"{r.noprefetch_pull_duration_s:.3f}",
            })
    if not rows:
        log.info("No prefetch data to save.")
        return
    write_csv(prefetch_csv_path(SCRIPT_DIR, model, base_image), list(rows[0].keys()), rows)


def plot(results: list[PullPrefetchResult], model: str, base_image: str) -> None:
    os.makedirs(prefetch_charts_dir(SCRIPT_DIR), exist_ok=True)

    for mode in MODES:
        mode_results = [r for r in results if r.mode == mode]
        if not mode_results:
            continue

        n_rows = len(mode_results)
        fig, axes = plt.subplots(
            n_rows, 1,
            figsize=(14, max(3, n_rows * 2.5)),
            squeeze=False,
        )

        for row_idx, result in enumerate(mode_results):
            ax = axes[row_idx][0]
            ref = result.pull_start_s

            if not result.layers:
                ax.set_title(f"{result.n_allotments} allotments — no prefetch data")
                continue

            for i, ev in enumerate(result.layers):
                dl_start = ev.start_s - ref
                dl_dur = ev.download_ms / 1000
                decomp_dur = ev.decompress_ms / 1000

                ax.barh(i, dl_dur, left=dl_start, height=0.6,
                        color=DOWNLOAD_COLOR, alpha=0.85)
                ax.barh(i, decomp_dur, left=dl_start + dl_dur, height=0.6,
                        color=DECOMPRESS_COLOR, alpha=0.85)

            ax.axvline(x=result.pull_end_s - ref, color="red", linestyle="--", linewidth=1.2)
            ax.axvline(x=result.noprefetch_pull_duration_s, color="orange", linestyle="--", linewidth=1.2)

            layer_labels = [
                f"{ev.layer_sha[7:19]} ({ev.prefetch_size_bytes // 1024 // 1024}MB)"
                for ev in result.layers
            ]
            ax.set_yticks(range(len(result.layers)))
            ax.set_yticklabels(layer_labels, fontsize=7, family="monospace")
            ax.set_xlabel("Time since pull start (s)")
            ax.set_title(f"{result.n_allotments} allotments — {len(result.layers)} layers prefetched")
            ax.grid(True, linestyle="--", alpha=0.3, axis="x")

        dl_patch = mpatches.Patch(color=DOWNLOAD_COLOR, alpha=0.85, label="download")
        dc_patch = mpatches.Patch(color=DECOMPRESS_COLOR, alpha=0.85, label="decompress")
        pull_end_line = mlines.Line2D([], [], color="red", linestyle="--", label="pull end (prefetch)")
        noprefetch_end_line = mlines.Line2D([], [], color="orange", linestyle="--", label="pull end (no prefetch)")
        axes[0][0].legend(handles=[dl_patch, dc_patch, pull_end_line, noprefetch_end_line], loc="upper right", fontsize=8)

        fig.suptitle(f"Per-layer Prefetch Timeline — {mode}", fontsize=12)
        figure_footer(fig, model, base_image)
        fig.tight_layout()
        save_figure(fig, prefetch_chart_path(SCRIPT_DIR, model, base_image, mode))


# ── main ───────────────────────────────────────────────────────────

VERIFY_MODE = "2dfs-stargz"
VERIFY_ALLOTMENTS = 4
VERIFY_WAIT_S = 120


def verify(chunk_paths: list[str], source_image: str, cfg) -> None:
    """Pull one image, wait 2 min, save raw journal logs for manual inspection."""
    base_config = _read_base_config()
    config_content = _apply_overrides(base_config, PREFETCH_CONFIG_OVERRIDES)

    try:
        clear_stargz_cache()
        log.info("\n=== Applying prefetch stargz config ===")
        apply_stargz_config(config_content)

        log.info(f"\n=== Preparing {VERIFY_MODE} ===")
        prepare_local_registry(source_image, registry(cfg))
        _prepare_mode(VERIFY_MODE, chunk_paths, source_image, cfg)
        image = _pull_name(VERIFY_MODE, source_image, cfg, VERIFY_ALLOTMENTS)
        log.info(f"\nPulling {image}")
        pull_start_s = time.time()
        _rpull(image)
        log.info(f"Pull done in {time.time() - pull_start_s:.1f}s, waiting {VERIFY_WAIT_S}s...")
        time.sleep(VERIFY_WAIT_S)

        entries = collect_stargz_journal_since(pull_start_s)
        log.info(f"Collected {len(entries)} journal entries")

        out_dir = os.path.join(SCRIPT_DIR, "results", "prefetch")
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(out_dir, f"verify_logs_{ts}.json")
        with open(out_path, "w") as f:
            json.dump(entries, f, indent=2)
        log.result(f"Raw logs saved to {out_path}")

    finally:
        log.info("\n=== Restoring original stargz config ===")
        apply_stargz_config(base_config)


def main():
    log.set_verbose(VERBOSE)

    for model, base_image in EXPERIMENTS:
        chunk_paths = prepare_chunks(model, N_CHUNKS)
        results = measure(chunk_paths, base_image, CFG)
        save_csv(results, model, base_image)
        plot(results, model, base_image)
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)


if __name__ == "__main__":
    main()
