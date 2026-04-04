#!/usr/bin/env python3
import gzip
import os
import tempfile

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "openai-community--gpt2-medium")
LEVELS = [-1, 1]
CHUNK = 1024 * 1024


def compress_to_temp(src: str, level: int) -> int:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    try:
        with open(src, "rb") as f_in, gzip.open(tmp_path, "wb", compresslevel=level) as f_out:
            while chunk := f_in.read(CHUNK):
                f_out.write(chunk)
        return os.path.getsize(tmp_path)
    finally:
        os.remove(tmp_path)


def fmt(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def main():
    files = sorted(f for f in os.listdir(MODEL_DIR) if f.endswith(".safetensors"))
    if not files:
        print(f"No .safetensors files found in {MODEL_DIR}")
        return

    col = 14
    header = f"{'file':<40}  {'original':>{col}}" + "".join(f"  {'level '+str(l):>{col}}  {'ratio '+str(l):>{col}}" for l in LEVELS)
    print(header)
    print("-" * len(header))

    totals = {"orig": 0, **{l: 0 for l in LEVELS}}

    for fname in files:
        path = os.path.join(MODEL_DIR, fname)
        orig = os.path.getsize(path)
        totals["orig"] += orig
        row = f"{fname:<40}  {fmt(orig):>{col}}"
        for lvl in LEVELS:
            print(f"  compressing {fname} at level {lvl}...", end="\r")
            size = compress_to_temp(path, lvl)
            totals[lvl] += size
            ratio = size / orig * 100
            row += f"  {fmt(size):>{col}}  {ratio:>{col-1}.1f}%"
        print(row)

    print("-" * len(header))
    row = f"{'TOTAL':<40}  {fmt(totals['orig']):>{col}}"
    for lvl in LEVELS:
        ratio = totals[lvl] / totals["orig"] * 100
        row += f"  {fmt(totals[lvl]):>{col}}  {ratio:>{col-1}.1f}%"
    print(row)


if __name__ == "__main__":
    main()
