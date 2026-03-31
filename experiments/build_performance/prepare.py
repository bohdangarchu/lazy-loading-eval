import os

from shared.model import download_model, split_model
from shared.artifacts import write_2dfs_json, create_stargz_dockerfile, create_base_dockerfile
from shared.registry import base_image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def prepare(model_name: str, num_splits: int, source_image: str = "", is_local: bool = True) -> list[str]:
    shard_paths = download_model(model_name, SCRIPT_DIR)
    chunk_paths = split_model(shard_paths, num_splits, SCRIPT_DIR)
    write_2dfs_json(chunk_paths, SCRIPT_DIR)
    create_stargz_dockerfile(chunk_paths, base_image(source_image, is_local), SCRIPT_DIR)
    create_base_dockerfile(chunk_paths, base_image(source_image, is_local), SCRIPT_DIR)
    return chunk_paths
