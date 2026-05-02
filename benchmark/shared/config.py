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
    build_cooldown: int
    pull_cooldown: int
    build_n_runs: int
    build_with_resource: bool
    rebuild_n_runs: int
    pull_n_splits: int
    pull_base_splits: list[int]
    pull_n_runs: int
    refresh_n_splits: int
    refresh_n_runs: int
    stargz_config_n_splits: int
    stargz_config_base_splits: list[int]
    stargz_config_n_runs: int


_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config() -> EnvConfig:
    with open(_CONFIG_PATH) as f:
        data = yaml.safe_load(f)
    return EnvConfig(**data)
