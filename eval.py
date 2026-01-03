#!/usr/bin/env python3

"""
Minimal Stargz Pull Time Evaluation
Compares pull time between standard and stargz Python images
"""

import time
import subprocess
import uuid

DEBUG_SHELL = False

def cleanup_image(image):
    """Remove image if exists"""
    subprocess.run(f"ctr image rm {image}", shell=True, capture_output=True)

def pull_standard_image(image):
    """Pull standard image with ctr"""
    start = time.time()
    result = subprocess.run(f"ctr i pull {image}", shell=True, text=True, capture_output=not DEBUG_SHELL)
    duration = time.time() - start

    if result.returncode != 0:
        raise Exception(f"Pull failed: {result.stderr}")
    return duration

def pull_stargz_image(image):
    """Pull stargz image with ctr-remote"""
    start = time.time()
    result = subprocess.run(f"ctr-remote i rpull {image}", shell=True, text=True, capture_output=not DEBUG_SHELL)
    duration = time.time() - start

    if result.returncode != 0:
        raise Exception(f"Pull failed: {result.stderr}")
    return duration

def run_standard_image(image):
    container_id = f"bench-{uuid.uuid4().hex[:8]}"
    start = time.time()
    result = subprocess.run(
        f"ctr run --rm {image} {container_id} "
        "python3 -c \"print('ok')\"",
        shell=True,
        text=True,
        capture_output=not DEBUG_SHELL
    )
    duration = time.time() - start

    if result.returncode != 0:
        raise Exception(f"Run failed: {result.stderr}")
    return duration


def run_stargz_image(image):
    container_id = f"bench-{uuid.uuid4().hex[:8]}"
    start = time.time()
    result = subprocess.run(
        f"ctr run --rm --snapshotter=stargz {image} {container_id} "
        "python3 -c \"print('ok')\"",
        shell=True,
        text=True,
        capture_output=not DEBUG_SHELL
    )
    duration = time.time() - start

    if result.returncode != 0:
        raise Exception(f"Run failed: {result.stderr}")
    return duration

def bench_runtime(standard_img, stargz_img):
    print("\nRuntime Comparison (ctr run)")
    print("-" * 50)

    standard_time = None
    stargz_time = None

    try:
        standard_time = run_standard_image(standard_img)
        print(f"  Standard run: {standard_time:.2f}s")
    except Exception as e:
        print(f"  Standard run: FAILED - {e}")

    try:
        stargz_time = run_stargz_image(stargz_img)
        print(f"  Stargz run:   {stargz_time:.2f}s")
    except Exception as e:
        print(f"  Stargz run:   FAILED - {e}")

    return standard_time, stargz_time

def bench_pull_time(standard_img, stargz_img):
    print(f"Pull Time Comparison")
    print(f"Standard: {standard_img}")
    print(f"Stargz:   {stargz_img}")
    print("-" * 50)
    
    standard_time = None
    stargz_time = None

    # Test standard image
    cleanup_image(standard_img)
    try:
        std_time = pull_standard_image(standard_img)
        standard_time = std_time
        print(f"  Standard: {std_time:.2f}s")
    except Exception as e:
        print(f"  Standard: FAILED - {e}")

    # Test stargz image
    cleanup_image(stargz_img)
    try:
        sgz_time = pull_stargz_image(stargz_img)
        stargz_time = sgz_time
        print(f"  Stargz:   {sgz_time:.2f}s")
    except Exception as e:
        print(f"  Stargz:   FAILED - {e}")
    
    return standard_time, stargz_time

def main():
    # Images from stargz benchmarks
    standard_img = "ghcr.io/stargz-containers/python:3.7-org"
    stargz_img = "ghcr.io/stargz-containers/python:3.7-esgz"

    standard_time, stargz_time = bench_pull_time(standard_img, stargz_img)
    run_std, run_sgz = bench_runtime(standard_img, stargz_img)

    # Results
    if standard_time and stargz_time:
        speedup = standard_time / stargz_time if stargz_time > 0 else 0

        print(f"\nResults:")
        print(f"Standard avg: {standard_time:.2f}s")
        print(f"Stargz avg:   {stargz_time:.2f}s")
        print(f"Speedup:      {speedup:.2f}x")
    if run_std and run_sgz:
        print("Run results")
        print(f"Run std: {run_std:.2f}")
        print(f"Run stargz: {run_sgz:.2f}")

if __name__ == "__main__":
    main()