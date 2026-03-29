from shared.registry import base_image, registry, image_slug


def _slug(is_local: bool) -> str:
    return image_slug(base_image(is_local))


def _reg(is_local: bool) -> str:
    return registry(is_local)


# ── Build targets (used by prepare.py) ─────────────────────────────
# No library/ prefix — tdfs adds it automatically in the registry.


def build_name_2dfs(is_local: bool) -> str:
    return f"{_reg(is_local)}/{_slug(is_local)}-2dfs:latest"


def build_name_2dfs_stargz(is_local: bool) -> str:
    return f"{_reg(is_local)}/{_slug(is_local)}-2dfs-stargz:latest"


def build_name_stargz(is_local: bool) -> str:
    return f"{_reg(is_local)}/{_slug(is_local)}-stargz:latest"


def build_name_base(is_local: bool, num_splits: int) -> str:
    return f"{_reg(is_local)}/{_slug(is_local)}-base-{num_splits}-splits:latest"


# ── Pull references (used by measure.py) ───────────────────────────
# 2dfs/2dfs-stargz need library/ prefix and allotment range tag.
# Allotment range: --0.0.0.{end_col}  pulls columns 0 through end_col.


def pull_name_2dfs(is_local: bool, num_allotments: int) -> str:
    end_col = num_allotments - 1
    return f"{_reg(is_local)}/library/{_slug(is_local)}-2dfs:latest--0.0.0.{end_col}"


def pull_name_2dfs_stargz(is_local: bool, num_allotments: int) -> str:
    end_col = num_allotments - 1
    return f"{_reg(is_local)}/library/{_slug(is_local)}-2dfs-stargz:latest--0.0.0.{end_col}"


def pull_name_stargz(is_local: bool) -> str:
    return build_name_stargz(is_local)


def pull_name_base(is_local: bool, num_splits: int) -> str:
    return build_name_base(is_local, num_splits)
