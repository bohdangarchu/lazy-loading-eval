# HuggingFace Models for Lazy Loading Experiment

Sizes are verified safetensors totals from HuggingFace file listings. Update pair = exact model IDs to use for a model update experiment.

| Model ID | Total Size | Shards | Update Pair |
|---|---|---|---|
| `mistralai/Mistral-7B-Instruct-v0.2` | ~14.5 GB | 3 | `mistralai/Mistral-7B-Instruct-v0.2` → `mistralai/Mistral-7B-Instruct-v0.3` |
| `mistralai/Mistral-7B-Instruct-v0.3` | ~14.5 GB | 3 | `mistralai/Mistral-7B-Instruct-v0.2` → `mistralai/Mistral-7B-Instruct-v0.3` |
| `Qwen/Qwen2.5-7B-Instruct` | ~15.2 GB | 4 | `Qwen/Qwen2-7B-Instruct` → `Qwen/Qwen2.5-7B-Instruct` |
| `meta-llama/Llama-3.1-8B-Instruct` | ~16.1 GB | 4 | `meta-llama/Meta-Llama-3-8B-Instruct` → `meta-llama/Llama-3.1-8B-Instruct` |
| `Qwen/Qwen2-VL-7B-Instruct` | ~16.6 GB | 5 | `Qwen/Qwen2-VL-7B-Instruct` → `Qwen/Qwen2.5-VL-7B-Instruct` |
| `google/gemma-7b` | ~17.1 GB | 4 | `google/gemma-7b` → `google/gemma-7b-it` ⚠️ base→instruct, not a version update |
| `microsoft/Phi-3-medium-4k-instruct` | ~27.9 GB | 6 | `microsoft/Phi-3-medium-4k-instruct` → `microsoft/Phi-3-medium-128k-instruct` |
| `google/flan-t5-xxl` | ~45 GB | 5 | — no clean version pair available |

## Notes

- `meta-llama` models are gated — require HuggingFace access approval.
- `google/gemma-7b` is gated — requires Google license acceptance.
- Best candidates for update experiment: **Mistral v0.2→v0.3** (same arch, clean versioning, 14.5 GB) and **Qwen2→Qwen2.5** (both verified, 4 shards).
