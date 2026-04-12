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

def config_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "config")

def config_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "config")

def rebuild_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "rebuild")

def rebuild_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "rebuild")

def compression_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "compression")

def compression_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "compression")

def build_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "build")

def build_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "build")

def resource_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "resource")

def resource_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "resource")

def resource_cpu_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "resource", "cpu")

def resource_ram_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "resource", "ram")


# ── output file paths ──────────────────────────────────────────────

def build_csv_path(base_dir: str, model: str, base_image: str) -> str:
    return os.path.join(build_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_{now_ts()}.csv")

def build_chart_path(base_dir: str, model: str, base_image: str, n_splits: int) -> str:
    return os.path.join(build_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_stages_{n_splits}_{now_ts()}.png")

def resource_csv_path(base_dir: str, model: str, base_image: str, max_splits: int) -> str:
    return os.path.join(resource_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_resource_splits_{max_splits}_{now_ts()}.csv")

def resource_chart_path(base_dir: str, model: str, base_image: str, max_splits: int) -> str:
    return os.path.join(resource_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_resource_splits_{max_splits}_{now_ts()}.png")

def rebuild_csv_path(base_dir: str, model: str, base_image: str, n: int) -> str:
    return os.path.join(rebuild_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_rebuild_n{n}_{now_ts()}.csv")

def rebuild_chart_path(base_dir: str, model: str, base_image: str, n: int) -> str:
    return os.path.join(rebuild_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_rebuild_n{n}_{now_ts()}.png")

def compression_csv_path(base_dir: str, model: str, base_image: str, max_splits: int) -> str:
    return os.path.join(compression_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_splits_{max_splits}_{now_ts()}.csv")

def compression_chart_path(base_dir: str, model: str, base_image: str, max_splits: int) -> str:
    return os.path.join(compression_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_splits_{max_splits}_{now_ts()}.png")

def measure_config_csv_path(base_dir: str, model: str, base_image: str, mode: str, n_splits: int) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_results_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}_splits_{n_splits}_{now_ts()}.csv")

def measure_config_chart_path(base_dir: str, model: str, base_image: str, mode: str, n_splits: int) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_charts_dir(base_dir), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}_splits_{n_splits}_{now_ts()}.png")
