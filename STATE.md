# Current state

Living "what's deployed right now" page. Updated whenever a wave item
ships, not when the calendar flips. For the frozen end-of-Wave-1
snapshot, see [docs/wave-1/state.md](docs/wave-1/state.md). For
architectural context see [ARCHITECTURE.md](ARCHITECTURE.md); for
canonical names see [GLOSSARY.md](GLOSSARY.md).

**Last updated:** 2026-04-30 (end of Wave 1).

## Cluster spine

One AKS control plane (Canada Central) plus three edge nodes joined via
unbounded-agent + WireGuard.

| Node                         | Pool / class      | Arch  | Region | Hardware label    |
|------------------------------|-------------------|-------|--------|-------------------|
| `aks-system-*-vmss00000{0,1}`| `system`          | amd64 | —      | —                 |
| `aks-gwmain-*-vmss00000{0,1}`| `gwmain`          | amd64 | —      | —                 |
| `apollo-lab-bou-gw`          | edge gateway      | amd64 | —      | —                 |
| `spark-2c24`                 | edge (DGX Spark)  | arm64 | `a`    | `dgx-spark-gb10`  |
| `spark-3d37`                 | edge (DGX Spark)  | arm64 | `a`    | `dgx-spark-gb10`  |

Region-A labels applied via [foundation/label-region-a.sh](foundation/label-region-a.sh).
nginx-ingress LB at `20.48.249.187`, public hostname
`vapa-ollama.canadacentral.cloudapp.azure.com`, TLS via cert-manager
`letsencrypt-prod`.

## Workloads by namespace

| Namespace               | Workload                                                            | Node            | Wave |
|-------------------------|---------------------------------------------------------------------|-----------------|------|
| `lab-ollama-qwen-moe`   | `ollama-0` (StatefulSet) — Ollama serving `qwen3:30b-a3b` (Q4_K_M)  | `spark-3d37`    | W1.1 |
| `lab-vllm-qwen-moe`     | `vllm-0` (StatefulSet, `vllm` + `proxy`) — `Qwen3-30B-A3B-GPTQ-Int4`| `spark-2c24`    | W1.2 |
| `lab-openwebui`         | `open-webui-*` (Deployment) — chat UI, multi-backend                | AKS user pool   | bonus |
| `lab-ingress`           | (no pods; namespace holds the kustomize host-literal ConfigMap)     | —               | W1.4 |
| `lab-observability`     | kube-prometheus-stack + dcgm-exporter + node-exporter + Grafana     | AKS sys + Sparks (DCGM only) | W1.5 |
| `lab-bench`             | bench Jobs (on-demand)                                              | AKS gw / system | W1.6 |

## Public endpoints

All on `https://vapa-ollama.canadacentral.cloudapp.azure.com/`:

| Path                       | Backend                                                  | Auth                 |
|----------------------------|----------------------------------------------------------|----------------------|
| `/`                        | Open WebUI                                               | Open WebUI login     |
| `/lab-api/ollama/`         | Ollama native API (`lab-ollama-qwen-moe`)                | basic-auth           |
| `/lab-api/vllm/`           | vLLM native OpenAI `/v1` (`lab-vllm-qwen-moe`, port 8000)| basic-auth           |
| `/lab-api/vllm-ollama/`    | vLLM via Ollama-shim sidecar (port 11434)                | basic-auth           |

## PVC inventory

| Namespace             | PVC                                          | Size        | Class       | Used for                                               |
|-----------------------|----------------------------------------------|-------------|-------------|--------------------------------------------------------|
| `lab-ollama-qwen-moe` | `models-ollama-0`                            | 60 Gi       | `local-path`| Ollama GGUF blobs (≈18.6 GB used)                      |
| `lab-vllm-qwen-moe`   | `models-vllm-0`                              | 100 Gi      | `local-path`| HF Hub cache (≈56 GB used: 31 abandoned + 16 GPTQ + 9.4 Xet) |
| `lab-openwebui`       | `data`                                       | 10 Gi       | `default`   | Open WebUI SQLite + RAG cache                          |
| `lab-observability`   | grafana, prometheus, alertmanager            | 5 / 10 / 2 Gi | `default` | metrics + dashboards                                   |
| `lab-bench`           | `lab-bench-results`                          | 1 Gi        | `default`   | benchmark JSON output                                  |

## Running model

`Qwen/Qwen3-30B-A3B` MoE serves via both engines from the same logical
checkpoint family but two incompatible byte streams (GGUF Q4_K_M for
Ollama; GPTQ-Int4 for vLLM). Single GPU per Spark (NVIDIA GB10, 119 GiB
unified memory each). License: Apache-2.0 (model card:
[Qwen/Qwen3-30B-A3B](https://huggingface.co/Qwen/Qwen3-30B-A3B)).

## Headline benchmark (W1.2 vLLM, GPTQ-Int4)

From [bench/results/lab-bench-vllm-w1-2.json](bench/results/lab-bench-vllm-w1-2.json)
(W1.6 harness, in-cluster Job, prompt=512 / gen=128, 20 measured runs / phase):

| Concurrency | Aggregate decode t/s | p50 latency | p99 latency | Per-req decode p50 |
|------------:|---------------------:|------------:|------------:|-------------------:|
|           1 |                 62.5 |       2.05 s |       2.09 s|              62.5  |
|           4 |                199.6 |       2.52 s |       2.67 s|              50.8  |
|           8 |                299.2 |       2.87 s |       2.91 s|              44.5  |
|          16 |                462.2 |       2.81 s |       2.82 s|              45.5  |

100% success across all 80 measured requests. Continuous batching
visibly working: 7.4× throughput at 1.4× latency vs single-stream. KV
pool capped at 9.31 GiB / 101 712 tokens at
`--gpu-memory-utilization=0.22` because of the unreclaimable host page
cache (open carry-over below).

The wider W1.7 sweep (c=1,2,4,8,16,32,48,64; streaming TTFT/TPOT;
3 repeats; knee detection) lives at
[bench/results/lab-bench-vllm-w1-7.json](bench/results/lab-bench-vllm-w1-7.json).

## Open carry-overs

| Item                              | Action                                                                                                                |
|-----------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| `spark-2c24` page-cache reboot    | Coordinated drain + reboot to release ~65 GiB pinned page cache, lift vLLM `gpu-memory-utilization` from 0.22 → ~0.85+. Logged in [JOURNAL.md](JOURNAL.md). Procedure: [docs/runbooks/spark-reboot.md](docs/runbooks/spark-reboot.md). |
| HF Hub direct pulls in pods       | Mirror to ACR / Azure Blob origin. Wave-2 work — see [ROADMAP.md](ROADMAP.md) §First-Party Microsoft Story.            |
| Workload Identity, Container Insights, Front Door | All "planned" per [ROADMAP.md](ROADMAP.md); not Wave 1 blockers.                                  |

## Reproducibility

Every item ships behind a `make` target. From a clean cluster + the
right `LAB_HOST`:

```sh
make w1.1-up         # Ollama + Open WebUI
make w1.2-up         # vLLM
make w1.4-creds      # generate basic-auth secrets (one-time)
make w1.4-up         # shared ingress
make w1.5-up         # observability
make w1.6-up         # benchmark harness namespace + script
make w1.6-run-vllm   # measured benchmark run (≈3 min)
make w1.7-run-vllm   # wider sweep with TTFT/TPOT + repeats (≈25 min)
```
