import os

from shared.config import EnvConfig
from shared.model import download_model, split_model
from shared.artifacts import write_2dfs_json, create_stargz_dockerfile, create_base_dockerfile
from shared.registry import plain_base_image
from shared import fs, paths

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def clear_chunks() -> None:
    fs.clear_dir(paths.chunks_dir(SCRIPT_DIR))


def prepare(model_name: str, num_splits: int, source_image: str = "", cfg: EnvConfig = None) -> list[str]:
    shard_paths = download_model(model_name, SCRIPT_DIR)
    chunk_paths = split_model(shard_paths, num_splits, SCRIPT_DIR)
    write_2dfs_json(chunk_paths, SCRIPT_DIR)
    create_stargz_dockerfile(chunk_paths, plain_base_image(source_image, cfg), SCRIPT_DIR)
    create_base_dockerfile(chunk_paths, plain_base_image(source_image, cfg), SCRIPT_DIR)
    return chunk_paths
