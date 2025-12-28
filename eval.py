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

def main():
    iterations = 1

    # Images from stargz benchmarks
    standard_img = "ghcr.io/bohdangarchu/bert-split:org"
    stargz_img = "ghcr.io/bohdangarchu/bert-split:esgz"

    print(f"Pull Time Comparison ({iterations} iterations)")
    print(f"Standard: {standard_img}")
    print(f"Stargz:   {stargz_img}")
    print("-" * 50)

    standard_times = []
    stargz_times = []

    for i in range(iterations):
        print(f"Iteration {i+1}/{iterations}")

        # Test standard image
        cleanup_image(standard_img)
        try:
            std_time = pull_standard_image(standard_img)
            standard_times.append(std_time)
            print(f"  Standard: {std_time:.2f}s")
        except Exception as e:
            print(f"  Standard: FAILED - {e}")

        # Test stargz image
        cleanup_image(stargz_img)
        try:
            sgz_time = pull_stargz_image(stargz_img)
            stargz_times.append(sgz_time)
            print(f"  Stargz:   {sgz_time:.2f}s")
        except Exception as e:
            print(f"  Stargz:   FAILED - {e}")

    # Results
    if standard_times and stargz_times:
        std_avg = sum(standard_times) / len(standard_times)
        sgz_avg = sum(stargz_times) / len(stargz_times)
        speedup = std_avg / sgz_avg if sgz_avg > 0 else 0

        print(f"\nResults:")
        print(f"Standard avg: {std_avg:.2f}s")
        print(f"Stargz avg:   {sgz_avg:.2f}s")
        print(f"Speedup:      {speedup:.2f}x")
        print(f"Improvement:  {((std_avg-sgz_avg)/std_avg)*100:+.1f}%")

if __name__ == "__main__":
    main()