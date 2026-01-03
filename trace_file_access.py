import re
import subprocess
from collections import defaultdict
from typing import Dict


OPENAT_RE = re.compile(
    r'openat\([^,]+,\s*"([^"]+)",[^)]*\)\s+=\s+(\d+)'
)
READ_RE = re.compile(
    r'read\((\d+),.*?\)\s+=\s+(\d+)'
)


def parse_strace(stderr_text: str):
    """
    Parse strace stderr and return:
      - file_bytes: dict[path -> bytes read]
      - total_bytes_read: int
    """
    fd_to_path: Dict[int, str] = {}
    file_bytes: Dict[str, int] = defaultdict(int)
    total_bytes_read = 0

    for line in stderr_text.splitlines():

        # successful openat
        m_open = OPENAT_RE.search(line)
        if m_open:
            path, fd = m_open.groups()
            fd_to_path[int(fd)] = path
            continue

        # successful read
        m_read = READ_RE.search(line)
        if m_read:
            fd, nbytes = map(int, m_read.groups())
            if nbytes > 0 and fd in fd_to_path:
                path = fd_to_path[fd]
                file_bytes[path] += nbytes
                total_bytes_read += nbytes

    return file_bytes, total_bytes_read


def get_image_size_bytes(image: str) -> int:
    """
    Parse image size from `ctr image ls` output.
    Works with outputs like:
      ... 1.2 GiB linux/amd64 -
    """
    out = subprocess.check_output(
        ["ctr", "image", "ls"],
        text=True
    )

    size_units = {"B", "KiB", "MiB", "GiB"}

    for line in out.splitlines():
        if image in line:
            parts = line.split()

            for i in range(len(parts) - 1):
                value, unit = parts[i], parts[i + 1]
                if unit in size_units:
                    return parse_size_to_bytes(f"{value}{unit}")

            raise RuntimeError(
                f"Could not find size in ctr image ls line:\n{line}"
            )

    raise RuntimeError(f"Image not found in ctr image ls: {image}")


def parse_size_to_bytes(size: str) -> int:
    """
    Convert ctr size strings like '3.6GiB', '912MiB' to bytes.
    """
    size = size.strip()
    if size.endswith("GiB"):
        return int(float(size[:-3]) * 1024**3)
    if size.endswith("MiB"):
        return int(float(size[:-3]) * 1024**2)
    if size.endswith("KiB"):
        return int(float(size[:-3]) * 1024)
    if size.endswith("B"):
        return int(size[:-1])

    raise ValueError(f"Unrecognized size format: {size}")


def print_report(
    file_bytes: Dict[str, int],
    total_bytes_read: int,
    total_image_bytes: int,
):
    print("\n========== STRACE SPARSITY REPORT ==========\n")

    print(f"Total image size      : {total_image_bytes / 1024 / 1024:.2f} MB")
    print(f"Bytes read by app     : {total_bytes_read / 1024:.2f} KB")
    print(f"Unique files accessed : {len(file_bytes)}")

    ratio = total_bytes_read / total_image_bytes
    print(f"Sparsity ratio        : {ratio * 100:.6f}%")

    print("\n---- Per-file bytes read ----\n")

    for path, nbytes in sorted(
        file_bytes.items(), key=lambda x: x[1], reverse=True
    ):
        print(f"{path:70s} {nbytes / 1024:8.2f} KB")

    print("\n===========================================\n")


def analyze_strace(stderr_text: str, image: str):
    file_bytes, total_bytes_read = parse_strace(stderr_text)
    total_image_bytes = get_image_size_bytes(image)

    print_report(
        file_bytes=file_bytes,
        total_bytes_read=total_bytes_read,
        total_image_bytes=total_image_bytes,
    )
