#!/usr/bin/env python3

"""
Minimal Stargz Pull Time Evaluation
Compares pull time between standard and stargz Python images
"""

import time
import subprocess

def cleanup_image(image):
    """Remove image if exists"""
    subprocess.run(f"ctr image rm {image}", shell=True, capture_output=True)

def pull_standard_image(image):
    """Pull standard image with ctr"""
    start = time.time()
    result = subprocess.run(f"ctr i pull {image}", shell=True, capture_output=True, text=True)
    duration = time.time() - start

    if result.returncode != 0:
        raise Exception(f"Pull failed: {result.stderr}")
    return duration

def pull_stargz_image(image):
    """Pull stargz image with ctr-remote"""
    start = time.time()
    result = subprocess.run(f"ctr-remote i rpull {image}", shell=True, capture_output=True, text=True)
    duration = time.time() - start

    if result.returncode != 0:
        raise Exception(f"Pull failed: {result.stderr}")
    return duration

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

    # Results
    if standard_time and stargz_time:
        speedup = standard_time / stargz_time if stargz_time > 0 else 0

        print(f"\nResults:")
        print(f"Standard avg: {standard_time:.2f}s")
        print(f"Stargz avg:   {stargz_time:.2f}s")
        print(f"Speedup:      {speedup:.2f}x")

if __name__ == "__main__":
    main()