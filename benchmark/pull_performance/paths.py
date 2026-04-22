import os

from shared.paths import now_ts


def _model_slug(model: str) -> str:
    return model.replace("/", "--")


def _image_slug(base_image: str) -> str:
    name = base_image.rsplit("/", 1)[-1]
    if ":" not in name:
        name += ":latest"
    return name.replace(":", "-")


# ── directory helpers ──────────────────────────────────────────────

def pull_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "pull")

def pull_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "pull")

def refresh_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "refresh")

def refresh_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "refresh")

def stargz_config_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "stargz-config")

def stargz_config_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "stargz-config")

def config_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "config")

def config_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "config")


# ── output file paths ──────────────────────────────────────────────

def pull_csv_path(base_dir: str, model: str, base_image: str, n_splits: int) -> str:
    return os.path.join(pull_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_pull_{n_splits}_{now_ts()}.csv")

def pull_chart_path(base_dir: str, model: str, base_image: str, n_splits: int) -> str:
    return os.path.join(pull_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_pull_{n_splits}_{now_ts()}.png")

def refresh_csv_path(base_dir: str, model: str, base_image: str) -> str:
    return os.path.join(refresh_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_refresh_{now_ts()}.csv")

def refresh_chart_path(base_dir: str, model: str, base_image: str) -> str:
    return os.path.join(refresh_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_refresh_{now_ts()}.png")

def stargz_config_csv_path(base_dir: str, model: str, base_image: str) -> str:
    return os.path.join(stargz_config_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_{now_ts()}.csv")

def stargz_config_chart_path(base_dir: str, model: str, base_image: str, mode: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(stargz_config_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}_{now_ts()}.png")

def build_config_csv_path(base_dir: str, model: str, base_image: str, mode: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}_{now_ts()}.csv")

def build_config_chart_path(base_dir: str, model: str, base_image: str, mode: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}_{now_ts()}.png")

def prefetch_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "prefetch")

def prefetch_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "prefetch")

def prefetch_csv_path(base_dir: str, model: str, base_image: str) -> str:
    return os.path.join(prefetch_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_{now_ts()}.csv")

def prefetch_chart_path(base_dir: str, model: str, base_image: str, mode: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(prefetch_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}_{now_ts()}.png")
