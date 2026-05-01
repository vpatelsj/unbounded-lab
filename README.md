# unbounded-lab

An AI workload showcase running on top of
[unbounded-kube](https://github.com/microsoft/unbounded-kube): two
[NVIDIA DGX Spark](https://www.nvidia.com/en-us/products/workstations/dgx-spark/)
nodes (ARM64, GB10 GPU, 119 GiB unified memory each) sit behind NAT in
Region A, joined to an AKS control plane in Canada Central via
unbounded-agent + WireGuard. From `kubectl`'s perspective they're
ordinary kubelets. From the cluster's perspective the same Qwen3 30B-A3B
MoE model is served two ways — Ollama (GGUF Q4_K_M) on one Spark, vLLM
0.11.0 (GPTQ-Int4) on the other — fronted by a single TLS hostname with
per-engine basic-auth, full Prometheus/Grafana/DCGM observability, and a
stdlib-only Python benchmark harness that produces self-describing JSON
sweeps.

The point isn't the model, the engines, or even the Sparks. The point is
that **the same Kubernetes patterns that work on a hyperscaler's GPU
fleet also work on edge ARM64 GPUs joined over WireGuard**, that
**every artifact built here is designed to transplant onto GB200/GB300
tomorrow with minimal re-work**, and that **every wave produces a
measured pain receipt** — disk, egress, page-cache pressure, cold-start
latency — for the future Unbounded Storage product team to consume.

## What's deployed today

Wave 1 closed on 2026-04-30. Single AKS cluster, two Sparks in Region A,
one public hostname:

| Path on `vapa-ollama.canadacentral.cloudapp.azure.com` | Backend                        | Auth                    |
|--------------------------------------------------------|--------------------------------|-------------------------|
| `/`                                                    | Open WebUI (chat front-end)    | Open WebUI session login |
| `/lab-api/ollama/`                                     | Ollama on `spark-3d37`         | basic-auth              |
| `/lab-api/vllm/`                                       | vLLM `/v1` on `spark-2c24`     | basic-auth              |
| `/lab-api/vllm-ollama/`                                | vLLM via Ollama-shim sidecar   | basic-auth              |

Cert is Let's Encrypt via cert-manager; ingress is nginx. Adding a third
engine (SGLang at W2.4) is one new `Ingress` object and one path prefix.

Headline numbers from the W1.6 benchmark harness against the vLLM path
(GPTQ-Int4, in-cluster Job, prompt 512 / gen 128, 20 measured runs per
phase): aggregate decode throughput goes from **62 t/s at c=1 to 462 t/s
at c=16** with p50 latency only rising from 2.05 s to 2.81 s — 7.4×
throughput at 1.4× latency, with continuous batching visibly working
and 100% success across all 80 measured requests. Full sweep:
[bench/results/lab-bench-vllm-w1-2.json](bench/results/lab-bench-vllm-w1-2.json).
The wider W1.7 sweep with streaming TTFT/TPOT and knee detection lives
next to it.

The interesting numbers aren't the t/s — they're in
[JOURNAL.md](JOURNAL.md): 18.6 GB Ollama Q4_K_M GGUF and 16 GB vLLM
GPTQ-Int4 safetensors for the *same logical model* (the dedup-pain story
in two rows), 31 GB of FP8 weights downloaded then abandoned because
vLLM 0.11.0 has no sm_121a CUTLASS kernels for GB10, and ~65 GiB of
unreclaimable host page cache pinning unified memory on `spark-2c24`
(DGX OS denies `drop_caches` even from a privileged pod) — which is
*why* vLLM runs at `--gpu-memory-utilization=0.22` instead of 0.85+ and
why a coordinated reboot is the top open carry-over into Wave 2.

## How the repo is organized

Three categories of artifact, three audiences:

**Component bundles** under [inference/](inference/),
[observability/](observability/), [bench/](bench/), [foundation/](foundation/),
[training/](training/), [rag/](rag/), [models/](models/), [geo/](geo/).
Each `<area>/<item>/` is a self-contained kustomize bundle plus a
`README.md` that follows a fixed 7-section template (What this proves /
Files / Deploy, status, teardown / API access / Pain runbook / Plan
deviations / GB200 / GB300 carry-over). Aimed at operators.

**Reference docs** at the root: [ARCHITECTURE.md](ARCHITECTURE.md) for
the wave-agnostic topology and hard rules, [STATE.md](STATE.md) for
what's currently deployed, [ROADMAP.md](ROADMAP.md) for the strategy and
wave structure (W1 → W5), [GLOSSARY.md](GLOSSARY.md) for canonical
model/namespace/label names (source of truth — wins over any other
doc), [JOURNAL.md](JOURNAL.md) for the append-only storage-pain
measurements. Aimed at architects and the storage-product team.

**Per-wave write-ups and runbooks** under
[docs/wave-1/](docs/wave-1/) (frozen historical snapshot of Wave 1's
architecture, demo script, transfer review, and W1.2 sanity sweep) and
[docs/runbooks/](docs/runbooks/) (operational playbooks like
[spark-reboot.md](docs/runbooks/spark-reboot.md)). Monthly executive
notes live under [sponsor-updates/](sponsor-updates/).

If you're new here, the fastest path through is:
[ARCHITECTURE.md](ARCHITECTURE.md) → [STATE.md](STATE.md) → whichever
component README matches what you're looking for.

## Hard rules

The four conventions every change has to honor (full text in
[ARCHITECTURE.md](ARCHITECTURE.md)):

1. `deploy/` in unbounded-kube is for **component manifests** rendered
   via `make *-manifests`. This repo is for **AI workloads** running on
   top. The two never mix.
2. Every `<area>/<item>/` carries a `README.md` and either plain YAML or
   a `kustomization.yaml`. Model license + provenance goes in the
   README intro for anything shipping or running model weights.
3. Anywhere a model, namespace, label, or region is named, it must
   match an entry in [GLOSSARY.md](GLOSSARY.md). Glossary wins over any
   other doc.
4. No third-party object stores (no S3, GCS, R2). Azure Blob / ACR
   only — the Microsoft-aligned narrative breaks if a slide shows AWS
   in the architecture diagram.

## Public hostname (`LAB_HOST`)

Public ingresses read their hostname from a per-namespace
`configMapGenerator`. Committed defaults are placeholders
(`ollama.lab.example.com`, `chat.lab.example.com`); the repo never
carries an environment-specific name. Override on the command line:

```sh
make LAB_HOST=mychat.example.com w1.1-up
```

The Make target backs up the affected `kustomization.yaml`, runs `sed`
over the `host=` literal, applies, and restores the file (even on
Ctrl-C). Without `LAB_HOST` set, targets print a warning and apply with
the placeholder — fine for local kustomize dry-runs, will not produce a
working public endpoint.

## Common make targets

`make help` lists every wave-numbered target. The ones you'll use most:

```sh
make w1.1-up         # Ollama + Open WebUI
make w1.2-up         # vLLM
make w1.4-creds      # generate basic-auth secrets (one-time)
make w1.4-up         # shared ingress
make w1.5-up         # observability (kube-prometheus-stack + DCGM)
make w1.5-grafana    # port-forward Grafana to localhost:3000
make w1.6-run-vllm   # measured benchmark run, ~3 min
make w1.7-run-vllm   # wider sweep with TTFT/TPOT + repeats, ~25 min
```

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
