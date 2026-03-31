from shared.registry import registry, image_slug


# ── Build targets (used by prepare.py) ─────────────────────────────
# No library/ prefix — tdfs adds it automatically in the registry.


def build_name_2dfs(source_image: str, is_local: bool) -> str:
    return f"{registry(is_local)}/{image_slug(source_image)}-2dfs:latest"


def build_name_2dfs_stargz(source_image: str, is_local: bool) -> str:
    return f"{registry(is_local)}/{image_slug(source_image)}-2dfs-stargz:latest"


def build_name_stargz(source_image: str, is_local: bool) -> str:
    return f"{registry(is_local)}/{image_slug(source_image)}-stargz:latest"


def build_name_base(source_image: str, is_local: bool, num_splits: int) -> str:
    return f"{registry(is_local)}/{image_slug(source_image)}-base-{num_splits}-splits:latest"


# ── Pull references (used by measure.py) ───────────────────────────
# 2dfs/2dfs-stargz need library/ prefix and allotment range tag.
# Allotment range: --0.0.0.{end_col}  pulls columns 0 through end_col.


def pull_name_2dfs(source_image: str, is_local: bool, num_allotments: int) -> str:
    end_col = num_allotments - 1
    return f"{registry(is_local)}/library/{image_slug(source_image)}-2dfs:latest--0.0.0.{end_col}"


def pull_name_2dfs_stargz(source_image: str, is_local: bool, num_allotments: int) -> str:
    end_col = num_allotments - 1
    return f"{registry(is_local)}/library/{image_slug(source_image)}-2dfs-stargz:latest--0.0.0.{end_col}"


def pull_name_stargz(source_image: str, is_local: bool) -> str:
    return build_name_stargz(source_image, is_local)


def pull_name_base(source_image: str, is_local: bool, num_splits: int) -> str:
    return build_name_base(source_image, is_local, num_splits)
