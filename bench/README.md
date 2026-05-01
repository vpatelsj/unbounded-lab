# Benchmark harness (W1.6 + W1.7)

Wave items: **W1.6** (baseline harness) and **W1.7** (v2: streaming TTFT/TPOT,
repeats, validity, knee, run metadata). See [ROADMAP.md](../ROADMAP.md) →
"Benchmark Methodology" and the Wave 1 deliverable list.

> *"One-off curl numbers ('54.6 t/s on one prompt') are not credible
> performance data. We need a repeatable harness."* — ROADMAP.md

## What this proves

- Repeatable, source-controlled measurements across engines: same
  harness, same JSON schema, sweepable concurrency, results extractable
  from `kubectl logs` so they're never lost to a missing PVC.
- Stdlib-only Python (no `requests`, `httpx`, `aiohttp`) means a clean
  `python:3.12-slim` image and **zero pip installs in the hot path**.
- Schema is versioned (`unbounded-lab-bench/v2`), so historical sweeps
  stay diffable across hardware generations.
- Bench runs cross the same WireGuard path real clients use (Job pinned
  to AKS gateway/system pool, not the Spark itself), so numbers are not
  hot-loop fakery.

## Files

| File | Role |
|---|---|
| [`lab_bench.py`](lab_bench.py) | The harness. Pure stdlib, single file. Schema `unbounded-lab-bench/v2`. |
| [`namespace.yaml`](namespace.yaml) | `lab-bench` namespace + 1 Gi results PVC |
| [`job-vllm-w1.2.yaml`](job-vllm-w1.2.yaml) | **W1.6** vLLM sweep, c=[1,4,8,16] × 20 runs, ~3 min |
| [`job-ollama-w1.1.yaml`](job-ollama-w1.1.yaml) | **W1.6** Ollama sweep, c=[1,4] × 20 runs |
| [`job-vllm-w1.7-sweep.yaml`](job-vllm-w1.7-sweep.yaml) | **W1.7** vLLM wide+streaming sweep, c=1..64 × 12 runs × 3 repeats, ~25 min |
| [`results/`](results/) | JSON results extracted from Job logs |

## Deploy, status, teardown

```sh
make w1.6-up                 # namespace + PVC + script ConfigMap (idempotent)

# W1.7 (current): streaming, repeats, knee detection
make w1.7-run-vllm           # full sweep c=1..64, 3 repeats, ~25 min
make w1.7-results-fetch
make w1.7-show               # median per-c row + knee + meta header

# W1.6 (baseline; kept for reproducibility)
make w1.6-run-vllm           # narrower sweep c=[1,4,8,16], non-streaming, ~3 min
make w1.6-results-fetch

make w1.6-down               # removes Jobs + ConfigMap (PVC kept; clean by deleting the namespace)
```

The Jobs are pinned to the AKS gateway/system pool so the bench
traffic crosses the same WireGuard path real clients use. Running the
bench client on the same Spark as the engine would be a hot-loop fakery
that overstates throughput.

### How a measurement run works

1. The harness ships into the cluster as a ConfigMap built from
   [`lab_bench.py`](lab_bench.py) by `make w1.6-up`.
2. The W1.7 target also writes a tiny `lab-bench-meta` ConfigMap
   carrying the working-tree git short SHA, so the harness can record
   it in the result JSON's `meta.harness_git_sha`.
3. A Job mounts the ConfigMap, runs the harness against the in-cluster
   engine Service, and writes both a JSON results file to the PVC *and*
   a copy to stdout (so `kubectl logs` carries the JSON for free).
4. `make w1.7-results-fetch` (or `w1.6-results-fetch`) extracts the JSON
   from the Job's logs into `bench/results/`. Source-controlled.

## API access

The harness is the API consumer, not a server. Inspecting recent
results:

```sh
make w1.7-show
jq '.aggregate.by_concurrency[] | {c: .concurrency,
                                   tps: .aggregate_decode_tokens_per_s.median,
                                   ttft: .ttft_s_p50.median,
                                   tpot: .tpot_ms_p50.median}' \
   bench/results/lab-bench-vllm-w1-7.json
```

### Schema `unbounded-lab-bench/v2` (W1.7)

Header (top level):

```json
{
  "schema": "unbounded-lab-bench/v2",
  "harness_version": "0.2.0",
  "engine": "vllm-openai",
  "model": "Qwen/Qwen3-30B-A3B-GPTQ-Int4",
  "stream": true,
  "concurrency_levels": [1,2,4,8,16,32,48,64],
  "runs_per_phase": 12, "warmup_per_phase": 2, "repeats_count": 3,
  "min_completion_tokens": 32,
  "knee_plateau_ratio": 1.05, "knee_collapse_ratio": 0.5,
  "knee_concurrency": 32, "knee_reason": "throughput_plateau",
  "meta": {
    "harness_git_sha": "abc1234",
    "harness_version": "0.2.0",
    "engine_version": "0.11.0",
    "client_pod_name": "lab-bench-vllm-w1-7-xxxxx",
    "client_node_name": "aks-gwmain-...",
    "kernel": "5.15.0-...",
    "gpus": [
      {"hostname": "spark-2c24", "uuid": "GPU-...", "gpu_index": "0",
       "model": "NVIDIA Spark", "device": "nvidia0"}
    ]
  },
  "repeats": [ { "iter": 0, "phases": [ ...per-c phase records... ] }, ... ],
  "aggregate": { "by_concurrency": [
    {"concurrency": 1,
     "aggregate_decode_tokens_per_s": {"min": 62.1, "median": 62.8, "max": 63.1, "n": 3},
     "ttft_s_p50": {"min": 0.18, "median": 0.19, "max": 0.21, "n": 3},
     "tpot_ms_p50": {"min": 15.4, "median": 15.7, "max": 16.0, "n": 3},
     ...
    }
  ]}
}
```

Per-phase fields (inside `repeats[i].phases[]`):

- `concurrency`, `runs`, `warmup_runs`, `wallclock_s`
- `aggregate_decode_tokens_per_s`, `peak_gpu_power_w`
- `latency_s_p50/p95/p99/_mean`
- `ttft_s_p50/p95/p99` (null when `--no-stream`)
- `tpot_ms_p50/p95` (null when `--no-stream`)
- `per_req_decode_tokens_per_s_p50/p95`
- `prompt_tokens_mean`, `completion_tokens_mean`
- `truncated_count`, `min_output_chars`, `invalid_utf8_count`,
  `quality_warning`
- `ok_count`, `failed_count`, `errors_sample`

Back-compat: when `--repeats 1`, the JSON also includes a flat
`phases: [...]` field with the v1 record shape so old jq queries keep
working.

## Pain runbook

N/A directly — the bench harness *produces* the steady-state throughput
numbers that anchor the storage-pain comparisons. Cold-start / pull /
disk-footprint pain is recorded in each engine's runbook and rolled up
in [JOURNAL.md](../JOURNAL.md). The W1.2 sanity sweep specifically lives
in [docs/wave-1/w1.2-vllm-sanity.md](../docs/wave-1/w1.2-vllm-sanity.md).

## Plan deviations

**Why not k6 / locust / vegeta / MLPerf?**

- k6 has no first-class OpenAI / Ollama protocol support; we'd write
  the same JSON-body code in JS.
- Locust adds a Python runtime and master/worker dance for 20–80
  sequential requests per phase; overkill.
- vegeta is HTTP-only and reports HTTP latency, not tokens/sec — we
  need both the wallclock latency *and* the engine's `usage` block to
  separate "request was slow" from "request returned few tokens."
- MLPerf Inference Server is the right answer for **published**
  numbers but is heavy. Wave 2 may swap to it for sponsor-published
  numbers and keep this harness as the regression-detector. Listed as
  a W2.0 carry-over in ROADMAP.md.

## Known limitations (carry-overs to W2.0 Bench v3)

- **Closed-loop only.** Driven by `ThreadPoolExecutor` at fixed
  concurrency — understates tail latency vs. real Poisson-arrival
  traffic. Open-loop / `goodput@SLO` is W2.0.
- **One workload shape.** Fixed Lorem-ipsum prompt, 512 in / 128 out.
  Real benchmarks need short-chat (256/128) and long-context (4k/512)
  profiles. W2.0.
- **No GPU-node driver / firmware capture.** `meta.kernel` is the
  bench-pod kernel, not the Spark kernel. A privileged sidecar with
  `nvidia-smi -q` lands in W2.0 alongside Workload Identity.
- **Single-engine sweep.** The W1.7 wide sweep targets vLLM only.
  Ollama saturates at very low concurrency and wants its own profile;
  tracked as a W1.7-followup, doesn't block W1.7 close.

## GB200 / GB300 carry-over

Per [docs/wave-1/transfer-review.md](../docs/wave-1/transfer-review.md):

- Harness: transplants unchanged.
- Job manifests: change `agentpool` selectors; everything else is
  engine-Service-DNS, unchanged.
- Schema is versioned, so historical sweeps stay diffable across
  hardware generations.
- After GB200 dcgm reports `DCGM_FI_DEV_FB_USED` natively (Spark Tegra
  silently omits it), `peak_gpu_power_w` can be joined with `peak_fb_bytes`
  in the result JSON.
- Bump `--concurrency` ceiling well past 64 on GB200; re-tune
  `--knee-plateau-ratio` once we have data. Expect
  `knee_concurrency >= 64` and TTFT well below GB10 numbers.
