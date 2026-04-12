import os
import shutil


def rmtree(path: str) -> None:
    """Recursively delete a directory. No-op if it doesn't exist."""
    shutil.rmtree(path, ignore_errors=True)


def clear_dir(path: str) -> None:
    """Delete all contents of a directory, recreating it empty."""
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
