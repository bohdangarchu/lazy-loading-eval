"""Wrapper around ../split-llm-simple/split_llm.py.

Flow:
  1. compute_optimal_params(model)       — read safetensors headers, return N_max + target.
  2. ensure_splits(model, N_max, target) — run split_llm.py via its own venv; cache by manifest.
  3. copy_splits_to_work_dir(splits_dir, work_dir) — copy into the build context.
  4. repack(safetensor_paths, N)         — best-fit pack into N buckets per capacity %.

Invariant the rest of the file assumes:
  Splits are produced with target ≥ M (largest single tensor). Then every base
  bucket lies in [0.5·target, target] (split_llm's undersize merge guarantees the
  lower bound). Merging neighbors (or best-fit repacking with the same target) for
  lower capacities preserves balance. If the invariant is violated (target < M),
  the embedding becomes an outsized bucket and merged buckets inherit that skew —
  capacity comparisons become unfair.

  compute_optimal_params enforces target ≥ M by construction.
"""

import json
import math
import os
import shutil
import struct
import subprocess

import requests
from dotenv import load_dotenv
from huggingface_hub import HfApi, hf_hub_url

from shared import fs, log

load_dotenv()


def split_llm_slug(model: str) -> str:
    """Matches the slug used by split_llm.py for its output folder."""
    return model.replace("/", "_").replace(":", "_")


# ── 1. params ──────────────────────────────────────────────────────────


def compute_optimal_params(
    model: str, cache_dir: str | None = None,
) -> tuple[int, int, int]:
    """Read safetensors headers from the HF Hub (no download) and compute the
    maximum bucket count that keeps every bucket ≥ largest single tensor.

    Returns (N_max, T_bytes, M_bytes).

    If `cache_dir` is given, persist the result to `<cache_dir>/optimal_params.json`
    and reuse it on subsequent calls. The cache survives across runs and is
    invalidated by wiping the directory (same lifecycle as splits_output).
    """
    cache_path = (
        os.path.join(cache_dir, "optimal_params.json") if cache_dir else None
    )
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                d = json.load(f)
            if d.get("model") == model:
                log.info(
                    f"optimal_params cache hit: {model} "
                    f"N_max={d['N_max']} T={d['T_bytes']/1024**2:.1f} MB "
                    f"M={d['M_bytes']/1024**2:.1f} MB"
                )
                return d["N_max"], d["T_bytes"], d["M_bytes"]
        except (OSError, json.JSONDecodeError, KeyError):
            log.info(f"optimal_params cache unreadable at {cache_path} — recomputing")

    token = os.environ.get("HF_TOKEN")
    api = HfApi()
    files = [
        f for f in api.list_repo_files(model, token=token)
        if f.endswith(".safetensors")
    ]
    if not files:
        raise RuntimeError(f"No .safetensors files in {model}")

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    T = 0
    M = 0
    for f in files:
        url = hf_hub_url(model, f)
        r = requests.get(url, headers={**headers, "Range": "bytes=0-7"}, timeout=30)
        r.raise_for_status()
        hlen = struct.unpack("<Q", r.content)[0]
        r = requests.get(
            url, headers={**headers, "Range": f"bytes=8-{8 + hlen - 1}"}, timeout=30,
        )
        r.raise_for_status()
        hdr = json.loads(r.content)
        for name, meta in hdr.items():
            if name == "__metadata__":
                continue
            start, end = meta["data_offsets"]
            sz = end - start
            T += sz
            if sz > M:
                M = sz

    N_max = max(1, T // M)
    log.info(
        f"{model}: T={T/1024**2:.1f} MB  M={M/1024**2:.1f} MB  N_max={N_max}"
    )

    if cache_dir and cache_path:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(
                {"model": model, "N_max": N_max, "T_bytes": T, "M_bytes": M},
                f, indent=2,
            )
        log.info(f"optimal_params cached at {cache_path}")

    return N_max, T, M


def target_mb_for_n(T_bytes: int, N: int) -> int:
    """Bucket target size (MB) to pass to split_llm.py for N buckets. 2% slack
    absorbs best-fit overshoot."""
    return math.ceil((T_bytes / N) * 1.02 / (1024 ** 2))


# ── 2. invoke splitter ─────────────────────────────────────────────────


def ensure_splits(
    model: str, repo_path: str, max_n_splits: int, target_split_size_mb: int,
) -> str:
    """Run ../split-llm-simple/split_llm.py with the given params, using that
    repo's own .venv. Caches via manifest.json: re-runs only if args differ.

    Returns the splits_output/<slug>/ path.
    """
    slug = split_llm_slug(model)
    out_dir = os.path.join(repo_path, "splits_output", slug)
    manifest_path = os.path.join(out_dir, "manifest.json")

    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                m = json.load(f)
            if (m.get("max_n_splits") == max_n_splits
                    and m.get("target_split_size_mb") == target_split_size_mb):
                log.info(f"Splits cache hit for {model} at {out_dir}")
                return out_dir
            log.info(
                f"Splits cache stale for {model} "
                f"(was max_n_splits={m.get('max_n_splits')}, "
                f"target={m.get('target_split_size_mb')}; "
                f"want {max_n_splits}/{target_split_size_mb}) — regenerating"
            )
        except (OSError, json.JSONDecodeError):
            log.info(f"Splits manifest unreadable at {manifest_path} — regenerating")
        fs.rmtree(out_dir)

    venv_py = os.path.join(repo_path, ".venv/bin/python")
    script = os.path.join(repo_path, "split_llm.py")
    if not os.path.exists(venv_py):
        raise RuntimeError(f"split-llm-simple venv not found at {venv_py}")

    cmd = [
        venv_py, script,
        "--model", model,
        "--max_n_splits", str(max_n_splits),
        "--target_split_size_mb", str(target_split_size_mb),
    ]
    log.info(f"Running split_llm: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=repo_path, check=True)
    log.result(f"Splits generated at {out_dir}")
    return out_dir


# ── 3. copy into build context ─────────────────────────────────────────


def copy_splits_to_work_dir(splits_dir: str, work_dir: str) -> list[str]:
    """Copy .safetensors + model metadata (config/tokenizer/...) from splits_dir
    into work_dir so that 2dfs.json and Dockerfile COPY paths stay inside the
    build context. Skips copy when work_dir/manifest.json already matches.

    Returns sorted absolute paths of the .safetensors files in work_dir.

    Note: file content is duplicated (~T bytes per model). This is intentional —
    keeps splits_output as the durable cache, lets refresh-mode mutations stay
    isolated to work_dir, and avoids buildkit's "outside build context" rejection
    for paths above SCRIPT_DIR. Hardlinks would save the disk but propagate
    mutations back into the cache.
    """
    os.makedirs(work_dir, exist_ok=True)

    src_manifest = os.path.join(splits_dir, "manifest.json")
    dst_manifest = os.path.join(work_dir, "manifest.json")
    if (os.path.exists(dst_manifest) and os.path.exists(src_manifest)
            and _read(src_manifest) == _read(dst_manifest)):
        log.info(f"Chunks up-to-date at {work_dir} — skipping copy")
    else:
        fs.clear_dir(work_dir)
        for f in sorted(os.listdir(splits_dir)):
            # Skip field.json: it describes split-llm's original bucketing,
            # which doesn't match our per-capacity repack and would be
            # misleading inside the image. split_N.json files are kept — they
            # describe semantic groupings (which tensors belong to which
            # logical layer) and remain valid regardless of our physical packing.
            if f == "field.json":
                continue
            src = os.path.join(splits_dir, f)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(work_dir, f))
        log.result(f"Copied splits into {work_dir}")

    return sorted(
        os.path.join(work_dir, f)
        for f in os.listdir(work_dir)
        if f.endswith(".safetensors")
    )


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


# ── 4. capacity-driven repacking ───────────────────────────────────────


def repack(safetensor_paths: list[str], num_buckets: int) -> list[list[str]]:
    """Best-fit pack files into `num_buckets` groups, balanced by file size.

    Same algorithm split_llm.py uses internally, applied to the per-tensor file
    pool — so capacity comparisons see the same packing policy at every N.

    Assumes target ≥ M (largest file) — i.e. num_buckets ≤ N_max from
    compute_optimal_params. Under that invariant the result is balanced. Above
    it, the largest file becomes a forced outlier (the "embedding" case).
    """
    if num_buckets <= 1:
        return [list(safetensor_paths)]

    files_with_sizes = sorted(
        ((p, os.path.getsize(p)) for p in safetensor_paths),
        key=lambda x: x[1], reverse=True,
    )
    T = sum(sz for _, sz in files_with_sizes)
    target = T / num_buckets

    buckets: list[dict] = []
    for path, sz in files_with_sizes:
        best_idx = None
        best_projected = -1
        for i, b in enumerate(buckets):
            projected = b["size"] + sz
            if projected <= target and projected > best_projected:
                best_idx = i
                best_projected = projected

        if best_idx is None:
            if len(buckets) < num_buckets:
                buckets.append({"files": [path], "size": sz})
            else:
                # Forced overflow: bucket can't grow to target without exceeding
                # it. Place into the currently-smallest bucket. Only reachable
                # when num_buckets > N_max (invariant violated).
                target_idx = min(
                    range(len(buckets)), key=lambda i: buckets[i]["size"],
                )
                buckets[target_idx]["files"].append(path)
                buckets[target_idx]["size"] += sz
        else:
            buckets[best_idx]["files"].append(path)
            buckets[best_idx]["size"] += sz

    # Merge undersized buckets (< 0.5 · target) into their smallest neighbor,
    # matching split_llm.py's behavior. Prevents tiny straggler buckets when the
    # tensor mix doesn't pack cleanly into num_buckets.
    min_size = target * 0.5
    while len(buckets) > 1:
        smallest = min(range(len(buckets)), key=lambda i: buckets[i]["size"])
        if buckets[smallest]["size"] >= min_size:
            break
        merge_into = min(
            (i for i in range(len(buckets)) if i != smallest),
            key=lambda i: buckets[i]["size"],
        )
        buckets[merge_into]["files"].extend(buckets[smallest]["files"])
        buckets[merge_into]["size"] += buckets[smallest]["size"]
        del buckets[smallest]

    return [sorted(b["files"]) for b in buckets]
