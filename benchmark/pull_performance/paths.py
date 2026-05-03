import os


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

def pull_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(pull_results_dir(base_dir), execution_ts)

def pull_charts_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(pull_charts_dir(base_dir), execution_ts)

def refresh_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "refresh")

def refresh_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "refresh")

def refresh_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(refresh_results_dir(base_dir), execution_ts)

def refresh_charts_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(refresh_charts_dir(base_dir), execution_ts)

def stargz_config_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "stargz-config")

def stargz_config_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "stargz-config")

def stargz_config_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(stargz_config_results_dir(base_dir), execution_ts)

def stargz_config_charts_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(stargz_config_charts_dir(base_dir), execution_ts)

def config_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "config")

def config_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "config")

def config_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(config_results_dir(base_dir), execution_ts)

def config_charts_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(config_charts_dir(base_dir), execution_ts)

def prefetch_layered_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "prefetch-layered")

def prefetch_layered_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "prefetch-layered")

def prefetch_layered_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(prefetch_layered_results_dir(base_dir), execution_ts)

def prefetch_layered_charts_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(prefetch_layered_charts_dir(base_dir), execution_ts)

def prefetch_pull_results_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "results", "prefetch-pull")

def prefetch_pull_charts_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "charts", "prefetch-pull")

def prefetch_pull_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(prefetch_pull_results_dir(base_dir), execution_ts)

def prefetch_pull_charts_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(prefetch_pull_charts_dir(base_dir), execution_ts)


# ── output file paths ──────────────────────────────────────────────

def pull_csv_path(base_dir: str, model: str, base_image: str, n_splits: int, execution_ts: str) -> str:
    return os.path.join(pull_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_pull_{n_splits}.csv")

def pull_chart_path(base_dir: str, model: str, base_image: str, n_splits: int, execution_ts: str) -> str:
    return os.path.join(pull_charts_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_pull_{n_splits}.png")

def refresh_csv_path(base_dir: str, model: str, base_image: str, execution_ts: str) -> str:
    return os.path.join(refresh_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_refresh.csv")

def refresh_chart_path(base_dir: str, model: str, base_image: str, execution_ts: str) -> str:
    return os.path.join(refresh_charts_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_refresh.png")

def stargz_config_csv_path(base_dir: str, model: str, base_image: str, execution_ts: str) -> str:
    return os.path.join(stargz_config_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}.csv")

def stargz_config_chart_path(base_dir: str, model: str, base_image: str, mode: str, execution_ts: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(stargz_config_charts_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}.png")

def build_config_csv_path(base_dir: str, model: str, base_image: str, mode: str, execution_ts: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}.csv")

def build_config_chart_path(base_dir: str, model: str, base_image: str, mode: str, execution_ts: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_charts_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}.png")

def prefetch_layered_csv_path(base_dir: str, model: str, base_image: str, execution_ts: str) -> str:
    return os.path.join(prefetch_layered_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}.csv")

def prefetch_layered_chart_path(base_dir: str, model: str, base_image: str, mode: str, execution_ts: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(prefetch_layered_charts_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}.png")

def prefetch_pull_csv_path(base_dir: str, model: str, base_image: str, execution_ts: str) -> str:
    return os.path.join(prefetch_pull_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}.csv")

def prefetch_pull_chart_path(base_dir: str, model: str, base_image: str, mode: str, execution_ts: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(prefetch_pull_charts_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}.png")

def prefetch_layered_logs_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "logs", "prefetch-layered")

def prefetch_layered_logs_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(prefetch_layered_logs_dir(base_dir), execution_ts)

def prefetch_layered_log_path(base_dir: str, model: str, base_image: str, mode: str, n: int, execution_ts: str) -> str:
    mode_slug = mode.replace("-", "_")
    return os.path.join(prefetch_layered_logs_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}_{n}allotments.json")

def stargz_config_logs_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "logs", "stargz-config")

def stargz_config_logs_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(stargz_config_logs_dir(base_dir), execution_ts)

def stargz_config_log_path(base_dir: str, model: str, base_image: str, mode: str, config_label: str, n: int, run: int, execution_ts: str) -> str:
    mode_slug = mode.replace("-", "_")
    label_slug = config_label.replace(" ", "_").replace(",", "").replace("/", "_")
    return os.path.join(stargz_config_logs_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}_{label_slug}_{n}allotments_run{run}.json")

def prefetch_pull_logs_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "logs", "prefetch-pull")

def prefetch_pull_logs_run_dir(base_dir: str, execution_ts: str) -> str:
    return os.path.join(prefetch_pull_logs_dir(base_dir), execution_ts)

def prefetch_pull_log_path(base_dir: str, model: str, base_image: str, mode: str, config_label: str, n: int, run: int, execution_ts: str) -> str:
    mode_slug = mode.replace("-", "_")
    label_slug = config_label.replace(" ", "_").replace(",", "").replace("/", "_")
    return os.path.join(prefetch_pull_logs_run_dir(base_dir, execution_ts), f"{_model_slug(model)}_{_image_slug(base_image)}_{mode_slug}_{label_slug}_{n}allotments_run{run}.json")


# ── artifact directories ──────────────────────────────────────────

def _experiment_dir(base_dir: str, scope: str, execution_ts: str, model: str, base_image: str) -> str:
    return os.path.join(
        base_dir, "artifacts", scope, execution_ts,
        f"{_model_slug(model)}_{_image_slug(base_image)}",
    )

def pull_artifacts_dir(base_dir: str, execution_ts: str, model: str, base_image: str, mode: str, n: int | None = None) -> str:
    root = _experiment_dir(base_dir, "pull", execution_ts, model, base_image)
    if mode == "base":
        if n is None:
            raise ValueError("n required for base mode")
        return os.path.join(root, "base", f"n_{n}")
    return os.path.join(root, mode)

def refresh_artifacts_dir(base_dir: str, execution_ts: str, model: str, base_image: str, build_mode: str) -> str:
    return os.path.join(_experiment_dir(base_dir, "refresh", execution_ts, model, base_image), build_mode)

def build_config_artifacts_dir(base_dir: str, execution_ts: str, model: str, base_image: str, label: str) -> str:
    label_slug = label.replace(" ", "_").replace(",", "").replace("/", "_")
    return os.path.join(_experiment_dir(base_dir, "build-config", execution_ts, model, base_image), label_slug)

def stargz_config_artifacts_dir(base_dir: str, execution_ts: str, model: str, base_image: str, mode: str) -> str:
    return os.path.join(_experiment_dir(base_dir, "stargz-config", execution_ts, model, base_image), mode)

def prefetch_layered_artifacts_dir(base_dir: str, execution_ts: str, model: str, base_image: str, mode: str) -> str:
    return os.path.join(_experiment_dir(base_dir, "prefetch-layered", execution_ts, model, base_image), mode)

def prefetch_pull_artifacts_dir(base_dir: str, execution_ts: str, model: str, base_image: str, mode: str) -> str:
    return os.path.join(_experiment_dir(base_dir, "prefetch-pull", execution_ts, model, base_image), mode)
