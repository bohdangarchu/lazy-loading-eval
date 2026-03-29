import os

BASE_IMAGE_LOCAL = "localhost:5000/python:3.10-esgz"
BASE_IMAGE_REMOTE = "10.10.1.2:5000/python:3.10-esgz"
REGISTRY_LOCAL = "localhost:5000"
REGISTRY_REMOTE = "10.10.1.2:5000"


def base_image(is_local: bool) -> str:
    return BASE_IMAGE_LOCAL if is_local else BASE_IMAGE_REMOTE


def registry(is_local: bool) -> str:
    return REGISTRY_LOCAL if is_local else REGISTRY_REMOTE


def image_slug(base_img: str) -> str:
    """Extract a short slug from the base image name.

    'localhost:5000/python:3.10-esgz' -> 'python-3.10'
    """
    name = base_img.rsplit("/", 1)[-1]
    name = name.split("-esgz")[0]
    name = name.replace(":", "-")
    return name


def tdfs_cmd(is_local: bool, work_dir: str) -> list[str]:
    if is_local:
        return [os.path.join(work_dir, "tdfs")]
    return ["sudo", "tdfs", "--home-dir", "/mydata/.2dfs"]
