import os

from shared.config import EnvConfig
from shared.model import download_model, split_model
from shared.artifacts import (
    write_2dfs_json, create_stargz_dockerfile, create_base_dockerfile,
    chunks_to_groups,
)
from shared.registry import plain_base_image
from shared import fs, paths

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def clear_chunks() -> None:
    fs.clear_dir(paths.chunks_dir(SCRIPT_DIR))


def prepare(
    model_name: str, max_allowed_splits: int, num_layers: int,
    source_image: str = "", cfg: EnvConfig = None,
) -> list[str]:
    shard_paths = download_model(model_name, SCRIPT_DIR)
    chunk_paths = split_model(shard_paths, max_allowed_splits, SCRIPT_DIR)
    groups = chunks_to_groups(chunk_paths, num_layers)
    write_2dfs_json(groups, SCRIPT_DIR)
    create_stargz_dockerfile(groups, plain_base_image(source_image, cfg), SCRIPT_DIR)
    create_base_dockerfile(groups, plain_base_image(source_image, cfg), SCRIPT_DIR)
    return chunk_paths
