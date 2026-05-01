# unbounded-lab

AI showcase running on top of [unbounded-kube](https://github.com/microsoft/unbounded-kube).
DGX Spark today, designed to transplant onto GB200/GB300 tomorrow.

## Start here

| Doc                                | Read it for                                                |
|------------------------------------|------------------------------------------------------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | wave-agnostic topology, conventions, hard rules            |
| [STATE.md](STATE.md)               | what's actually deployed right now                         |
| [ROADMAP.md](ROADMAP.md)           | strategy, wave structure, GB200/GB300 transfer plan        |
| [GLOSSARY.md](GLOSSARY.md)         | canonical model / namespace / label names (source of truth) |
| [JOURNAL.md](JOURNAL.md)           | append-only storage-pain measurements (internal)           |

Per-wave history lives under [docs/wave-N/](docs/wave-1/). Operational
playbooks live under [docs/runbooks/](docs/runbooks/). Monthly executive
notes live under [sponsor-updates/](sponsor-updates/).

## Layout

| Path                                                            | Wave items   | What it is                                                |
|-----------------------------------------------------------------|--------------|-----------------------------------------------------------|
| [inference/ollama-qwen-moe/](inference/ollama-qwen-moe/)        | W1.1         | Ollama serving Qwen MoE on spark-3d37                     |
| [inference/vllm-qwen-moe/](inference/vllm-qwen-moe/)            | W1.2         | vLLM serving Qwen MoE on spark-2c24                       |
| [inference/openwebui/](inference/openwebui/)                    | bonus / W1.1 | Open WebUI chat front-end (multi-backend)                 |
| [inference/ingress/](inference/ingress/)                        | W1.4         | Shared ingress + basic-auth + TLS pattern                 |
| [inference/sglang-dense/](inference/sglang-dense/)              | W2.4         | SGLang third-engine pick                                  |
| [inference/vllm-vision/](inference/vllm-vision/)                | W2.6         | Multimodal vLLM                                           |
| [inference/vllm-tp2/](inference/vllm-tp2/)                      | W3.1         | Two-Spark TP=2 vLLM via KubeRay or LWS                    |
| [models/](models/)                                              | W2.5 / W2.8  | Dense chat, whisper, reranker, etc.                       |
| [rag/](rag/)                                                    | W2.7         | Embedding + ChromaDB + LLM                                |
| [training/](training/)                                          | W2 / W3 / W4 | LoRA, Megatron, eval-harness, continuous pretrain, ScalarLM |
| [observability/](observability/)                                | W1.5         | kube-prometheus-stack + DCGM exporter + Grafana dashboards |
| [bench/](bench/)                                                | W1.6 / W1.7  | Repeatable benchmark harness + JSON results               |
| [foundation/](foundation/)                                      | all          | One-shot bootstrap scripts (node labels, repros)          |
| [geo/](geo/)                                                    | Wave 5       | Front Door, regional routing, FL                          |
| [docs/runbooks/](docs/runbooks/)                                | all          | Operational playbooks (e.g. spark-reboot)                 |
| [docs/wave-1/](docs/wave-1/)                                    | W1.x         | Frozen Wave 1 snapshot (architecture, demo, sanity, transfer review) |
| [sponsor-updates/](sponsor-updates/)                            | all          | Monthly executive updates (`YYYY-MM.md`)                  |

Each `<area>/<item>/` directory is a self-contained kustomize bundle plus
a `README.md`. See [ARCHITECTURE.md](ARCHITECTURE.md) for the four hard
rules (deploy/ vs. workloads, per-item conventions, canonical names, no
third-party object stores) and the `LAB_HOST` override mechanism for
public ingresses.

## Make targets

`make help` lists every wave-numbered target. Common entry points:

```sh
make w1.1-up         # Ollama + Open WebUI
make w1.2-up         # vLLM
make w1.4-up         # shared ingress
make w1.5-up         # observability
make w1.6-run-vllm   # measured benchmark run (~3 min)
make w1.7-run-vllm   # wider sweep with TTFT/TPOT (~25 min)
```
