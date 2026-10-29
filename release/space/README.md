---
title: har2client leaderboard
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
pinned: false
license: apache-2.0
short_description: Traffic-to-client benchmark, with hidden execution tests
---

# har2client leaderboard

Leaderboard, model comparison, task explorer, case 3 results and findings for the har2client benchmark.
The benchmark tests whether a model can turn recorded API traffic into a working client, and it was built to test
**Beam-1.0-501B-A23B** (Reflection) for the Reflection x Hugging Face "Build with Beam" challenge. Every number is computed from the CSV and
analysis files in `data/`, which are exact copies of the files in the `results/` folder of the dataset repo. See the dataset card for
the method and the limits.

## How Beam was used

- Model `Beam-1.0-501B-A23B` through Reflection's OpenAI-compatible API (`https://api.reflection.ai/openai/v1`), with `reasoning_effort` set to low, medium or high on every call.
- Beam is the model under test in all three cases: case 1 builds an API client from recorded traffic, case 2 probes a live practice server with a tool-calling agent, case 3 repairs a client after the API changed.
- Case 2 uses multi-turn tool calling (`http_request` and `submit_client`); the `reasoning_content` of each turn is sent back. Cases 1 and 3 are one call, with no tools.
- The output is graded by hidden tests on a fresh server. There is no LLM judge. The hypotheses were pre-registered. High effort in cases 1 and 3 is the mean of 3 runs.
- The Model comparison tab puts Beam next to GPT-OSS-120B (low, medium, high) and Qwen3.5-397B-A17B (thinking on).

The benchmark code (task builders, mock servers, grader, RL environment) is public: https://github.com/MohitGoyal09/har2client (Apache-2.0). To rebuild a task and run the grader: clone the repo, `cd har2client && uv sync`, build a task, then `uv run har2client grade TASK_DIR client.py`. This Space is code too: licence Apache-2.0.
The dataset is CC-BY-4.0, with the per-spec caveat for Tier A in its card.

Run locally: `pip install -r requirements.txt && python app.py` (run `release/sync_space_data.py` first to build `data/`).
