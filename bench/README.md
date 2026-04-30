# W1.6 — Benchmark harness

Wave item: **W1.6** (see [`../plan.md`](../plan.md) → "Benchmark Methodology"
and the Wave 1 deliverable list).

> *"One-off curl numbers ('54.6 t/s on one prompt') are not credible
> performance data. We need a repeatable harness."* — plan.md

## What's here

| File | Role |
|---|---|
| [`lab_bench.py`](lab_bench.py) | The harness itself. Pure stdlib, single file. |
| [`namespace.yaml`](namespace.yaml) | `lab-bench` namespace + 1Gi results PVC |
| [`job-vllm-w1.2.yaml`](job-vllm-w1.2.yaml) | W1.2 vLLM sweep, c=[1,4,8,16] x 20 runs |
| [`job-ollama-w1.1.yaml`](job-ollama-w1.1.yaml) | W1.1 Ollama sweep, c=[1,4] x 20 runs |
| [`results/`](results/) | JSON results extracted from Job logs |

The harness is deliberately stdlib-only (no `requests`, no `httpx`,
no `aiohttp`) so the Job container is a clean `python:3.12-slim` image
with **zero pip installs in the hot path**. Dependencies are a known
class of "the benchmark broke because PyPI sneezed"; we don't want
that variable when we're measuring the engine.

## How a measurement run works

1. The harness ships into the cluster as a ConfigMap built from
   [`lab_bench.py`](lab_bench.py) by `make w1.6-up`.
2. A Job (`job-vllm-w1.2.yaml` or `job-ollama-w1.1.yaml`) mounts the
   ConfigMap, runs the harness against the in-cluster engine Service,
   and writes both a JSON results file to a 1Gi PVC *and* a copy to
   stdout (so `kubectl logs` carries the JSON for free).
3. `make w1.6-results-fetch` extracts the JSON blocks from each Job's
   logs into `bench/results/<job>.json`. Source-controlled. Diff-able.

## Run it

```sh
make w1.6-up                 # namespace + PVC + script ConfigMap
make w1.6-run-vllm           # full vLLM sweep, ~80 s wallclock
make w1.6-run-ollama         # full Ollama sweep, ~longer (single-stream engine)
make w1.6-results-fetch      # pulls JSON into bench/results/
```

The Jobs are pinned to the AKS gateway/system pool so the bench
traffic crosses the same WireGuard path real clients use. Running the
bench client on the same Spark as the engine would be a hot-loop fakery
that overstates throughput.

## What the JSON contains

```json
{
  "schema": "unbounded-lab-bench/v1",
  "engine": "vllm-openai",
  "model": "Qwen/Qwen3-30B-A3B-GPTQ-Int4",
  "prompt_len_target_tokens": 512,
  "gen_len_max_tokens": 128,
  "phases": [
    {"concurrency": 1,  "ok_count": 20, "latency_s_p50": 2.05, "latency_s_p99": 2.09,
     "aggregate_decode_tokens_per_s": 62.46,  "per_req_decode_tokens_per_s_p50": 62.50, ...},
    {"concurrency": 16, "ok_count": 20, "latency_s_p50": 2.81, "latency_s_p99": 2.82,
     "aggregate_decode_tokens_per_s": 462.19, "per_req_decode_tokens_per_s_p50": 45.52, ...}
  ]
}
```

Per the plan's methodology section we record (and the JSON carries)
all of: prompt length target, generation length, concurrency, model,
engine, image tag implicit in the Job manifest, started/finished UTC,
and per-phase wallclock so `aggregate_tps` is independently verifiable
from `total_tokens / wallclock`.

## Why not k6 / locust / vegeta?

- k6 has no first-class OpenAI / Ollama protocol support; we'd write
  the same JSON-body code in JS.
- Locust adds a Python runtime and master/worker dance for what is
  20-80 sequential requests per phase; overkill.
- vegeta is HTTP-only and reports HTTP latency, not tokens/sec — we
  need both the wallclock latency *and* the engine's `usage` block to
  separate "request was slow" from "request returned few tokens."

The harness is ~300 lines of Python and does exactly the four things
we need: warm up, drive concurrent requests, summarise (p50/p95/p99 +
aggregate t/s + per-request decode t/s), and emit JSON.

## Known limitations / follow-ups

- **`peak_gpu_fb_bytes` records as null today.** The Prometheus
  `query_range` for `DCGM_FI_DEV_FB_USED` swallows exceptions silently
  and returns `None`. The bench Job runs as `runAsNonRoot: true` so
  any `urllib` DNS / connect issue surfaces as a caught exception, not
  a hard fail. To be tightened in a small follow-up: log the prom
  query error to stderr instead of swallowing it, and also accept
  `--prom-skew-s` for the time-window padding around each phase.
- **No streaming-mode TTFT metric.** Today we measure end-to-end
  request latency. Time-to-first-token under streaming requires
  switching to chunked reads; deferred until a Wave 2 streaming-UX
  story actually wants it.
- **Single prompt template.** The harness drives `make_prompt(target_tokens)`
  which is a deterministic Lorem-ipsum filler plus a fixed question.
  Good for "is the engine fast" — bad for "does the model give good
  answers." The *quality* eval is the W2.x eval-harness work, not this.
- **No SGLang adapter.** When SGLang lands at W2.4 it speaks the
  OpenAI `/v1` API natively, so the existing `--engine vllm-openai`
  caller will hit it unmodified; the only diff is the URL.

## GB200 / GB300 transfer

- harness: transplants unchanged.
- Job manifests: change `agentpool` selectors to whatever the new
  control plane uses; everything else is engine-Service-DNS,
  unchanged.
- The schema `unbounded-lab-bench/v1` is pinned so any later analyzer
  / Grafana ingestion works against historical results across hardware.

## Status / teardown

```sh
make w1.6-up                 # idempotent
make w1.6-down               # removes Jobs + ConfigMap (PVC kept; clean by deleting the namespace)
```
