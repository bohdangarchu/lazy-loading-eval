from prepare import prepare

MODEL = "openai-community/gpt2-medium"  # ~1.5 GB safetensors
MAX_SPLITS = 1

for n in range(1, MAX_SPLITS + 1):
    print(f"\n--- prepare(n={n}) ---")
    chunks = prepare(MODEL, n)
    print(f"Chunks: {chunks}")
