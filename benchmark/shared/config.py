import os
from dataclasses import dataclass
from typing import Optional
import yaml


@dataclass
class EnvConfig:
    registry: str
    tdfs_home_dir: Optional[str]
    tmpdir: Optional[str]
    tdfs_binary: str
    full_cache_wipe: bool
    build_cooldown: int
    pull_cooldown: int
    build_max_splits: int
    build_n_runs: int
    build_with_resource: bool
    rebuild_n_splits: int
    rebuild_n_runs: int
    rebuild_r_values: list[int]
    pull_n_splits: int
    pull_base_splits: list[int]
    pull_n_runs: int
    refresh_n_splits: int
    refresh_n_runs: int


_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config() -> EnvConfig:
    with open(_CONFIG_PATH) as f:
        data = yaml.safe_load(f)
    return EnvConfig(**data)
