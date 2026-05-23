import os

from shared import fs, log, paths
from shared.artifacts import (
    create_base_dockerfile, create_stargz_dockerfile, write_2dfs_json,
)
from shared.config import EnvConfig
from shared.registry import plain_base_image
from shared.split_llm import (
    compute_optimal_params, copy_splits_to_work_dir, ensure_splits,
    repack, split_llm_slug, target_mb_for_n,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPLIT_REPO = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "..", "split-llm-simple")
)


def clear_chunks(model_name: str | None = None) -> None:
    if model_name is None:
        fs.clear_dir(paths.chunks_dir(SCRIPT_DIR))
    else:
        fs.clear_dir(paths.model_chunks_dir(SCRIPT_DIR, model_name))


def prepare(
    model_name: str, max_allowed_splits: int, num_layers: int,
    source_image: str = "", cfg: EnvConfig = None,
) -> list[list[str]]:
    """Produce 2dfs.json + Dockerfiles for one (model, capacity) build.

    Flow:
      1. compute_optimal_params reads safetensors headers from HF and returns
         N_max (largest bucket count that still satisfies target ≥ M, where M
         is the largest single tensor — typically the embedding).
      2. max_allowed_splits caps N_max. The actual splitter run uses
         N_eff = min(N_max, max_allowed_splits) so the splitter cache is keyed
         on whichever is smaller. num_layers (capacity-driven) is then
         re-packed in-memory below — no re-splitting per capacity.
      3. ensure_splits invokes ../split-llm-simple via its own venv (cached on
         manifest.json).
      4. copy_splits_to_work_dir copies into benchmark/build_performance/chunks/
         so all referenced paths stay inside the buildkit context.
      5. repack packs the per-tensor files into `num_layers` buckets via best-
         fit. As long as num_layers ≤ N_eff the result is balanced.

    Returns the groups (one list of file paths per allotment/bucket), in the
    same order as the COPY layers in the resulting Dockerfile and the
    allotments in 2dfs.json. groups[0] is the bottommost image layer,
    groups[-1] is the topmost. Used by measure_rebuild for bucket-level
    mutation; measure can ignore it.
    """
    # Cache optimal params alongside splits_output so a wipe of that folder
    # invalidates both in one go.
    params_cache_dir = os.path.join(
        SPLIT_REPO, "splits_output", split_llm_slug(model_name),
    )
    N_max, T_bytes, _ = compute_optimal_params(
        model_name, cache_dir=params_cache_dir,
    )
    N_eff = min(N_max, max_allowed_splits)
    target_mb = target_mb_for_n(T_bytes, N_eff)

    splits_dir = ensure_splits(model_name, SPLIT_REPO, N_eff, target_mb)
    chunks_dir = paths.model_chunks_dir(SCRIPT_DIR, model_name)
    safetensor_paths = copy_splits_to_work_dir(splits_dir, chunks_dir)

    if num_layers > N_eff:
        log.info(
            f"num_layers={num_layers} > N_eff={N_eff}; clamping. Equal-size "
            f"invariant only holds for num_layers ≤ N_eff."
        )
        num_layers = N_eff

    groups = repack(safetensor_paths, num_layers)

    # Carry the small model-metadata files (config, tokenizer, generation
    # config, ...) into the image too, so the served model is self-sufficient.
    # Pin them to bucket 0 — small, present in every layout, never the
    # bottleneck. Excludes manifest.json which is only a cache key.
    metadata_files = [
        os.path.join(chunks_dir, f) for f in os.listdir(chunks_dir)
        if not f.endswith(".safetensors") and f != "manifest.json"
    ]
    if groups:
        groups[0] = sorted(metadata_files) + groups[0]

    write_2dfs_json(groups, SCRIPT_DIR)
    create_stargz_dockerfile(groups, plain_base_image(source_image, cfg), SCRIPT_DIR)
    create_base_dockerfile(groups, plain_base_image(source_image, cfg), SCRIPT_DIR)
    return groups


def preview_packings(
    model_name: str, num_layers_list: list[int],
) -> list[list[list[str]]]:
    """Repack the already-prepared chunks into N buckets for each N in num_layers_list."""
    chunks_dir = paths.model_chunks_dir(SCRIPT_DIR, model_name)
    safetensor_paths = sorted(
        os.path.join(chunks_dir, f) for f in os.listdir(chunks_dir)
        if f.endswith(".safetensors")
    )
    if not safetensor_paths:
        raise RuntimeError(
            f"No .safetensors in {chunks_dir} — call prepare() before "
            f"preview_packings()."
        )

    metadata_files = sorted(
        os.path.join(chunks_dir, f) for f in os.listdir(chunks_dir)
        if not f.endswith(".safetensors") and f != "manifest.json"
    )

    n_available = len(safetensor_paths)
    results: list[list[list[str]]] = []
    for n in num_layers_list:
        n_eff = min(max(1, n), n_available)
        groups = repack(safetensor_paths, n_eff)
        if groups:
            groups[0] = metadata_files + groups[0]
        results.append(groups)
    return results


def print_packing_table(
    model_name: str, max_allowed_splits: int,
    labels: list[str], num_layers_list: list[int],
) -> None:
    """Print per-label (#allotments, allotment sizes in MB)."""
    packings = preview_packings(model_name, num_layers_list)

    log.result(
        f"\n=== Packing preview: {model_name} "
        f"(max_allowed_splits={max_allowed_splits}) ==="
    )
    log.result(f"{'label':>10}  {'allotments':>11}  sizes (MB)")
    log.result("-" * 60)
    for label, groups in zip(labels, packings):
        sizes_mb = [
            sum(os.path.getsize(p) for p in g) / (1024 ** 2) for g in groups
        ]
        sizes_str = "[" + ", ".join(f"{s:.1f}" for s in sizes_mb) + "]"
        log.result(f"{label:>10}  {len(groups):>11}  {sizes_str}")
