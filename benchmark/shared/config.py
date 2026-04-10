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


_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config() -> EnvConfig:
    with open(_CONFIG_PATH) as f:
        data = yaml.safe_load(f)
    return EnvConfig(**data)
