import os

from shared import log, paths
from shared.config import load_config
from shared.model import cleanup_build_artifacts, cleanup_pull_artifacts

import build_performance.measure as bm
import build_performance.measure_rebuild as bmr
import pull_performance.measure as pm
import pull_performance.measure_refresh as pmr

_BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    log.set_verbose(True)
    cfg = load_config()

    log.result("=== Phase 1: Build Performance ===")
    bm.main()

    log.result("=== Phase 2: Build Rebuild Performance ===")
    bmr.main()

    log.result("=== Transition: clearing build artifacts ===")
    cleanup_build_artifacts(paths.build_perf_dir(_BENCHMARK_DIR), cfg)

    log.result("=== Phase 3: Pull Performance ===")
    pm.main()

    log.result("=== Phase 4: Pull Refresh Performance ===")
    pmr.main()

    log.result("=== Final cleanup ===")
    cleanup_pull_artifacts(pm.EXPERIMENTS, paths.pull_perf_dir(_BENCHMARK_DIR), cfg)


if __name__ == "__main__":
    main()
