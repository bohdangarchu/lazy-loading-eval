import os
import socket
import time

from shared import log, notify, paths
from shared.config import load_config
from shared.model import cleanup_build_artifacts, cleanup_pull_artifacts

import build_performance.measure as bm
import build_performance.measure_rebuild as bmr
import pull_performance.measure as pm

_BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))


def run_phases():
    cfg = load_config()

    log.result("=== Phase 1: Build Performance ===")
    bm.main()

    log.result("=== Phase 2: Build Rebuild Performance ===")
    bmr.main()

    log.result("=== Transition: clearing build artifacts ===")
    cleanup_build_artifacts(paths.build_perf_dir(_BENCHMARK_DIR), cfg)

    log.result("=== Phase 3: Pull Performance ===")
    pm.main()

    log.result("=== Final cleanup ===")
    cleanup_pull_artifacts(pm.EXPERIMENTS, paths.pull_perf_dir(_BENCHMARK_DIR), cfg)


def main():
    log.set_verbose(True)
    host = socket.gethostname()
    start = time.time()
    try:
        run_phases()
    except BaseException as e:
        mins = (time.time() - start) / 60
        notify.notify(
            f"run.py FAILED on {host} after {mins:.1f} min\n{type(e).__name__}: {e}",
            title="Benchmark failed",
            tags="x",
            priority="high",
        )
        raise

    mins = (time.time() - start) / 60
    notify.notify(
        f"run.py finished OK on {host} in {mins:.1f} min",
        title="Benchmark done",
        tags="white_check_mark",
    )


if __name__ == "__main__":
    main()
