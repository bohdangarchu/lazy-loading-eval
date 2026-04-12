import os


# ── private slug helpers ───────────────────────────────────────────
# Mirrors model_slug() in shared/model.py and image_slug() in shared/registry.py.
# Defined here to avoid circular imports (model.py imports paths.py).

def _ms(model: str) -> str:
    return model.replace("/", "--")


def _is(base_image: str) -> str:
    name = base_image.rsplit("/", 1)[-1]
    if ":" not in name:
        name += ":latest"
    return name.replace(":", "-")


# ── timestamp ──────────────────────────────────────────────────────

def now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


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

def chunks_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "chunks")

def model_chunks_dir(base_dir: str, model: str) -> str:
    return os.path.join(base_dir, "chunks", _ms(model))

def models_dir(base_dir: str, model: str) -> str:
    return os.path.join(base_dir, "models", _ms(model))


# ── artifact paths ─────────────────────────────────────────────────

def tdfs_json_path(base_dir: str) -> str:
    return os.path.join(base_dir, "2dfs.json")

def stargz_dockerfile_path(base_dir: str) -> str:
    return os.path.join(base_dir, "Dockerfile.stargz")

def base_dockerfile_path(base_dir: str) -> str:
    return os.path.join(base_dir, "Dockerfile.base")


# ── output file paths ──────────────────────────────────────────────
# All accept an optional ts; if omitted, now_ts() is called at write time.

def pull_csv_path(base_dir: str, model: str, base_image: str, n_splits: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(pull_results_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_pull_{n_splits}_{ts}.csv")

def pull_chart_path(base_dir: str, model: str, base_image: str, n_splits: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(pull_charts_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_pull_{n_splits}_{ts}.png")

def refresh_csv_path(base_dir: str, model: str, base_image: str, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(refresh_results_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_refresh_{ts}.csv")

def refresh_chart_path(base_dir: str, model: str, base_image: str, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(refresh_charts_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_refresh_{ts}.png")

def stargz_config_csv_path(base_dir: str, model: str, base_image: str, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(stargz_config_results_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_{ts}.csv")

def stargz_config_chart_path(base_dir: str, model: str, base_image: str, mode: str, ts: str | None = None) -> str:
    ts = ts or now_ts()
    mode_slug = mode.replace("-", "_")
    return os.path.join(stargz_config_charts_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_{mode_slug}_{ts}.png")

def build_config_csv_path(base_dir: str, model: str, base_image: str, mode: str, ts: str | None = None) -> str:
    ts = ts or now_ts()
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_results_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_{mode_slug}_{ts}.csv")

def build_config_chart_path(base_dir: str, model: str, base_image: str, mode: str, ts: str | None = None) -> str:
    ts = ts or now_ts()
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_charts_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_{mode_slug}_{ts}.png")

def build_csv_path(base_dir: str, model: str, base_image: str, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(build_results_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_{ts}.csv")

def build_chart_path(base_dir: str, model: str, base_image: str, n_splits: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(build_charts_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_stages_{n_splits}_{ts}.png")

def resource_csv_path(base_dir: str, model: str, base_image: str, max_splits: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(resource_results_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_resource_splits_{max_splits}_{ts}.csv")

def resource_chart_path(base_dir: str, model: str, base_image: str, max_splits: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(resource_charts_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_resource_splits_{max_splits}_{ts}.png")

def rebuild_csv_path(base_dir: str, model: str, base_image: str, n: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(rebuild_results_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_rebuild_n{n}_{ts}.csv")

def rebuild_chart_path(base_dir: str, model: str, base_image: str, n: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(rebuild_charts_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_rebuild_n{n}_{ts}.png")

def compression_csv_path(base_dir: str, model: str, base_image: str, max_splits: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(compression_results_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_splits_{max_splits}_{ts}.csv")

def compression_chart_path(base_dir: str, model: str, base_image: str, max_splits: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    return os.path.join(compression_charts_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_splits_{max_splits}_{ts}.png")

def measure_config_csv_path(base_dir: str, model: str, base_image: str, mode: str, n_splits: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_results_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_{mode_slug}_splits_{n_splits}_{ts}.csv")

def measure_config_chart_path(base_dir: str, model: str, base_image: str, mode: str, n_splits: int, ts: str | None = None) -> str:
    ts = ts or now_ts()
    mode_slug = mode.replace("-", "_")
    return os.path.join(config_charts_dir(base_dir), f"{_ms(model)}_{_is(base_image)}_{mode_slug}_splits_{n_splits}_{ts}.png")
