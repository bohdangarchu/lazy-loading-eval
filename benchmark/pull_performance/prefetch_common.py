import json
import re
import subprocess
import time
from dataclasses import dataclass

from shared import log
from shared.services import collect_stargz_journal_since
from pull_performance.images import pull_name_2dfs_stargz, pull_name_2dfs_stargz_zstd
from pull_performance.prepare import prepare_2dfs_stargz, prepare_2dfs_stargz_zstd

POLL_INTERVAL_S = 2          # how often to check journal during prefetch
PREFETCH_IDLE_S = 30         # stop when no new prefetch_total events for this long
PREFETCH_TIMEOUT_S = 300     # hard stop


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


def poll_until_prefetch_done(pull_start_s: float) -> list[LayerPrefetchEvent]:
    """Poll until no new prefetch_total events appear for PREFETCH_IDLE_S seconds."""
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
        elif time.time() - last_change_t >= PREFETCH_IDLE_S:
            log.info(f"  Idle after {seen} prefetch_total events")
            return parse_prefetch_events(entries)

    log.info(f"  Prefetch timeout ({PREFETCH_TIMEOUT_S}s), collecting what we have")
    entries = collect_stargz_journal_since(pull_start_s)
    return parse_prefetch_events(entries)


def parse_prefetch_events(entries: list[dict]) -> list[LayerPrefetchEvent]:
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


def prefetch_span(events: list[LayerPrefetchEvent]) -> tuple[float, float] | None:
    """Reduce per-layer events to a single (start_s, end_s) span, or None if empty."""
    if not events:
        return None
    return min(e.start_s for e in events), max(e.end_s for e in events)


# ── pull / prepare helpers ─────────────────────────────────────────


def pull_name(mode: str, source_image: str, cfg, n: int) -> str:
    if mode == "2dfs-stargz":
        return pull_name_2dfs_stargz(source_image, cfg, n)
    if mode == "2dfs-stargz-zstd":
        return pull_name_2dfs_stargz_zstd(source_image, cfg, n)
    raise ValueError(f"Unknown mode: {mode}")


def rpull(image: str) -> None:
    subprocess.run(
        ["sudo", "ctr-remote", "images", "rpull", "--plain-http", "--use-containerd-labels", image],
        check=True, capture_output=not log.VERBOSE,
    )


def rpull_noprefetch(image: str) -> None:
    subprocess.run(
        ["sudo", "ctr-remote", "images", "rpull", "--plain-http", image],
        check=True, capture_output=not log.VERBOSE,
    )


def prepare_mode(mode: str, chunk_paths: list[str], source_image: str, cfg) -> None:
    if mode == "2dfs-stargz":
        prepare_2dfs_stargz(chunk_paths, source_image, cfg)
    elif mode == "2dfs-stargz-zstd":
        prepare_2dfs_stargz_zstd(chunk_paths, source_image, cfg)
    else:
        raise ValueError(f"Unknown mode: {mode}")
