import log
from prepare import prepare

MODEL = "openai-community/gpt2"  # ~500 MB safetensors
NUM_SPLITS = 10
BASE_SPLITS = [2]
IS_LOCAL = True
VERBOSE = False


def main():
    log.set_verbose(VERBOSE)
    log.info(f"Model: {MODEL}")
    log.info(f"Splits (2dfs/stargz): {NUM_SPLITS}")
    log.info(f"Splits (base): {BASE_SPLITS}")

    prepare(MODEL, NUM_SPLITS, BASE_SPLITS, IS_LOCAL)


if __name__ == "__main__":
    main()
