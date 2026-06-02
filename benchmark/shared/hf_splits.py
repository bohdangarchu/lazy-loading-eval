import argparse
import os

from dotenv import load_dotenv
from huggingface_hub import HfApi, snapshot_download

from shared import log
from shared.split_llm import _SPLIT_REPO, run_split_llm, split_llm_slug

load_dotenv()


def splits_output_dir(model: str) -> str:
    """Local splits cache path for a model (where run_split_llm writes/reads)."""
    return os.path.join(_SPLIT_REPO, "splits_output", split_llm_slug(model))


def _api() -> HfApi:
    token = os.getenv("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN not set (put it in .env)")
    return HfApi(token=token)


def default_repo_id(model: str, api: HfApi) -> str:
    return f"{api.whoami()['name']}/llm-splits-{split_llm_slug(model)}"


def upload_splits(model: str, repo: str | None = None, public: bool = False, do_split: bool = True) -> str:
    """Split `model` (cached via run_split_llm) and push its splits dir to an HF
    dataset repo. Returns the repo id. Set do_split=False to upload only existing
    local splits."""
    api = _api()
    splits_dir = run_split_llm(model) if do_split else splits_output_dir(model)
    if not os.path.isfile(os.path.join(splits_dir, "field.json")):
        raise RuntimeError(f"No splits at {splits_dir} (run split_llm first)")

    repo_id = repo or default_repo_id(model, api)
    api.create_repo(repo_id, repo_type="dataset", private=not public, exist_ok=True)
    log.info(f"Uploading {splits_dir} -> dataset {repo_id} ({'public' if public else 'private'})")
    api.upload_large_folder(repo_id=repo_id, folder_path=splits_dir, repo_type="dataset")
    log.result(f"Uploaded: https://huggingface.co/datasets/{repo_id}")
    return repo_id


def download_splits(model: str, repo: str | None = None, dest: str | None = None) -> str:
    """Download a dataset repo of splits into the local splits cache and return its
    path. Defaults to splits_output_dir(model) so the benchmark flow (run_split_llm
    + copy_splits_to_work_dir) cache-hits instead of re-splitting."""
    api = _api()
    repo_id = repo or default_repo_id(model, api)
    dest = dest or splits_output_dir(model)
    log.info(f"Downloading dataset {repo_id} -> {dest}")
    snapshot_download(repo_id=repo_id, repo_type="dataset", local_dir=dest, token=api.token)
    log.result(f"Downloaded splits to {dest}")
    return dest


def main():
    """CLI: split a model and upload its splits to an HF dataset repo
    (--download to pull instead). Run from benchmark/: python -m shared.hf_splits <model>"""
    parser = argparse.ArgumentParser()
    parser.add_argument("model", help="HF model id (slug) to split + upload")
    parser.add_argument("--repo", help="Repo id (default <user>/llm-splits-<slug>)")
    parser.add_argument("--public", action="store_true", help="Public repo (default private)")
    parser.add_argument("--no-split", action="store_true", help="Upload existing splits; skip split_llm")
    parser.add_argument("--download", action="store_true", help="Download instead of upload")
    args = parser.parse_args()

    if args.download:
        download_splits(args.model, repo=args.repo)
    else:
        upload_splits(args.model, repo=args.repo, public=args.public, do_split=not args.no_split)


if __name__ == "__main__":
    main()
