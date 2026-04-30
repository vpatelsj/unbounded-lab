# Wave 1 — what's currently deployed

Snapshot of the live cluster at end of Wave 1 (2026-04-30). This is the
"single 'what's currently deployed' page" called for in the Wave 1
Definition of Done. Kept short — for the deeper why, see each item's
own README and [`../plan.md`](../plan.md).

## Cluster spine

One AKS control plane (Canada Central) with two AKS pools and three
edge nodes joined via unbounded-agent + WireGuard:

| Node | Pool / class | Arch | Region label | Hardware label |
|---|---|---|---|---|
| `aks-system-*-vmss00000{0,1}` | `system` | amd64 | — | — |
| `aks-gwmain-*-vmss00000{0,1}` | `gwmain` | amd64 | — | — |
| `apollo-lab-bou-gw` | edge gateway | amd64 | — | — |
| `spark-2c24` | edge (DGX Spark) | arm64 | `a` | `dgx-spark-gb10` |
| `spark-3d37` | edge (DGX Spark) | arm64 | `a` | `dgx-spark-gb10` |

Region-A labels applied via [`../foundation/label-region-a.sh`](../foundation/label-region-a.sh).
nginx-ingress LB at `20.48.249.187`, public hostname
`vapa-ollama.canadacentral.cloudapp.azure.com`,
TLS via cert-manager `letsencrypt-prod`.

## Workloads by namespace

| Namespace | Pod | Node | What | Wave |
|---|---|---|---|---|
| `lab-ollama-qwen-moe` | `ollama-0` (StatefulSet) | `spark-3d37` | Ollama serving `qwen3:30b-a3b` (GGUF Q4_K_M) | W1.1 |
| `lab-vllm-qwen-moe` | `vllm-0` (StatefulSet, 2 containers: `vllm` + `proxy`) | `spark-2c24` | vLLM serving `Qwen/Qwen3-30B-A3B-GPTQ-Int4` + Ollama-shim sidecar | W1.2 |
| `lab-openwebui` | `open-webui-*` (Deployment) | AKS user pool | Chat UI, multi-backend (Ollama + vLLM) | bonus |
| `lab-ingress` | (no pods — namespace exists for the host-literal ConfigMap) | — | kustomize replacement target for ingress hostname | W1.4 |
| `lab-observability` | kube-prometheus-stack + DCGM exporter (DaemonSet on Sparks only) + node-exporter (DaemonSet on every node) + Grafana | AKS system pool + Sparks (DCGM only) | metrics + dashboards | W1.5 |
| `lab-bench` | bench Jobs (on-demand) | AKS gw/system pool | repeatable benchmark harness | W1.6 |

## Public endpoints

| URL | Backend | Auth |
|---|---|---|
| `/` (root) | Open WebUI | Open WebUI's own login |
| `/lab-api/ollama/` | Ollama native API in `lab-ollama-qwen-moe` | basic-auth |
| `/lab-api/vllm/` | vLLM native OpenAI `/v1` in `lab-vllm-qwen-moe` (port 8000) | basic-auth |
| `/lab-api/vllm-ollama/` | vLLM via Ollama-shim sidecar (port 11434) | basic-auth |

All four served on `https://vapa-ollama.canadacentral.cloudapp.azure.com/`.

## PVC inventory

| Namespace | PVC | Size | Class | Used for |
|---|---|---|---|---|
| `lab-ollama-qwen-moe` | `models-ollama-0` | 60 Gi | `local-path` (Spark-local NVMe) | Ollama GGUF blobs (currently 18.6 GB used) |
| `lab-vllm-qwen-moe` | `models-vllm-0` | 100 Gi | `local-path` | HF Hub cache (currently 56 GB used; 31 GB FP8 abandoned + 16 GB GPTQ + 9.4 GB Xet) |
| `lab-openwebui` | `data` | 10 Gi | `default` (Azure Disk) | Open WebUI SQLite + RAG cache |
| `lab-observability` | grafana, prometheus, alertmanager | 5/10/2 Gi | `default` | metrics + dashboards |
| `lab-bench` | `lab-bench-results` | 1 Gi | `default` | benchmark JSON output |

## Running model

`Qwen/Qwen3-30B-A3B` MoE serves via both engines from the same logical
checkpoint family but two incompatible byte streams (GGUF Q4_K_M for
Ollama; GPTQ-Int4 for vLLM). Single GPU per Spark (NVIDIA GB10, 119 GiB
unified memory each). License: Apache-2.0 (model card:
[Qwen/Qwen3-30B-A3B](https://huggingface.co/Qwen/Qwen3-30B-A3B)).

## Headline benchmark numbers (W1.2 vLLM, GPTQ-Int4)

From [`../bench/results/lab-bench-vllm-w1-2.json`](../bench/results/lab-bench-vllm-w1-2.json)
(W1.6 harness, in-cluster Job, prompt=512 / gen=128, 20 measured runs / phase):

| Concurrency | Aggregate decode t/s | p50 latency | p99 latency | Per-req decode p50 |
|---|---|---|---|---|
| 1 | 62.5 | 2.05 s | 2.09 s | 62.5 t/s |
| 4 | 199.6 | 2.52 s | 2.67 s | 50.8 t/s |
| 8 | 299.2 | 2.87 s | 2.91 s | 44.5 t/s |
| 16 | 462.2 | 2.81 s | 2.82 s | 45.5 t/s |

100% success across all 80 measured requests. Continuous batching
visibly working: 7.4× throughput at 1.4× latency vs single-stream.
KV pool currently capped at 9.31 GiB / 101 712 tokens at
`--gpu-memory-utilization=0.22` because of the unreclaimable host page
cache (open carry-over).

## Open carry-overs from Wave 1

| Item | Action |
|---|---|
| `spark-2c24` page-cache reboot | Coordinated drain + reboot to release ~65 GiB pinned page cache, lift vLLM `gpu-memory-utilization` from 0.22 → ~0.85+. Logged in [`../storage-pain-journal.md`](../storage-pain-journal.md). See [`spark-reboot-runbook.md`](spark-reboot-runbook.md). |
| HF Hub direct pulls in pods | Plan (§First-Party Microsoft Story) requires mirroring to ACR / Azure Blob origin before Wave 5. Wave-2 work. |
| Azure Workload Identity, Container Insights, Front Door | All "planned" per plan; not Wave 1 blockers. |

## Reproducibility

Every item ships behind a `make` target — see [`../Makefile`](../Makefile)
or `make help`. From a clean cluster + the right `LAB_HOST`:

```sh
make w1.1-up         # Ollama + Open WebUI
make w1.2-up         # vLLM
make w1.4-creds      # generate basic-auth secrets (one-time)
make w1.4-up         # shared ingress
make w1.5-up         # observability
make w1.6-up         # benchmark harness namespace + script
make w1.6-run-vllm   # actual benchmark run
```

## What lives where in the repo

| Path | Wave | Contents |
|---|---|---|
| [`../inference/ollama-qwen-moe/`](../inference/ollama-qwen-moe/) | W1.1 | Ollama StatefulSet + Service + Ingress |
| [`../inference/vllm-qwen-moe/`](../inference/vllm-qwen-moe/) | W1.2 | vLLM StatefulSet (2 containers) + MoE tuning ConfigMap + proxy ConfigMap |
| [`../inference/openwebui/`](../inference/openwebui/) | bonus | Open WebUI Deployment + multi-backend connection JSON |
| [`../inference/ingress/`](../inference/ingress/) | W1.4 | Two Ingress objects (per-namespace), basic-auth Secret generator, kustomize host-literal |
| [`../observability/`](../observability/) | W1.5 | Helm values for kube-prometheus-stack + DCGM + initial Grafana dashboard ConfigMap |
| [`../bench/`](../bench/) | W1.6 | Harness + Job manifests + first results |
| [`../docs/`](../docs/) | all | This page + sanity write-ups + transfer review + architecture |
| [`../storage-pain-journal.md`](../storage-pain-journal.md) | all | Append-only measurements |
| [`../sponsor-updates/`](../sponsor-updates/) | all | Monthly written updates |
