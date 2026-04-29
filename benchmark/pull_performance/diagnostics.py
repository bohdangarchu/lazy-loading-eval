import json
import os
import time
import uuid
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import urlopen

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

from shared import log
from shared.charts import figure_footer, save_figure, write_csv
from shared.config import load_config
from shared.registry import clear_registry, prepare_local_registry, registry
from shared.services import clear_stargz_cache, save_stargz_run_log
from shared.stargz_config import apply_overrides, apply_stargz_config, read_base_config
from pull_performance.measure import _run_cmd, _timed_pull, _timed_run
from pull_performance.prefetch_common import (
    poll_until_prefetch_done, prefetch_span, prepare_mode, pull_name,
)
from pull_performance.prepare import prepare_chunks

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENT = ("openai-community/gpt2-large", "docker.io/library/python:3.12-slim")
MODE = "2dfs-stargz"
N_SPLITS = 10
N_ALLOTMENTS = 8
CONFIG_LABEL = "prefetch_async0_logfa"
CONFIG_OVERRIDES = {
    "noprefetch": False,
    "prefetch_async_size": 0,
    "no_background_fetch": True,
    "log_file_access": True,
}
PROM_URL = "http://127.0.0.1:9090"

PULL_COLOR = "#1f77b4"
PREFETCH_COLOR = "#9467bd"
RUN_COLOR = "#2ca02c"


# ── paths ──────────────────────────────────────────────────────────


def _diag_run_dir(execution_ts: str) -> str:
    return os.path.join(SCRIPT_DIR, "results", "diagnostics", execution_ts)


def _diag_charts_dir(execution_ts: str) -> str:
    return os.path.join(SCRIPT_DIR, "charts", "diagnostics", execution_ts)


def _diag_logs_dir(execution_ts: str) -> str:
    return os.path.join(SCRIPT_DIR, "logs", "diagnostics", execution_ts)


# ── prometheus ─────────────────────────────────────────────────────


def _check_prometheus_alive() -> bool:
    try:
        with urlopen(f"{PROM_URL}/-/healthy", timeout=3) as r:
            return r.status == 200
    except Exception as e:
        log.info(f"  prometheus not reachable at {PROM_URL}: {e}")
        return False


def _query_range(query: str, start_s: float, end_s: float, step: str = "1s") -> dict | None:
    params = urlencode({"query": query, "start": f"{start_s}", "end": f"{end_s}", "step": step})
    url = f"{PROM_URL}/api/v1/query_range?{params}"
    try:
        with urlopen(url, timeout=15) as r:
            return json.load(r)
    except Exception as e:
        log.info(f"  query failed [{query}]: {e}")
        return None


def _snapshot_prometheus(start_s: float, end_s: float, out_path: str) -> None:
    queries = {
        "bytes_served": "stargz_fs_bytes_served",
        "operation_count": "stargz_fs_operation_count",
        "operation_latency_us_count": "stargz_fs_operation_duration_microseconds_count",
        "operation_latency_us_sum": "stargz_fs_operation_duration_microseconds_sum",
        "operation_latency_ms_count": "stargz_fs_operation_duration_milliseconds_count",
        "operation_latency_ms_sum": "stargz_fs_operation_duration_milliseconds_sum",
    }
    results = {name: _query_range(q, start_s, end_s) for name, q in queries.items()}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"window": {"start_s": start_s, "end_s": end_s}, "queries": results}, f, indent=2)
    log.info(f"  Saved prometheus snapshot → {out_path}")


# ── chart ──────────────────────────────────────────────────────────


def _plot_timeline(row: dict, model: str, base_image: str, out_path: str) -> None:
    fig, ax = plt.subplots(figsize=(11, 3.2))

    bars = [("pull", row["pull_rel_start_s"], row["pull_rel_end_s"], PULL_COLOR)]
    if row["prefetch_rel_start_s"] != "":
        bars.append(("prefetch", float(row["prefetch_rel_start_s"]),
                     float(row["prefetch_rel_end_s"]), PREFETCH_COLOR))
    bars.append(("run", float(row["run_rel_start_s"]), float(row["run_rel_end_s"]), RUN_COLOR))

    for i, (label, s, e, color) in enumerate(bars):
        ax.barh(i, e - s, left=s, height=0.6, color=color, edgecolor=color)
        ax.text(e + 0.05, i, f"{label}: {s:.2f}–{e:.2f}s ({e - s:.2f}s)",
                va="center", fontsize=9)

    ax.set_yticks(range(len(bars)))
    ax.set_yticklabels([b[0] for b in bars])
    ax.set_xlabel("Time since pull start (s)")
    ax.set_title(f"Diagnostics: {MODE}, {N_ALLOTMENTS} allotments, "
                 f"prefetch async=0, log_file_access=true")
    ax.invert_yaxis()
    ax.grid(True, linestyle="--", alpha=0.3, axis="x")
    ax.set_xlim(0, max(b[2] for b in bars) * 1.25)

    figure_footer(fig, model, base_image)
    fig.tight_layout(rect=[0, 0.08, 1, 1])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    save_figure(fig, out_path)


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(True)
    cfg = load_config()
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    model, base_image = EXPERIMENT

    log.result(f"\n===== Diagnostics =====")
    log.result(f"  model:       {model}")
    log.result(f"  base image:  {base_image}")
    log.result(f"  mode:        {MODE}")
    log.result(f"  allotments:  {N_ALLOTMENTS}")
    log.result(f"  config:      {CONFIG_OVERRIDES}")
    log.result(f"  output ts:   {execution_ts}")

    if not _check_prometheus_alive():
        log.result("  WARNING: prometheus not reachable. Start it with local/start-prometheus.sh "
                   "before running. Continuing — prometheus snapshot will be empty.")

    log.info("Preparing chunks (reusing existing if present)...")
    prepare_local_registry(base_image, registry(cfg))
    chunk_paths = prepare_chunks(model, N_SPLITS)

    base_config = read_base_config()
    config_content = apply_overrides(base_config, CONFIG_OVERRIDES)
    apply_stargz_config(config_content)

    try:
        log.info(f"\n=== Building/pushing {MODE} ===")
        clear_registry(cfg, preserve_base=True)
        prepare_local_registry(base_image, registry(cfg))
        prepare_mode(MODE, chunk_paths, base_image, cfg)

        clear_stargz_cache()

        # Pull
        image = pull_name(MODE, base_image, cfg, N_ALLOTMENTS)
        log.info(f"\n=== Pulling: {image} ===")
        pull_start_s = time.time()
        pull_t = _timed_pull([
            "sudo", "ctr-remote", "images", "rpull", "--plain-http",
            "--use-containerd-labels", image,
        ])
        pull_end_s = pull_start_s + pull_t
        log.result(f"  pull: {pull_t:.2f}s")

        # Run
        name = f"diag-{uuid.uuid4().hex[:8]}"
        log.info(f"\n=== Running: {name} ===")
        run_start_s = time.time()
        run_t = _timed_run([
            "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
            image, name, *_run_cmd(N_ALLOTMENTS),
        ])
        run_end_s = run_start_s + run_t
        log.result(f"  run: {run_t:.2f}s")

        # Drain prefetch
        events = poll_until_prefetch_done(pull_start_s)
        span = prefetch_span(events)
        if span:
            log.result(f"  prefetch span: {span[0] - pull_start_s:.2f}s → "
                       f"{span[1] - pull_start_s:.2f}s ({len(events)} layers)")
        else:
            log.result("  prefetch span: none")

        logs_dir = _diag_logs_dir(execution_ts)

        # Journal
        journal_path = os.path.join(logs_dir, "journal.json")
        save_stargz_run_log(
            pull_start_s,
            max(run_end_s, span[1] if span else run_end_s),
            journal_path,
        )

        # Phase timings
        timings = {
            "pull_start_s": pull_start_s,
            "pull_end_s": pull_end_s,
            "prefetch_start_s": span[0] if span else None,
            "prefetch_end_s": span[1] if span else None,
            "run_start_s": run_start_s,
            "run_end_s": run_end_s,
            "execution_ts": execution_ts,
            "mode": MODE,
            "n_allotments": N_ALLOTMENTS,
            "config_label": CONFIG_LABEL,
            "config": CONFIG_OVERRIDES,
            "model": model,
            "base_image": base_image,
        }
        timings_path = os.path.join(logs_dir, "timings.json")
        os.makedirs(logs_dir, exist_ok=True)
        with open(timings_path, "w") as f:
            json.dump(timings, f, indent=2)
        log.info(f"  Saved timings → {timings_path}")

        # Prometheus snapshot, padded ±5s/+30s around the run window
        prom_path = os.path.join(logs_dir, "prometheus.json")
        q_start = pull_start_s - 5
        q_end = (span[1] if span else run_end_s) + 30
        _snapshot_prometheus(q_start, q_end, prom_path)

        # CSV
        ref = pull_start_s
        row = {
            "mode": MODE,
            "config": CONFIG_LABEL,
            "n_allotments": N_ALLOTMENTS,
            "pull_rel_start_s": 0.000,
            "pull_rel_end_s": round(pull_end_s - ref, 3),
            "prefetch_rel_start_s": round(span[0] - ref, 3) if span else "",
            "prefetch_rel_end_s": round(span[1] - ref, 3) if span else "",
            "run_rel_start_s": round(run_start_s - ref, 3),
            "run_rel_end_s": round(run_end_s - ref, 3),
        }
        csv_path = os.path.join(_diag_run_dir(execution_ts), "diagnostics.csv")
        write_csv(csv_path, list(row.keys()), [row])
        log.info(f"  Saved CSV → {csv_path}")

        # Chart
        chart_path = os.path.join(_diag_charts_dir(execution_ts), "timeline.png")
        _plot_timeline(row, model, base_image, chart_path)

        log.result(f"\n===== Diagnostics complete =====")
        log.result(f"  results: {_diag_run_dir(execution_ts)}")
        log.result(f"  charts:  {_diag_charts_dir(execution_ts)}")
        log.result(f"  logs:    {logs_dir}")
    finally:
        log.info("\n=== Restoring base stargz config ===")
        apply_stargz_config(base_config)


if __name__ == "__main__":
    main()
