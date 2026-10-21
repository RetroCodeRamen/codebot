# Larger local models (experimental path, started 2026-10-20)

Question: can a larger Mixture-of-Experts model, split between CPU and GPU (and, later, streaming experts from the SSD), finish codebot tasks
more reliably, in less total time to a verified result, than ornith:9b? Judged on complete task outcomes under one wall-clock budget, not on
tokens per second. The ornith baseline and its Ollama backend stay as they are; a candidate is opt-in (`codebot bench run --model ... --num-ctx ...`).

Labels: **[M]** measured on this laptop, **[U]** upstream documentation or listings (checked 2026-10-20), **[E]** estimate.
Scripts and launch configurations: `bench/bigmodel/` (`launch.sh`, `screen.py`, `compare.sh`, `checkpoints.py`, the prompts, the Modelfile);
raw results stay local in `bench/results/bigmodel/`.

## 1. The machine (normal operating conditions: the user's Windows VM, browser and desktop keep running)

| | |
|---|---|
| CPU | i7-11375H, 4 cores / 8 threads, AVX-512; about 3.4 GHz all-core under model load at 89-96 C (max 5.0 GHz) [M] |
| RAM | 16 GB (15.4 GiB); with no model loaded 9.8 GB available in the morning, 12-13 GB later in the day once more of the VM had gone to swap [M] |
| Swap | 16 GB swapfile; 8.5 GB in use before any test (3.9 GB of it the VM), swappiness 10 [M] |
| GPU | RTX 3050 Ti Laptop, 4 GB (3.64 GB free to CUDA) [M] |
| SSD | WD Blue SN570 1 TB, PCIe 3.0 x4, 465 GB free; direct reads 2.7 GB/s sequential, 2.36 GB/s random 2 MiB, 1.0 GB/s random 256 KiB [M] |
| Toolchain | no CUDA toolkit, no cmake; Docker has no NVIDIA runtime; `/tmp` is RAM (tmpfs) [M] |

The baseline: Ollama 0.40.3 (private, port 11500) runs its bundled llama.cpp `llama-server` (0.5.0-dev, commit 631109b34, CUDA 13) as
`-c 32768 -np 1 --chat-template chatml --flash-attn auto -b 512 -ub 512`, placement by `--fit` (on by default). ornith:9b (Qwen3.5 9B dense,
Q4_K_M, 5.6 GB) generates at a median 8.5 tokens/s in drafts mode; reading the prompt is 2% of the model time (181 calls). [M]

## 2. Runtimes and projects

- The bundled `llama-server` already has `--cpu-moe`, `--n-cpu-moe N`, `-ot`, `--fit`, `--no-repack`. It finds its GPU backend only when
  started in its `cuda_v13/` folder (as Ollama does) or with `GGML_BACKEND_PATH` set; started elsewhere it silently ran CPU-only. [M]
- Upstream llama.cpp b11755 (commit 704366d, 2026-10-20) ships prebuilt CUDA 12.8 binaries (`tools/llama.cpp/b11755/`, checksums verified)
  and adds `--moe-cache-mib` (PR #29887, merged 2026-10-07: an LRU cache on the GPU for experts kept in host memory; the PR's 1.57-2.20x
  speed-ups are on 24-32 GB GPUs). [U]
- Swap-MoE (llama.cpp patches at commit dd002b91: `--expert-streaming`, keep-recent, prefetch, fixation) reports 7.0 tokens/s for
  Qwen3.5-35B-A3B Q4_K_M (19.7 GB) and 2.0 for 122B-A10B Q2_K on a 13th-gen i7 with 16 GB and no GPU (decode speed at token 400; its expert
  fixation degraded output in its own tests). litemoe: only llama.cpp's own `--n-cpu-moe` works, its expert cache is "in progress".
  KTransformers targets large-RAM servers; it uses the disk for prefix caches, not experts. [U]
- llama-server keeps up to 8 GiB of earlier prompts' KV in RAM by default (`--cache-ram`). With the 30B model that cache grew 2 GB in 6
  requests and went to swap; codebot sends one request at a time and gains nothing from it, so `bin/codebot` now starts Ollama with
  `LLAMA_ARG_CACHE_RAM=0` (its runners inherit it: the runner's own memory stayed at 0.12 GB instead of 0.82 GB after 3 requests). [M]

## 3. Candidates (Hugging Face listings) [U]

| model | active | file | size | fits in RAM + VRAM here? |
|---|---|---|---|---|
| Qwen3-Coder-30B-A3B-Instruct | 3.3B | unsloth UD-IQ3_XXS (rev b17cb02, sha256 d6b85d2b...) | 12.85 GB | no headroom: runs, but pushes the user's programs to swap [M] |
| Qwen3-Coder-30B-A3B-Instruct | 3.3B | Q3_K_S / Q4_K_M | 13.29 / 18.56 GB | no; Q4_K_M only by streaming [E] |
| Qwen3.5-35B-A3B | 3B | UD-IQ3_XXS / Q4_K_M | 13.08 / 22.02 GB | like the coder at IQ3 [E] |
| Qwen3-Coder-Next (80B-A3B) | 3B | UD-IQ2_XXS / UD-IQ3_XXS | 23.3 / 28.5 GB | streaming only [E] |
| Qwen3.5-122B-A10B | 10B | UD-IQ2_XXS / UD-IQ3_XXS | 36.6 / 44.7 GB | streaming only, upper bound [E] |

## 4. Screening Qwen3-Coder-30B-A3B UD-IQ3_XXS [M]

One model loaded, one request at a time, real drafts-mode prompts (`bench/bigmodel/prompts/`), temperature 0.2, seed 1.
Load: 11-13 s; `--fit` puts the attention and dense weights of all layers plus the experts of a few layers on the GPU (2.7 GB used) and maps
the other experts (12.0 GB) from the file.

| config | request | prompt tokens, tok/s | first token | generated, tok/s | wall |
|---|---|---|---|---|---|
| A1 Ollama runtime, `--fit`, 4K context, 4 threads | calc fresh draft | 883 at 45 (cold) | 19.9 s | 1355 at 15.1 | 110 s |
| B1 same, 16K context | calc fresh / calc repair | 883 at 58 / 2234 at 175 | 15.5 / 13.1 s | 1358 at 12.8 / 2262 at 11.7 | 122 / 207 s |
| B1 | level_03 fresh / repair | 1208 at 175 / 3672 at 187 | 7.4 / 20.0 s | 1855 at 12.3 / 2257 at 11.1 | 159 / 223 s |
| C1 B1 + `--cache-ram 0` | calc repair / level_03 repair | 2289 at 74 (cold) / 3720 at 190 | 31 / 20 s | 1163 at 10.4 / 2321 at 11.4 | 143 / 223 s |
| C2 C1 with 8 threads | same | 2289 at 125 / 3720 at 188 | 19 / 20 s | 2727 at 11.1 / 2297 at 10.8 | 264 / 232 s |
| C3 upstream b11755, `--fit`, 4 threads | same | 2289 at 100 / 3720 at 192 | 23 / 20 s | 976 at 12.2 / 2262 at 11.0 | 103 / 226 s |
| C4 b11755, all experts on CPU + 1 GB expert cache | same | 2289 at 194 / 3720 at 200 | 12 / 19 s | 6000 at 7.1 (cap) / 2256 at 6.6 | 853 / 359 s |

- Generation is 11-15 tokens/s, faster than ornith's 8.5: only about 3B parameters are active per token. 8 threads are no faster than 4
  (4 physical cores, memory-bound); the upstream runtime is no faster than Ollama's; the expert cache makes this 4 GB GPU slower (6-7 tok/s).
- Every JSON request (codebot's worker-action and planner-task schemas, through `response_format`) was valid and matched the schema; every
  drafts reply but one (C4, a 6000-token runaway) ended on its own.
- Memory: about 11.5 GB of the model file stays hot, so with the user's programs it does not fit. During inference the kernel moved 150-250
  MB/min of the user's memory (mostly the VM) to swap and back, against 30-100 MB/min with the model idle; a cold start read 7.5-11 GB from
  the SSD. Free swap fell to 3.6 GB with the prompt cache on and stayed at 4.8-5.7 GB with it off. A guard stops the server below 1.5 GB of
  free swap (it never fired).
- Heat: 93-97 C during generation (ornith runs at about 92 C); the thermal guard's pauses count against a run's time.
- The model writes its `### main.go` header inside the code block; drafts mode now reads that as the file name (no change to any of the 428
  recorded ornith replies).

Through codebot's own path (`ollama create coder30b-iq3` from `bench/bigmodel/Modelfile.coder30b-iq3`: Ollama's `qwen3-coder` renderer and
parser, ornith's top_k/top_p; requests with `NUM_CTX=16384`): the runner Ollama starts uses the same flags as ornith's. First quality probe,
level_03's first drafts prompt: two replies (133 and 172 s) both passed all 6 spec examples with all 9 declared names, and the scored one
passes **74 of 87 hidden tests**; ornith's first draft of the same prompt passed 0 of 6 examples, and its best level_03 run ever reached 74
after 32 minutes. calc_go's first drafts (two replies) did not compile (a constant division by zero; an undefined name). One prompt each:
this says nothing yet about reliability.

## 5. Next: complete tasks under a 3-hour budget

Drafts mode on calc_go and level_03, seeds 1 and 2, each task with a 180-minute budget (`--task-minutes 180`), candidate
(`--model coder30b-iq3 --num-ctx 16384`) and baseline (ornith, its normal 32K context) interleaved. The budget counts model loading, prompt
processing, generation, test runs, repairs, thermal pauses and recovery; downloads and imports are excluded. Progress at 90, 150 and 180 min
comes afterwards from the delivered commits (`checkpoints.py`), without interrupting the runs. Drafts prompts plus replies stay under 12K
tokens, so the two context sizes do not change what either model sees.

## 6. Results (2026-10-20) [M]

Drafts mode, 180 min per task, same seeds, candidate and baseline interleaved (runs `20261020-*-bigC-*`). Hidden tests at fixed minutes
come from the version delivered by then (`checkpoints.py`).

| task, seed | Qwen3-Coder-30B-A3B UD-IQ3_XXS | ornith:9b |
|---|---|---|
| calc_go 1 | all 15 spec examples at **33.9 min** (12 rounds); hidden **26/32** | 12/15 at the 180-min cap; hidden 20, 21, 23, 23 at 30, 90, 150, 180 min |
| calc_go 2 | all 15 at **28.4 min** (10 rounds); hidden **26/32** | 13/15 from minute 4.8 (hidden 21) to minute 162, when the user stopped the series |
| level_03 1 | all 6 examples and 9 declared names at **3.6 min** (its first draft); hidden **75/87** | all 6 and 9 at 64.1 min (13 rounds); hidden 64/87 |

- Completed (every spec example passes) within 3 hours: the candidate 3 of 3, ornith 1 of 3 (it has never passed calc_go's 2 operator
  precedence examples, in any run). Time to completion: 34, 28 and 4 min against never, never and 64 min.
- Hidden tests: 26, 26 and 75 against 23, 21 and 64. On calc_go the candidate passes all 4 precedence tests; it loses the same kind of
  tests as ornith, in requirements without examples (division by zero, infinity and NaN input, standard input).
- First attempts: its first level_03 draft passed everything; its first calc_go drafts did not compile (a constant division by zero, an
  undefined name), and 3 of its 5 calc_go drafts in seed 1 never compiled. No reply ran away (no loop stop in its 3 runs; ornith: 15 in one).
- Not run: level_03 seed 2 for both (the user stopped the series with the evidence above), level_01, other levels, the task pipeline.

**Decision (user, 2026-10-20: "Let's stop the test since we already know that the new model is significantly better")**: this machine's
codebot now runs on the candidate (`config/local.env`: `CODEBOT_MODEL="coder30b-iq3"`, `NUM_CTX=16384`; `codebot bench --preflight` passes:
JSON schema enforced, KV cache reused 82% across turns, context 16384). opencode-based paths (`codebot plan`'s interview, legacy projects)
keep `codebot-model` (ornith) at 32K. The shipped default stays ornith, which fits smaller machines. The costs: the user's programs are
pushed to swap while it runs, the CPU runs at 93-97 C, and the model takes 12.85 GB of disk (twice while the download is kept beside
Ollama's copy).

Next, if wanted: Stage D (higher precision with SSD streaming; Qwen3-Coder-Next 80B-A3B), which the brief allowed once a smaller
candidate gave useful results.
