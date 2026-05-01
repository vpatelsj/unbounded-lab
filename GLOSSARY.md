# Glossary

Canonical names used throughout this repo and in [ROADMAP.md](ROADMAP.md).
This file is the source of truth; if [ROADMAP.md](ROADMAP.md) and this file
disagree, this file wins (and `ROADMAP.md` should be patched).

Append entries when a new wave introduces a new model, namespace, label,
or region. Never silently rename — always add an alias row pointing to
the new canonical form.

## Models

| Canonical name | What it is | Format(s) on disk | Wave |
|---|---|---|---|
| `qwen-3-30b-a3b` | Qwen 3 30B-A3B MoE | GGUF Q4_K_M (~18.6 GB on Ollama), HF GPTQ-Int4 (~16 GB on vLLM) | W1.1 + W1.2 |
| `qwen-dense` | placeholder for the W2.5 dense pick — Qwen 3.5 32B Dense default; fallback chain Qwen 3.5 32B → Llama 3.3 70B Q4 | TBD | W2.5 |
| `llama-3.1-70b-fp8` | Llama 3.1 70B FP8 (~70 GB) | safetensors | W3.1 |
| `qwen2.5-vl-32b` *or* `llama-3.2-vision-11b` | W2.6 multimodal pick | TBD | W2.6 |
| `whisper-large-v3` | speech-to-text option for W2.8 | safetensors | W2.8 |
| `bge-m3` | embedding model for W2.7 RAG | safetensors | W2.7 |
| `bge-reranker` | reranker for W2.7 RAG | safetensors | W2.7 |
| `nomic-embed-text` | alternative embedding for W2.7 | safetensors | W2.7 |

> Plan-deviation note: the roadmap references `qwen-3.5-35b-a3b` (~24 GB
> GGUF, ~70 GB BF16). The lab actually deploys `qwen-3-30b-a3b`, the
> generation actually shipped at deploy time. Same architecture family
> (Qwen MoE), different generation+size. See
> [docs/wave-1/w1.2-vllm-sanity.md](docs/wave-1/w1.2-vllm-sanity.md) for the
> model deviation chain (BF16 → FP8 → GPTQ-Int4).

## Namespaces

| Namespace | Purpose | Wave |
|---|---|---|
| `lab-ollama-qwen-moe` | Ollama serving `qwen-3-30b-a3b` on `spark-3d37` | W1.1 |
| `lab-vllm-qwen-moe` | vLLM serving `qwen-3-30b-a3b` on `spark-2c24` | W1.2 |
| `lab-openwebui` | Open WebUI chat front-end (multi-backend) | bonus, sits next to W1.1 |
| `lab-ingress` | Holds the kustomize host-literal ConfigMap; ingress objects live in their respective engine namespaces | W1.4 |
| `lab-observability` | kube-prometheus-stack + DCGM exporter + dashboards | W1.5 |
| `lab-bench` | Benchmark harness Jobs + results PVC | W1.6 |
| `lab-rag` | (planned) embedding + ChromaDB + LLM pipeline | W2.7 |
| `lab-vllm-tp2` | (planned) two-Spark vLLM TP=2 | W3.1 |
| `lab-megatron-multi` | (planned) multi-node training | W3.2 |
| `lab-geo` | (planned) Front Door + region-aware routing | Wave 5 |

**Naming rule:** `lab-<engine>-<model-shortname>` for engine workloads;
`lab-<purpose>` for cross-cutting infra (ingress, observability, bench).

The plan's `qwen35-35b` and `qwen36-35b` "grandfathering" namespaces
**never existed on this cluster** — W1.1 and W1.2 deployed greenfield
into the new `lab-*` namespaces directly.

## Node labels

| Label | Values | Where applied | Set by |
|---|---|---|---|
| `lab.unbounded.cloud/region` | `a` (today), `b`, `c` (Wave 5) | All Sparks | [`foundation/label-region-a.sh`](foundation/label-region-a.sh) |
| `lab.unbounded.cloud/hardware-class` | `dgx-spark-gb10` (today), future `gb200`/`gb300` | All Sparks | same |

These are the labels manifests use in `nodeSelector` and `kubectl get nodes -L`.

## Regions

| Region | Status | Hardware |
|---|---|---|
| `region-a` | live | `spark-2c24` + `spark-3d37`, ConnectX-7 200 Gbps intra-region link, AKS Canada Central control plane |
| `region-b` | hardware-pending (Wave 5 gate) | 2× DGX Spark GB10, same join pattern |
| `region-c` | hardware-pending (Wave 5 gate) | 2× DGX Spark GB10, same join pattern |

## Endpoints

| URL | What it is | Auth | Wave |
|---|---|---|---|
| `https://vapa-ollama.canadacentral.cloudapp.azure.com/` | Open WebUI chat UI | Open WebUI's own login | bonus / W1.1 |
| `https://vapa-ollama.canadacentral.cloudapp.azure.com/lab-api/ollama/` | Ollama native API | basic-auth (`lab-api-basic-auth`) | W1.4 |
| `https://vapa-ollama.canadacentral.cloudapp.azure.com/lab-api/vllm/` | vLLM native OpenAI `/v1` API | basic-auth | W1.4 |
| `https://vapa-ollama.canadacentral.cloudapp.azure.com/lab-api/vllm-ollama/` | vLLM via Ollama-shim sidecar | basic-auth | W1.4 |

Hostname is intentionally a deployer-specific Azure default (not
committed as a literal); see [`README.md`](README.md) `LAB_HOST` section.

## Engines

| Short name | Image | Where | Notes |
|---|---|---|---|
| Ollama | `ollama/ollama` | `spark-3d37` | reads `OLLAMA_*` env vars; no config file |
| vLLM | `vllm/vllm-openai:v0.11.0` | `spark-2c24` | MoE tuning JSON via ConfigMap; Ollama-shim proxy as sidecar |
| SGLang | TBD | TBD | Wave 2.4 — joins the W1.4 ingress pattern with one new file |
| Open WebUI | `ghcr.io/open-webui/open-webui` | AKS user pool | front-end only, multi-backend wiring |

## Charts / pinned versions

| Chart | Version | Where |
|---|---|---|
| `prometheus-community/kube-prometheus-stack` | 84.4.0 | [Makefile](Makefile) `KPS_CHART_VERSION` |
| `nvidia/dcgm-exporter` | 4.8.1 | [Makefile](Makefile) `DCGM_CHART_VERSION` |

Bump these with one targeted PR each; never bump silently.
