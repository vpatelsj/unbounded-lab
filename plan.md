# Plan: unbounded-lab

*AI showcase and proving ground for unbounded-kube, running on DGX Spark today and
designed to transplant onto GB200/GB300 tomorrow.*

> **Note:** This plan was originally drafted while `lab/` lived inside the
> `unbounded-kube` repo. Path references like `lab/inference/...`,
> `lab/training/...`, etc. now correspond to `inference/...`, `training/...`
> at the root of this standalone repo. The narrative is otherwise unchanged.

## Goals

1. **Demonstrate** AKS + unbounded-kube credibly orchestrates the AI workloads.
2. **Build Experience** in deploying production AI workloads on 
    Kubernetes, including inference, fine-tuning, RAG pipelines, multi-node distributed work, and multi-region orchestration.
3. **Hardware-portable patterns:** every artifact built on DGX Spark (GB10, ARM64) is
   designed to transplant onto GB200/GB300 hardware when it lands - Spark is the on-ramp,
   not the destination.
4. **Internal product input:** the Storage Pain Journal feeds the unbounded-kube and
   future Unbounded Storage product teams with measured friction from real workloads.

**The infrastructure of the showcase is the edge-join story:** unbounded-agent +
unbounded-net (WireGuard) joining ARM64 DGX Sparks (eventually across 3 regions) to an
AKS control plane - one cluster, one kubectl context. Every other demo (inference engines,
RAG, fine-tuning, multi-node, multi-region) sits on top of that spine. It is the one
thing only unbounded-kube + AKS does in the Microsoft-aligned stack today.

**Baseline assumption:** AKS-managed control plane is the control-plane substrate. Eventually we might want to show unbounded-kube running on other control planes (kubeadm, OpenShift, NeoCloud's k8s service), but for this showcase we focus on AKS. The story is "unbounded-kube extends AKS to the edge with heterogeneous GPU nodes," not "unbounded-kube runs on every control plane."

## Hardware Inventory (Demo Lab)

The spine of the story is "ARM64 DGX Sparks under one AKS control plane via unbounded-agent."
One hardware class today (DGX Spark, GB10, ARM64); multi-region comes online when Regions B
and C land.

**Already deployed:**
- **Region A**: spark-3d37 + spark-2c24 (both DGX Spark, GB10 ARM64 sm_120, 120 GiB
  unified memory, 273 GB/s bandwidth). ConnectX-7 at 200 Gbps intra-region link.
  Currently running: Ollama on spark-3d37, vLLM on spark-2c24.
- **AKS control plane** in Canada Central, gateway nodes with public IPs, cert-manager,
  nginx-ingress. Region A Sparks attached via unbounded-agent + unbounded-net (WireGuard).

**Pending (not a blocker before Wave 5):**
- **Region B and Region C**: two more DGX Sparks each, same hardware class, in the process
  of being deployed. Identical to Region A's hardware; will join the same AKS control
  plane via the same unbounded-agent + WireGuard pattern. Waves 1-4 assume Region A only.
  Wave 5 is gated on these landing.

**Future (post-Spark):**
- GB200 / GB300 hardware when available. Patterns built in this lab on Spark MUST be
  designed to transplant onto GB200/GB300 with minimal re-work. See "GB200/GB300 Transfer
  Plan" section.

**Not in scope:**
- No Azure GPU VM node pool. The spine differentiator is edge-Sparks-to-AKS via
  unbounded-agent, not hardware heterogeneity within one cluster.
- No Jetson or other ARM edge hardware in this round.

**Shared infrastructure:**
- AKS, Azure CNI, nginx-ingress, cert-manager - standard Microsoft stack throughout.

## Code Organization

The lab is greenfield code. None of the existing repo code (product binaries under `cmd/`,
component manifests under `deploy/`, dev tooling under `hack/`) is reusable for the lab.
To avoid colliding with product conventions, all lab artifacts live under a new
top-level `lab/` directory.

**Hard rule:** `deploy/` is for unbounded-kube *component* manifests (machina, net,
inventory) rendered via `make *-manifests`. `lab/` is for *AI workloads* running on top
of unbounded-kube. The two never mix.

```
lab/
  README.md                 # entry point; current state, how to deploy each item
  GLOSSARY.md               # mirrors the Glossary section below; canonical names
  inference/
    ollama-qwen-moe/        # W1.1
    vllm-qwen-moe/          # W1.2 (incl. MoE tuning ConfigMap, vLLM proxy sidecar)
    sglang-dense/           # W2.4
    vllm-vision/            # W2.6
    vllm-tp2/               # W3.1 (KubeRay or LeaderWorkerSet)
  models/
    ollama-dense/           # W2.5
    whisper/  | reranker/   # W2.8 (pick one)
  rag/                      # W2.7 (embedding + ChromaDB + LLM)
  training/
    lora-job/               # W2.2
    megatron-single/        # W2.1 / R1
    megatron-multinode/     # W3.2
    eval-harness/           # W2.3
    continuous-pretrain/    # W4.1
    scalarlm/               # W3.4 / W3.5 (only if R3 closes)
  observability/            # W1.5 (Prometheus, Grafana, DCGM exporter, dashboards)
  ingress/                  # W1.4 shared ingress + auth + TLS pattern
  geo/                      # Wave 5 (Front Door config, region-aware routing, FL)
  bench/                    # benchmark harness (see Benchmark Methodology)
  storage-pain-journal.md   # the running measurements table (append-only)
  sponsor-updates/          # YYYY-MM.md monthly written updates
```

**Per-item conventions** (each `lab/<area>/<item>/` directory):
- `README.md` - what it deploys, what it proves, deploy + teardown commands.
- `LICENSE.md` - model card link + license text/citation (mandatory; see Licensing section).
- Manifests as plain YAML or `kustomization.yaml`. Templating is allowed if reused
  across items but is not required.

**Why a new top-level directory and not `hack/lab/` or `demo/`:**
- `hack/` is dev tooling for product engineers; lab is sponsor-facing.
- `demo/` already holds product-demo content (`demo/nebius-ssh/`); overloading would
  conflate "demo of the product" with "AI workloads on the product."
- Top-level visibility matches the lab's importance to sponsors.

## Glossary

Canonical names, used consistently throughout this plan and inside `lab/`. Anywhere
this plan says a model name or namespace, it matches an entry here.

**Models** (pick the canonical form; release-status reality wins over branding):
- `qwen-3.5-35b-a3b` - Qwen 3.5 35B-A3B MoE (BF16 ~70 GB, GGUF Q4_K_M ~24 GB). Already
  shipped, this is what the existing Ollama and vLLM deployments serve.
- `qwen-3.5-122b-a10b` - Qwen 3.5 122B-A10B MoE (Q4 ~70 GB), used in W3.1 alternative.
- `qwen-dense` - placeholder for the W2.5 dense pick. Default Qwen 3.5 32B Dense; if
  Qwen 3.6 27B Dense ships in time, swap. Fallback chain: Qwen 3.5 32B → Llama 3.3 70B
  Q4. (Mistral Large is *not* in the chain - commercial license excludes lab use.)
- `llama-3.1-70b-fp8` - Llama 3.1 70B FP8 (~70 GB), the W3.1 default TP=2 target.
- `llama-3.2-vision-11b` or `qwen2.5-vl-32b` - W2.6 multimodal pick.
- `whisper-large-v3` - W2.8 speech-to-text option.
- `bge-m3`, `bge-reranker`, `nomic-embed-text` - embeddings/reranker for RAG (W2.7).

**Namespace convention:**
- New lab workloads use `lab-<engine>-<model-shortname>`, e.g.,
  `lab-ollama-qwen-moe`, `lab-vllm-qwen-moe`, `lab-vllm-tp2`, `lab-rag`.
- Existing namespaces `qwen35-35b` and `qwen36-35b` are grandfathered until W1.1 and
  W1.2 redeploy from `lab/inference/...` manifests, at which point they are renamed
  to the new convention and the old namespaces are torn down.

**Region and node labels:**
- `region-a` (current): spark-2c24, spark-3d37, ConnectX-7 link.
- `region-b`, `region-c` (Wave 5 hardware-gated): same hardware class.
- Node labels: `lab.unbounded.cloud/region={a,b,c}`,
  `lab.unbounded.cloud/hardware-class=dgx-spark-gb10`. Used by node selectors and
  `kubectl get nodes -L`.

## What Sponsors Should See

Each demo should illustrate one or more of these unbounded-kube + AKS capabilities:

1. **Edge node joining**: ARM64 Blackwell GB10 Sparks behind NAT, joined to an AKS
   control plane via unbounded-agent + WireGuard. One cluster, one kubectl.
2. **GPU discovery and scheduling**: unbounded-agent generates CDI specs, RuntimeClass,
   device plugin on Sparks; pods request `nvidia.com/gpu` and just work.
3. **Workload portability**: manifests built on Spark (ARM64) are designed to transplant
   onto GB200/GB300 (also ARM64) with minimal re-work.
4. **Model/weight management**: PVCs on local-path storage keep models on the node;
   weights stay where the GPU is.
5. **Secure exposure**: auth, TLS, ingress - standard k8s patterns, work for AI workloads.
6. **Multi-node coordination (intra-region)**: distributed inference/training across two
   Sparks over ConnectX-7 using k8s-native primitives.
7. **Multi-region under one control plane (Wave 5)**: when Regions B and C land, 3
   regions of Sparks all visible via one kubectl; workloads placed by region-aware scheduling.
8. **AKS as first-party citizen**: AKS control plane, Azure CNI, Azure Container Registry,
   Azure Blob, Azure Monitor - the Microsoft stack is visible and load-bearing throughout.

## Showcase Structure

The execution plan is wave-based (see "Execution Order" below). Waves are the canonical
source of truth for what gets built and when. The thematic narrative (foundation,
inference diversity, model diversity, training, multi-node, geo) maps onto wave items
as follows:

| Capability narrative | Proven by wave items |
|---|---|
| Foundation: edge Sparks join AKS, GPU scheduling, local-PVC weights, standard ingress | Already deployed; reproducible from manifests at W1.1, W1.2, W1.4, W1.5 |
| Inference diversity: multiple engines coexist on one platform | W1.1 (Ollama), W1.2 (vLLM), W2.4 (third engine, default SGLang) |
| Model diversity: dense, MoE, multimodal, RAG, speech/reranker | W2.5 (dense), already-running MoE, W2.6 (vision), W2.7 (RAG), W2.8 (whisper or reranker) |
| Training on the platform: LoRA → Megatron → eval → continuous pretrain → ScalarLM | W2.1 (Megatron single), W2.2 (LoRA), W2.3 (eval), W3.4 (ScalarLM single), W4.1 (continuous pretrain) |
| Multi-node intra-region: TP=2 inference, multi-node training, resilience | W3.0 (RDMA spike), W3.1 (vLLM TP=2), W3.2 (Megatron multinode), W3.3 (resilience), W3.5 (ScalarLM multinode) |
| Geo-distributed under one control plane | W5.1-W5.7 (hardware-gated on Regions B and C) |

**Every wave item tracks two things:**
1. **unbounded-kube capability proved** - what the demo shows is possible today.
2. **Storage pain observed** - real friction (egress, duplication, disk pressure, auth
   glue), logged in the Storage Pain Journal below.

This dual view makes each demo a working capability proof AND a requirements data point
for the future Unbounded Storage product. We are NOT deploying Unbounded Storage in this
lab - it doesn't exist yet - but we ARE capturing the problems it would solve.

### Storage Pain Predictions (per capability area)

These are the predictions for what each capability area will surface. Actual measured
values land in the Storage Pain Journal. This list is the *hypothesis*; the journal is
the *data*.

**Foundation (W1.1, W1.2, W1.5):**
- Local-path PVC pins weights to one node. Two failure modes to measure separately:
  - *Node reboot, disk survives:* PVC contents persist under
    `/opt/local-path-provisioner/...`; pod restarts fast, no re-pull. Happy case.
  - *Node loss or PVC deletion:* weights re-pulled from HF Hub on recovery. Painful.
    Cannot reschedule to a different node automatically.
- No way to share a cached weight between namespaces on the same node without
  re-downloading (separate PVCs).

**Inference diversity (W1.1, W1.2, W2.4):**
- Each engine uses a different artifact for the same logical model: Ollama ships GGUF
  (~24 GB for `qwen-3.5-35b-a3b` Q4_K_M), vLLM ships HF safetensors (~70 GB BF16). Not
  the same bytes - different quantizations of the same model. Total on disk: ~94 GB for
  "one model, two engines." Evidence for a content-addressable cache that can transcode
  formats, not just dedupe bytes.
- Pod restart on Ollama triggers re-load from local PVC (fast); fresh deployment on a
  different node forces a re-pull from HF Hub (slow, costly).

**Model diversity (W2.5-W2.8):**
- RAG dataset (W2.7) wants persistent, shareable storage. Local-path PVC pins it to one
  node; a shared store means external dependencies.
- 5+ model classes active = 5+ independent weight sets on disk, no dedup, no P2P sharing.
- Vector store grows with documents; no tiered storage story (hot vectors on NVMe, cold
  on Blob).

**Training (W2.1-W2.3, W3.4, W4.1):**
- Training datasets pulled from HF Hub per job. Two experiments using the same dataset
  download it twice. Multi-TB datasets would be brutal.
- Checkpoint accumulation: continuous pre-training writes N checkpoints over hours.
  Local PVC fills up; manual cleanup needed. The "checkpoint chaos" use case.
- ScalarLM shared-checkpoint depends on a path both training and inference pods can
  read/write. RWX PVC support varies by storage class.
- No deduplication of base-model weights across experiments.

**Multi-node intra-region (W3.0-W3.5):**
- *Thundering herd in miniature:* vLLM TP=2 cold start - both Sparks independently pull
  the same ~70 GB (or ~35 GB/node TP-shard, per W3.1's sizing) from HF Hub. 2x egress,
  same content, no P2P sharing even though ConnectX-7 sits there at 200 Gbps idle.
- *Distributed training data pipeline:* Megatron TP/PP/DP with all ranks pulling the
  same dataset shards = thundering-herd egress explosion at scale.
- *Checkpoint coordination:* multi-pod training needs a shared writable path; RWX PVC
  choice affects performance dramatically. Async write-back (NVMe buffer + async upload)
  would materially help.
- This is the hero use case for Unbounded Storage: one origin fetch serves the cluster
  via P2P over ConnectX-7. The lab directly validates the P2P claim.

**Geo-distributed (W5.1-W5.7):**
- *3x origin fetch:* each region pulls weights independently. 3 regions x ~65 GB =
  ~195 GB of WAN egress for the same content. A regional cache tier eliminates 2/3.
- *Multi-provider auth explosion:* if Region B is a NeoCloud and Region C is on-prem,
  each region has different credentials to reach source data. Today = per-region glue.
- *Follow-the-sun checkpoint movement (W5.6):* when a job moves regions, the checkpoint
  must move too. Today = manual rsync or Azure Blob copy.
- *Federated learning aggregation (W5.7):* gradient deltas need a shared meeting point.
- *DR demo (W5.4):* when Region A dies, does Region B have weights and latest checkpoint?
  Today: only if someone set up cross-region replication manually.
- The strongest evidence base: every cross-region problem encountered validates the
  Unbounded Storage product requirements.

---

## Storage Pain Journal

**Purpose:** The future Unbounded Storage product solves a set of AI-data problems that this
showcase will naturally encounter. Rather than pretend the product exists, we log the pain
points as we build each wave item. This serves two audiences:

1. **Anyone reviewing the demo** (sponsors, future team members): we can honestly say
   "here's what would be hard without a caching/replication layer, here's how much
   egress/disk/time it costs." No hand-waving.
2. **The Unbounded Storage product team**: real requirements data from a real cluster running
   real AI workloads, not hypotheticals.

**What to measure per wave item (concrete numbers to capture as we build):**

| Metric | Wave item(s) | How to capture |
|---|---|---|
| Time to first inference after cold pod start | W1.1, W1.2 | Pod event timestamps vs readiness |
| Origin egress per pod start (GB) | W1.1, W1.2, W2.x | Network monitoring on node or blob store metrics |
| Duplicate weights on disk (same model, different engines) | W1.1 + W1.2 | `du` across model cache dirs |
| Dataset re-download count across training jobs | W2.1, W2.2, W3.2 | HF Hub download count or network counters |
| Checkpoint disk growth rate during long training | W4.1 | PVC usage over time |
| RWX PVC write throughput for multi-pod training | W3.2, W3.5 | fio from inside pods |
| Cold start origin fetches for TP=2 multi-node | W3.1 | Per-node egress during vLLM startup |
| Cross-region origin egress (same model, 3 regions) | W5.3 | Per-region network counters |
| Multi-provider credential glue (LOC and auth systems) | W5.x | Count distinct auth mechanisms in use |
| Cross-region checkpoint transfer time | W5.6 | Job migration wall time |
| Time to recover in DR (model weights availability) | W5.4 | From region kill to first response in survivor |

**Deliverable:** A running table at `lab/storage-pain-journal.md` (append-only) that
fills in actual measured values as each wave item ships. By the end of the showcase, we
will have a data-backed story of "this is what AI storage friction looks like today" -
which is exactly what Unbounded Storage's pitch needs to land.

**Note:** We are NOT building Unbounded Storage in this lab. We are NOT deploying Alluxio
or Fluid to solve these problems prematurely either - that would hide the pain we want to
document. We deploy naive-but-real storage patterns (local-path PVC, PVC with RWX, HF Hub
pulls, Azure Blob as origin) and measure what hurts.

**Audience:** This journal is **internal** - it feeds the unbounded-kube and Unbounded
Storage product teams. It is not a sponsor-facing artifact on its own; sponsor updates
summarize it, raw entries stay internal.

---

## GB200 / GB300 Transfer Plan

**Why this section exists:** sponsors will ask "does this work translate when we get
GB200/GB300?" Have the answer ready, not improvised. Every wave's deliverables get a
transfer-review at the end; this section is the rubric.

### What transplants unchanged

- All Kubernetes manifests (Deployments, StatefulSets, Jobs, Services, Ingresses,
  ConfigMaps, Secrets, NetworkPolicies). Sparks and GB200s both run k8s; manifests are
  hardware-agnostic.
- All container images that target ARM64 + CUDA. GB200/GB300 are also ARM-based (Grace
  CPU + Blackwell GPU). The ARM64 bring-up pain we eat today on Spark IS the work that
  pays off on GB200.
- unbounded-agent + unbounded-net join pattern. Same WireGuard-over-NAT approach;
  GB200/GB300 in a customer rack joins an AKS control plane the same way.
- Inference engine choices (Ollama, vLLM, SGLang). All have ARM64 builds.
- Training stack (PyTorch + HF Transformers + PEFT + Megatron-LM if R1 closes). NGC
  PyTorch ARM64 images are the same lineage on both.
- Observability (Prometheus + Grafana + DCGM exporter). Hardware-class is just a label.

### What changes (mostly relaxes)

- **Memory budget** balloons. GB200 has 192 GB HBM3e per GPU; GB300 has more. Models
  that need TP=2 multi-node on Spark fit on a single GB200 GPU. The "multi-node TP"
  demos become "single-node TP=N" demos - but the manifests STILL need to express this
  via env vars (`--tensor-parallel-size`), so the YAML changes by 1 line.
- **NVLink replaces ConnectX-7** for multi-GPU within a node. Multi-node still needs
  RoCE/IB. The intra-region multi-node story we build on Spark remains relevant but
  scales out (4-8 GB200s per node) instead of out (2 nodes per region).
- **MIG becomes available** on GB200/GB300. Multi-tenant GPU sharing - which is impossible
  on a 1-GPU Spark - becomes a real story. Out of scope for this lab; flag as future work.
- **Power, cooling, networking** at GB200 scale require datacenter-grade infra. Spark
  patterns assume edge-class power and cooling; the sizing math has to be re-done.

### What breaks (be honest)

- Anything we tune specifically for sm_120 (GB10) won't carry to sm_100/sm_103
  (GB200/GB300). The MoE kernel tuning JSON in `hack/E=256,N=1024,device_name=NVIDIA_GB10.json`
  is hardware-specific and will need a re-tune. This is normal and expected.
- Cost-per-token math is completely different. Spark numbers are not predictive of GB200
  numbers. Do NOT publish or quote Spark perf numbers as if they predict GB200 perf.
- Anything we hard-code to "1 GPU per node" assumptions breaks. Manifests should use
  `nvidia.com/gpu` resource requests, not implicit "the only GPU." Audit before transfer.

### Per-wave transfer review checklist

At end of each wave, fill in:

| Item | Transplants? | Changes needed | Re-test required |
|---|---|---|---|
| W1.1 Ollama manifests | y/n | | |
| W1.2 vLLM manifests | y/n | | |
| ... | | | |

Catches drift early. Anything that is Spark-specific gets called out and either generalized
or marked "Spark-only learning artifact, do not transplant."

---

## Team Learning Objectives

By end of plan, the engineer should have personally shipped at least one of each:

- **L1 - Inference deployment:** stand up a production-ish inference server (vLLM
  preferred, Ollama acceptable) end-to-end on k8s, including ingress, auth, observability,
  and a benchmark run
- **L2 - Fine-tuning:** run a LoRA fine-tune as a k8s Job, produce a usable adapter,
  evaluate it against the base model
- **L3 - RAG pipeline:** wire up an embedding model + vector store + LLM into a working
  retrieval pipeline served via a single endpoint
- **L4 - Multi-node distributed work:** debug at least one NCCL/Ray/multi-pod issue and
  ship a working multi-node deployment (vLLM TP=2 OR multi-node training)
- **L5 - Multi-region operations:** operate a workload across regions via the existing
  unbounded-agent joins and observe all regions from the central AKS control plane
- **L6 - GPU/driver troubleshooting:** diagnose and resolve at least one CUDA/driver/CDI
  /NCCL issue in writing, document for future team members / successors

### Tracking

End-of-wave retro: which objectives the engineer hit, which remain open. Anything not hit
by end of plan is a known gap, called out explicitly in the final sponsor update.

### Things we will NOT pretend to learn

- Bleeding-edge research (RL training, novel architectures, paper reproductions). Out of
  scope; consume models, don't train them from scratch.
- ML engineering as a discipline (loss curves, hyperparameter tuning, dataset cleaning).
  Out of scope; we deploy what works, we don't optimize what doesn't.
- Becoming an AI researcher. The engineer becomes an AI infra operator - a more durable
  skill and the one the sponsor is funding.

---

## First-Party Microsoft Story

**Why this section exists:** AKS + Microsoft alignment is the funded mandate. Make the
Microsoft stack visible and load-bearing throughout, not incidental. Sponsors should be
able to point at any layer and see Azure.

### Microsoft / Azure components in use

| Layer | Service | Status | How it's used |
|---|---|---|---|
| Control plane | AKS | in use | k8s control plane + gateway nodes |
| Networking | Azure CNI / Azure Load Balancer | in use | Pod and service networking; public ingress |
| DNS / TLS | Azure DNS + cert-manager (ACME) | in use | `vapa-ollama.canadacentral.cloudapp.azure.com` |
| Container registry | Azure Container Registry (ACR) | in use | All custom-built images pushed here |
| Object storage | Azure Blob | planned | Model weights origin, training datasets, checkpoints |
| Identity (workload) | Azure Workload Identity | planned | Pod -> Blob auth without static keys (Bearer token today) |
| Monitoring | Azure Monitor + Container Insights | planned (W1.5 augments) | Cluster + node metrics, log shipping |
| Edge join | unbounded-agent + unbounded-net (WireGuard) | in use | Sparks into AKS |
| Geo-routing (Wave 5) | Azure Front Door or Traffic Manager | planned | Multi-region request routing |
| Disaster recovery (Wave 5) | Azure Blob geo-replication | planned | Cross-region weight + checkpoint stage |

### Things to avoid

- Don't pull from Hugging Face Hub directly in a pod for production patterns - mirror to
  ACR or Azure Blob and pull from there. The Microsoft story requires Azure-hosted
  origins, not third-party origins.
- Don't use S3 / GCS / R2 for anything in this lab. Even if convenient for a one-off,
  the sponsor narrative breaks if a slide shows AWS in the architecture diagram.
- Don't roll our own auth where Azure AD / Workload Identity fits. The current Bearer
  token proxy is a tactical exception; document it as such, plan to replace with AAD
  integration before any cross-region endpoint (Wave 5) or external sharing.

### Architecture diagram requirement

Every wave's deliverable bundle includes an architecture diagram with Azure service
icons used everywhere they appear. Sponsors look at diagrams; make Azure visible.

---

## Execution Order

The Showcase Structure mapping table above organizes the work by sponsor narrative.
Actual execution follows the wave structure below, sequentially, by one engineer. Risk
spikes and dependencies drive ordering within each wave.

### Wave 1: Solidify what we have

**Goal:** everything currently running is reproducible from source-controlled manifests,
documented, and measured. No demo is credible on an ad-hoc setup, and we need a
stable baseline to measure the Storage Pain Journal against.

**Preconditions:** none (the deployments already exist ad hoc).

**Scope:**

- **W1.1 - Ollama manifests under `inference/ollama-qwen-moe/`**
  - StatefulSet pinned to spark-3d37 via node selector
  - PVC on local-path storage for model weights (Qwen 3.5 35B-A3B MoE, ~24 GB)
  - Service + Ingress fronted by the existing auth proxy
  - Engine parameters via env vars on the container (`OLLAMA_NUM_PARALLEL=2`,
    `OLLAMA_CONTEXT_LENGTH=65536`, `OLLAMA_KEEP_ALIVE`, etc.) - NOT a ConfigMap of
    "engine parameters"; Ollama reads env, not a config file
  - `make` target or doc that applies the bundle from scratch on a clean namespace
- **W1.2 - vLLM manifests under `lab/inference/vllm-qwen-moe/`**
  - Same pattern pinned to spark-2c24 (Qwen 3.6 35B-A3B MoE BF16, ~70 GB)
  - Includes the `hack/scratch/vllm-proxy.py` sidecar or a cleaned-up version
  - Includes the MoE tuning JSON as a ConfigMap (that one IS a config file)
  - **Sanity check as part of W1.2:** measure actual sustainable batch x context x prompt
    throughput, document in `docs/`. The "already running" claim is on the edge of OOM
    for KV cache at 32K and needs real numbers, not hand-waving.
- **W1.3 - Baseline Storage Pain Journal entries**
  - Measure, with stopwatch or `kubectl get events`:
    - Time from `kubectl apply` of a fresh namespace to first successful inference
    - Bytes egressed from Hugging Face / registry per pod start (tcpdump or az monitor)
    - Duplicate bytes on disk (same weights pulled by Ollama + vLLM separately)
    - Cold start time after node reboot (weights survive on local-path PVC under
      `/opt/local-path-provisioner/...`; measure pod-restart time, not re-pull time)
  - These numbers are the "before" for any future Unbounded Storage product
- **W1.4 - Uniform ingress/auth doc**
  - Short writeup (1 page) showing how both engines (Ollama, vLLM) today sit behind the
    same ingress, cert, and auth proxy. SGLang joins this pattern at W2.4 - this is the
    demo story for "standard k8s patterns apply to AI"
- **W1.5 - Observability foundation**
  - Prometheus + Grafana deployed on AKS, scraping the gateway nodes and both Sparks
  - NVIDIA DCGM exporter on each Spark for GPU utilization/memory/power
  - Node-exporter on each node for host-level metrics
  - Initial Grafana dashboard: per-node GPU util, memory, pod status, inference request
    rate for the deployed engines
  - Every later wave's measurements (Storage Pain Journal, benchmarks) go through this
    stack - it is a Wave 1 prerequisite, not a Wave 5 afterthought
  - Deliverable: `lab/observability/` + one dashboard screenshot in sponsor update
- **W1.6 - Benchmark harness**
  - Implements the harness described in the Benchmark Methodology section. Lives at
    `lab/bench/`, source-controlled, repeatable.
  - Used as part of the W1.2 vLLM sanity check to measure actual sustainable
    batch x context x prompt throughput; results land in `docs/`.
  - Deliverable: `lab/bench/` + one JSON results file from the W1.2 run.

**Definition of done:**
- Namespace `lab-ollama-qwen-moe` and `lab-vllm-qwen-moe` can be destroyed and recreated
  from repo in one command each (existing `qwen35-35b`/`qwen36-35b` namespaces are torn
  down on cutover per the Glossary's grandfathering rule)
- Storage Pain Journal has 4 measured rows (not TODOs)
- `docs/` has a single "what's currently deployed" page
- `kubectl get nodes` shows the AKS gateway nodes AND both Region A Sparks under one
  control plane - the spine artifact is real, source-controlled, and reproducible
- Prometheus/Grafana dashboard live with GPU metrics from both Sparks
- Benchmark harness landed in `lab/bench/` and used for the W1.2 sanity check
- Wave 1 transfer-review checklist filled in (GB200/GB300 transferability per item)

**Dependencies:** blocks all other waves. Solo engineer; no parallel work.

---

### Wave 2: Breadth

**Goal:** deploy the AI workloads customers run in production (one at a time, sequentially)
on the spine. The intra-region distributed climax (vLLM TP=2, Megatron multi-node) is
Wave 3. Single engineer works through items in order.

**Preconditions:** Wave 1 complete. Risk spike R1 (Megatron on ARM64) begins as the first
item of Wave 2 so a blocker surfaces early for downstream (Wave 3/4) dependencies.

**Scope (execute sequentially, top to bottom):**

- **W2.1 - Megatron-LM single-node on Spark - RISK SPIKE R1**
  - First in the wave so a blocker surfaces early. See Risk Register R1 for test/fallback.
  - Deliverable: working `lab/training/megatron-single/` manifest OR blocker report
- **W2.2 - LoRA fine-tuning as a K8s Job**
  - PEFT + Transformers + small base model (Qwen 3.6 7B or Llama 3.2 3B)
  - Plain `batch/v1` Job, not a CRD - keep it boring
  - Validates: PyTorch ARM64+CUDA, bf16 training on GB10, Jobs can own a GPU
  - Deliverable: `lab/training/lora-job/` + a notebook showing the adapter works
- **W2.3 - Evaluation pipeline**
  - `lm-evaluation-harness` in a K8s Job, targets Ollama/vLLM endpoints
  - Emits results as ConfigMap or Grafana-friendly metric
  - Deliverable: `lab/training/eval-harness/` + sample run comparing base vs LoRA
- **W2.4 - Third inference engine**
  - Recommendation: **SGLang** (structured output, agents). Fallbacks: TGI or llama.cpp
  - Deploy one mid-size dense model
  - Deliverable: `lab/inference/sglang-dense/` + a one-pager comparing with Ollama/vLLM
- **W2.5 - Dense chat/coding model**
  - Qwen 3.6 27B if shipped, else fallback chain (Qwen 3.5 32B → Llama 3.3 70B Q4)
  - Deliverable: `lab/models/ollama-dense/` + benchmark comparison vs MoE
- **W2.6 - Multimodal / vision**
  - Qwen2.5-VL 32B or Llama 3.2 Vision 11B, on vLLM or Ollama
  - Deliverable: `lab/inference/vllm-vision/` + curl demo
- **W2.7 - RAG pipeline**
  - Embedding model (BAAI/bge-m3) + ChromaDB (StatefulSet + local-path PVC) + LLM
  - Ingestion Job loads `docs/` into vector DB
  - Deliverable: `lab/rag/` + a query script
- **W2.8 - Small specialized model**
  - Whisper large-v3 (speech-to-text) OR reranker (bge-reranker) integrated with RAG
  - Deliverable: `lab/models/whisper/` or `lab/models/reranker/` + curl demo

The "single endpoint, multiple nodes" artifact is Wave 3's W3.1 (vLLM TP=2 across both
Sparks), not a Wave 2 item.

**Definition of done (Wave 2):**
- Items W2.1-W2.8 shipped, each with its own `lab/` subfolder and deliverable
- Storage Pain Journal grows with: dataset re-download pain, checkpoint disk growth rate,
  duplicate weight accumulation across multiple deployments
- Wave 2 transfer-review checklist filled in (each item annotated for GB200/GB300 fit)
- Learning objectives L1, L2, L3 hit (inference, fine-tuning, RAG)

**Dependencies:** Wave 1 done. Sequential execution; the single engineer owns the order.
W2.2 LoRA uses HF Transformers + PEFT and is independent of R1 (Megatron); it can ship
even if R1 is still open. R1 must close (or have an accepted fallback) before Wave 3 W3.2
(Megatron multi-node) and before W4.1 (continuous pre-training).

---

### Wave 3: Multi-node intra-region

**Goal:** prove unbounded-kube coordinates distributed GPU workloads across two edge nodes
using the ConnectX-7 link, for both inference and training.

**Preconditions:** Wave 2 complete; R1 (Megatron ARM64) closed or on accepted fallback.
R4 (ConnectX-7 NCCL bandwidth) is closed *inside this wave* at W3.0 before W3.1 starts;
it is not a pre-wave gate. R3 depends on R3.prime's pre-wave answer (see Risk Register).

Note: W3.1 (vLLM TP=2) does not depend on R1; it can start as soon as W3.0 passes, in
parallel with R1 closure if needed.

**Scope:**

- **W3.0 - Risk spike R4: ConnectX-7 NCCL bandwidth validation**
  - RDMA/RoCE is available on the Sparks; this spike measures achieved bandwidth, it is
    not an availability investigation.
  - Run `nccl-tests` all_reduce_perf between spark-3d37 and spark-2c24; also run
    `ib_send_bw` / `ib_write_bw` as an RDMA-path sanity check.
  - Expected: bandwidth within ~72% of link spec (200 Gbps = 25 GB/s nominal -> expect
    >= 18 GB/s).
    Significant shortfall points at NIC driver, MTU, or RoCE config to debug before any
    model work.
  - **Pod plumbing to confirm once:** container needs `/dev/infiniband` device mounts,
    `IPC_LOCK` capability, NCCL built with IB support, and for GPUDirect-RDMA the
    `nv_peer_mem` kernel module. Verify the current unbounded-agent CDI/device-plugin
    setup surfaces all of these on the Spark pods; fix the pod template where needed.
  - Document: link configuration (IP, MTU, NIC driver), measured bus bandwidth, and the
    pod-spec pattern that produced it (for re-use by W3.1/W3.2/W3.5).
- **W3.1 - vLLM TP=2 across both Sparks**
  - Deploy via **KubeRay** (`kuberay-operator` + `RayCluster` CR) OR **LeaderWorkerSet**
    (v0.4+, which vLLM now has first-class support for). Pick KubeRay if we want the
    mature, widely-used path; LWS if we want the leaner, single-dependency path. This
    IS the integration work, not a hand-rolled Deployment+Service.
  - Target model, honestly sized. **Default: Llama 3.1 70B FP8** (~70 GB on disk,
    ~35 GB/node TP-shard, leaves ~85 GB/node for KV cache, activations, and vLLM
    overhead). **Alternative: Qwen 3.5 122B-A10B Q4** (~70 GB) if FP8 path hits issues.
    **Llama 3.1 70B BF16 (~140 GB) is marginal** at ~70 GB/node with TP=2: technically
    fits in unified memory but leaves little KV room. Default stays FP8 unless we prove
    BF16 works.
  - Demo: a single endpoint that's actually 2 nodes under the hood, `nvidia-smi` on both
    shows GPU activity, `kubectl cordon` or `pkill` on one worker shows graceful request
    failure and the Ray / LWS reconcile behavior
  - Deliverable: `lab/inference/vllm-tp2/`
- **W3.2 - Megatron-LM TP+PP across both Sparks**
  - TP=2 across the two GPUs (intra-region all-reduce on ConnectX-7)
  - Small model for the demo (GPT 2-3B) but real distributed training
  - Tensorboard or wandb-ish output showing loss curve, both nodes active
  - Deliverable: `lab/training/megatron-multinode/` + a training run screenshot
- **W3.3 - Failure/resilience demo**
  - While vLLM TP=2 is serving, cordon one node; show the behavior (inference stops,
    k8s doesn't reschedule the worker until node returns - that's honest, don't pretend)
  - While Megatron is training, kill a worker pod; show checkpoint recovery
  - Deliverable: a short recorded demo + a doc explaining what k8s gives you for free and
    what still requires app-level work (honest about limits, good customer conversation)
- **W3.4 - ScalarLM single-node - RISK SPIKE R3**
  - **Pre-wave check (do THIS WEEK, not at Wave 3):** check TensorWave GHCR / repo for an
    arm64 tag; file an issue if absent. See R3.prime in the Risk Register for owner/date.
  - If image available: deploy on spark-3d37, run closed-loop train+serve example
  - If not: decide - (a) build our own image (2-3 eng-weeks), (b) skip and let Megatron
    be the flagship, (c) upstream a PR to TensorWave
  - Deliverable: working demo OR a clean go/no-go call
- **W3.5 - ScalarLM multi-node**
  - Only if W3.4 succeeded. Scale the same setup to both Sparks.
  - Deliverable: `lab/training/scalarlm/`

**Definition of done:**
- Two-node distributed inference serves a model that wouldn't fit on one node
- Two-node distributed training produces a loss curve
- Failure demo is recorded and understood
- ScalarLM path has a clear status (working, blocked, or explicitly skipped)

**Dependencies:** Wave 2 done. R1 closed or on fallback; R3 answer known via R3.prime;
R4 closes inside the wave at W3.0.

---

### Wave 4: Interleaved follow-ups (not a separate sponsor checkpoint)

**Goal:** round out the demo catalog with items that aren't on the critical path. These
are *interleaved* with Wave 2 / Wave 3 cooldown periods, not run as a distinct sequential
wave. There is no SC-* sponsor checkpoint between Wave 3 and Wave 5 - Wave 4 deliverables
roll into the SC-3 (after Wave 3) and SC-4 (after Wave 5) updates as appropriate.

**Preconditions:** any wave with capacity. The engineer picks these up when other items
are blocked or in cooldown.

**Scope:**

- **W4.1 - Continuous pre-training with checkpointing**
  - Extend Wave 3's Megatron job: longer run, periodic checkpoint to PVC, resume-on-restart
    demonstrated by deleting the pod
  - Adds data to Storage Pain Journal: checkpoint disk growth over N hours, RWX throughput
    if checkpoints shared across workers
- **W4.2 - MoE vs Dense side-by-side**
  - Grafana dashboard or a small web UI that queries Ollama-MoE, vLLM-MoE, Ollama-dense,
    SGLang-dense in parallel
  - Shows tokens/sec, memory used, quality on a fixed eval prompt
  - **Depends on** the dense-model decision from Wave 2 W2.5 - whichever model we actually
    shipped is what goes here; do not write against Qwen 3.6 27B as a given.

**Definition of done:** both items land in `lab/` and have a doc page.

**Dependencies:** W4.1 needs a working training stack from Wave 2/3 - either Megatron
(W2.1/W3.2) if R1 closed, or the R1 fallback stack (HF Transformers + Accelerate +
DeepSpeed). W4.2 needs W2.5 complete.

---

### Wave 5: Geo-distributed (hardware-gated)

**Goal:** show unbounded-kube managing GPU workloads across 3 regions from one AKS
control plane.

**Preconditions:** Waves 1-3 complete in Region A. Regions B and C Sparks physically
installed and joined to AKS via unbounded-agent / WireGuard. R5 spike scheduled near
the end.

**Region B/C onboarding (W5.0):**
- Repeat the W1.1 / W1.2 smoke tests on the B and C Sparks: same Ollama / vLLM manifests,
  different namespace/region. Proves the join worked.
- Storage Pain: per-region model download - we now pull the same weights to every region.
- Risk gate: R6 (CNI/MTU/WireGuard) fires here.

**Scope (in execution order):**

- **W5.1 - Multi-region smoke test**
  - Once B and C onboarding is complete, verify the same workload runs in all 3 regions
  - `kubectl get pods -A -o wide` shows the same workload live in all 3 regions
- **W5.2 - Cross-region observability rollup**
  - Extend Wave 1 Prometheus to scrape nodes in Regions B and C (federation or direct)
  - Single Grafana dashboard showing per-region GPU, throughput, egress, pod state
  - Note: the foundational observability stack already exists from W1.5; this is the
    "aggregate across regions" extension
- **W5.3 - Geo-routed inference**
  - Same model deployed in all 3 regions behind Azure Front Door (or Traffic Manager)
  - Demo: curl from 3 client locations, show which region served each request
  - Measure: cross-region egress if a request accidentally routes wrong
- **W5.4 - Disaster recovery demo**
  - Kill Region A (cordon all nodes); traffic fails over to B/C
  - Measure: time to failover, model ready state in surviving regions (big Storage Pain
    Journal entry if weights weren't pre-staged)
- **W5.5 - Regional model specialization**
  - Each region runs a different fine-tuned variant (regional language tuning, for example)
  - Client chooses region by endpoint; shows multi-tenant specialization story
- **W5.6 - Follow-the-sun batch training**
  - Training Job migrates between regions as timezone shifts utilization
  - Coarse-grained coordination only (WAN latency forbids TP across regions)
  - Storage Pain: cross-region checkpoint transfer cost is the headline number
- **W5.7 - Federated fine-tuning - RISK SPIKE R5**
  - Flower or NVIDIA FLARE running on all 3 regions, aggregating weight updates
  - ARM64 support unverified - ambitious, but kept in scope because eventual GB200/GB300
    hardware makes this work non-throwaway
  - If frameworks don't work, narrative pivot: "federated-pattern-via-checkpoint-sync"
    using W5.6's infrastructure

**Definition of done:** all 7 sub-items have demos. Cross-region observability works. DR
is demonstrable. Storage Pain Journal has all cross-region rows populated with real numbers.

**Dependencies:** Waves 1-3 done. R5 resolved (or fallback accepted) before W5.7.


### Critical risks and de-risk first

Detailed treatment in the Risk Register section below. TL;DR:
1. **R1** Megatron-LM on ARM64+CUDA (IS the W2.1 spike; blocks W3.2, W3.4, W4.1)
2. **R2** vLLM multi-node via Ray on ARM64 (blocks W3.1, W3.5)
3. **R3** ScalarLM ARM64+CUDA image (blocks W3.4, W3.5)
4. **R4** ConnectX-7 NCCL/RDMA bandwidth (blocks all of Wave 3, distributed parts of W5)
5. **R5** Federated learning framework on ARM64+CUDA (blocks W5.7 only)
6. **R3.prime** TensorWave ScalarLM ARM64 image status check (pre-wave, due within 1 week of
   this plan's adoption - a failure here reshapes Wave 3)
7. **R6** Region B/C edge join: CNI/MTU/WireGuard interactions (blocks all of Wave 5)

---

## Risk Register

Each risk has: **trigger** (what kicks off the spike), **test** (the minimum validation),
**pass criteria** (what "green" looks like), **if blocked** (fallback), **owner**, **blocks**.

### R1 - Megatron-LM on ARM64+CUDA (GB10)

- **Trigger:** first day of Wave 2. Run in parallel with LoRA work.
- **Test:** pull `nvcr.io/nvidia/pytorch:<latest>-py3`, confirm ARM64 + sm_120 (GB10)
  support via `torch.cuda.get_device_capability()`. Clone Megatron-LM, run
  `pretrain_gpt.py` on a tiny (125M-350M) config with synthetic data for 10 steps.
- **Pass criteria:** loss decreases, no NaN, no "unsupported arch" warnings, throughput
  within an order of magnitude of published small-config numbers.
- **If blocked:** (a) build Megatron from source against a Spark-compatible PyTorch build;
  (b) substitute HuggingFace Transformers + Accelerate + DeepSpeed for the "real training"
  story (less impressive but real); (c) escalate via NVIDIA enterprise channel.
- **Time-box:** if not green within 5 working days of trigger, switch to fallback (b)
  and proceed; do not block downstream waves on R1.
- **Owner:** engineer
- **Blocks:** W3.2 (Megatron multinode), W3.4 (ScalarLM), W4.1 (continuous pretraining).
  Note: R1 IS the W2.1 spike, so it does not "block" W2.1; it gates downstream waves.

### R2 - vLLM multi-node via Ray on ARM64

- **Trigger:** inside W3.1, after W3.0 (the RDMA/NCCL spike) closes green.
- **Test:** deploy vLLM with `--tensor-parallel-size 2` across spark-2c24 and spark-3d37,
  serving a small model (Qwen 3.6 7B) to rule out OOM as a confounder. Verify both
  `nvidia-smi`s show load; run a 1000-prompt throughput test.
- **Pass criteria:** serves correctly, throughput > single-node throughput for a model
  that fits on one node (sanity check), no Ray worker crashes over 10 min.
- **If blocked:** (a) try vLLM with native `--pipeline-parallel-size` path (no Ray);
  (b) fall back to SGLang multi-node (also uses Ray); (c) demote W3.1 to a "future work"
  and lean on Megatron for the multi-node story.
- **Time-box:** if not green within 5 working days of trigger, take fallback (a) or (b);
  if both fail, demote W3.1 per (c).
- **Owner:** engineer
- **Blocks:** W3.1, W3.5 (ScalarLM multi-node), W5.x distributed inference pattern

### R3 - ScalarLM ARM64+CUDA image

- **Trigger:** start of W3.4, *only if R3.prime returned "image available"*. If
  R3.prime answered "unavailable" or "no response," R3 does not fire; W3.4/W3.5 fall back
  to Megatron-standalone per R3.prime's "if blocked" path.
- **Test:** check TensorWave GHCR / docs for arm64 tag. If present, pull and run their
  `vllm + megatron + HF hub` smoke test on spark-3d37.
- **Pass criteria:** image pulls, starts, their example train+serve loop completes.
- **If blocked:** (a) build our own image from ScalarLM sources against an arm64 PyTorch
  (2-3 eng-weeks); (b) file an upstream issue/PR; (c) skip ScalarLM entirely - Megatron
  becomes the flagship training demo. Option (c) is the recommended fallback if we are
  schedule-pressured.
- **Time-box:** if not green within 5 working days of trigger, take option (c) and let
  Megatron carry the training narrative.
- **Owner:** engineer
- **Blocks:** W3.4, W3.5

### R3.prime - Pre-wave check of ScalarLM ARM64 status (schedule risk)

- **Trigger:** within 1 week of this plan being adopted. NOT at start of Wave 3 - that's
  too late to reshape plans.
- **Test:** (a) check TensorWave's GHCR and docs for an arm64 image tag; (b) file a GitHub
  issue on the ScalarLM repo asking for status if not obvious; (c) email TensorWave if we
  have a contact.
- **Pass criteria:** a written answer in the plan's notes - "image exists at tag X" OR
  "confirmed not yet, ETA Y" OR "no response in 5 days, assume unavailable".
- **If blocked (no answer in time):** assume unavailable, downshift W3.4/W3.5 to
  Megatron-standalone, note in the plan.
- **Owner:** engineer
- **Blocks:** accurate Wave 3 scoping; not a technical dependency

### R4 - ConnectX-7 NCCL/RDMA bandwidth between Sparks

- **Trigger:** start of Wave 3 (W3.0).
- **Test:** `nccl-tests all_reduce_perf` across the two Sparks over the ConnectX-7 link.
  Also run `ib_send_bw` / `ib_write_bw` if RDMA is on. Document MTU, NIC driver, IP config.
- **Pass criteria:** bus bandwidth >= 18 GB/s (~72% of 200 Gbps = 25 GB/s nominal) for
  large messages.
  RDMA path confirmed if available.
- **If blocked:** (a) debug NIC driver / RoCE config (likely what's needed); (b) accept
  Ethernet TCP speeds and re-scope expectations (distributed training will be slow but
  functional). Must NOT proceed to W3.1 or W3.2 before this is resolved because failures
  there will be hard to attribute.
- **Owner:** engineer
- **Blocks:** W3.1, W3.2, W3.3, all Wave 5 distributed items

### R5 - Federated learning framework on ARM64+CUDA

- **Trigger:** start of Wave 5 item W5.7.
- **Test:** deploy Flower server + 3 clients (one per region) running a simple CIFAR-style
  FL round. Or NVIDIA FLARE equivalent.
- **Pass criteria:** one full federated round completes with weight aggregation.
- **If blocked:** narrative pivot - demo "federated-pattern-via-checkpoint-sync" using
  W5.6's cross-region checkpoint transfer infrastructure. Explicitly scope W5.7 as
  "investigate, report findings, ship if feasible" rather than a must-ship item.
- **Owner:** engineer
- **Blocks:** W5.7 only

### R6 - Region B/C edge join: CNI / MTU / WireGuard interactions

- **Trigger:** first Region B (or Region C) Spark onboarding attempt at the start of
  Wave 5.
- **Test:** join one B-Spark to AKS via the same unbounded-agent + WireGuard pattern
  that worked for Region A. Run W1.1 + W1.2 smoke tests. Verify pod-to-pod connectivity
  across regions, MTU is consistent end-to-end, no fragmentation under load.
- **Pass criteria:** node `Ready`, GPU pods schedule, an Ollama smoke test responds, and
  a `ping -M do -s 1400` (or equivalent MTU probe) succeeds across the WireGuard tunnel.
- **If blocked:** (a) tune WireGuard MTU and Azure CNI MaxPodCIDR per node; (b) check for
  IP overlap between Region B's local subnet and AKS pod CIDR; (c) escalate to the
  unbounded-net team if the tunnel itself misbehaves.
- **Owner:** engineer
- **Blocks:** all of Wave 5.

### Risk review cadence

- End of each wave: review the register, mark closed risks, add any new risks surfaced.
- If a risk blocks for more than 1 week past its trigger, escalate to the "if blocked"
  path - do not let open risks stall the wave.

---

## Sponsor Update Cadence

Updates go to sponsors, not external customers. Single channel: monthly written. The goal
is "yes, fund the next quarter" - not selling to a prospect. No live walkthroughs are
planned; if a sponsor asks for one, we can assemble it ad hoc from the existing artifacts.

### Monthly written update (~1 page)

Format, every month, in `docs/sponsor-updates/YYYY-MM.md`:

1. **Headline (1 sentence):** the most important thing that shipped this month.
2. **Spine artifact status:** state of the edge-Spark pool. Nodes online per region,
   what's serving from each, recent failures.
3. **What shipped:** wave items completed with links to manifests + any recorded demos.
4. **What's blocked:** open risks from the Risk Register; what we're doing about them.
5. **Storage Pain Journal deltas:** new measured rows this month. Numbers, not narrative.
6. **Learning progress:** which L1-L6 objectives the engineer hit this month.
7. **GB200/GB300 transfer notes:** anything we learned that affects portability.
8. **Asks of the sponsor:** explicit (more hardware, ARM64-image escalation contact, etc.).

Brutal brevity. One page. If it's longer, it's not a status update, it's a wishlist.

### Artifact maintenance (supports the monthly update)

- Keep one short-recorded demo current for the spine artifact (re-record after significant
  changes). This is the go-to asset if a sponsor asks "can we see it?"
- **Rotate the demo endpoint's API key** before any external sharing - the current key
  has been circulated in planning docs and must not be used. Do not record any prefix
  or suffix of the rotated key in this plan or any other tracked file.
- Keep the architecture diagram current as Microsoft-first services are added.

---

## Sponsor Checkpoints

Replaces the previous "Minimum Viable Showcase" framing. Same idea: explicit stop points
so we have something coherent to show at end of each wave, regardless of what slips later.

**SC-1 (after Wave 1):** Foundation + Spine.
- What shipped: W1.1-W1.6 - Ollama and vLLM manifests reproducible from repo, edge
  Sparks joined to AKS via unbounded-agent, observability stack (Prometheus/Grafana/
  DCGM), benchmark harness, baseline Storage Pain Journal entries.
- Sponsor narrative: "The spine works. One engineer can stand up AI inference on edge
  ARM64 GPU nodes joined to an AKS control plane, with live observability."
- Decisions needed from sponsor: confirm Wave 2 priority/cuts (the W2.1-W2.8 list is
  enumerated; the question is what to drop if a wave runs long, not what to add).

**SC-2 (after Wave 2):** Breadth of AI workloads on the spine.
- What shipped: third inference engine, multi-modal, RAG, LoRA fine-tuning, eval
  pipeline, dense chat model - all running on Region A Sparks.
- Sponsor narrative: "The engineer can deploy and operate the AI workloads customers use
  in production. The spine carries real workloads, not just hello-world."
- Decisions needed: confirm we proceed to multi-node (Wave 3) vs add more breadth.

**SC-3 (after Wave 3):** Scale-out.
- What shipped: vLLM multi-node TP=2, Megatron multi-node training, honest resilience
  demo, ScalarLM if R3 closed.
- Sponsor narrative: "Models that don't fit on one node are served. Training spans
  nodes. Failure modes are documented honestly."
- Decisions needed: confirm Wave 5 scope.

**SC-4 (after Wave 5, hardware-gated on Regions B and C):** Geo + the climax architecture diagram.
- What shipped: Regions B and C onboarded, geo-routed inference, DR demo, cross-region
  observability, follow-the-sun training, federated fine-tuning if R5 closed.
- Sponsor narrative: "One AKS control plane, three regions of ARM64 edge GPU nodes,
  AI workloads serving and training across all of it - delivered by one engineer."
- Decisions needed: GB200/GB300 hardware procurement timeline; transition plan from
  Spark lab to GB200 production reference.

**Explicit rule:** each SC corresponds to a monthly written update with extra detail on
the wave just closed. Live walkthroughs are not promised; if a sponsor asks, assemble
ad hoc from the existing artifacts.

---


## Security and Multi-Tenancy (Internal Posture)

Internal lab. No external customers. Posture goals: keep the demo endpoint from being
abused; keep the lab from being a security embarrassment for sponsors.

**Current state (risk):**
- Single demo endpoint, Bearer token auth, key in planning docs.
- No rate limiting, no WAF, no abuse protection beyond the auth proxy.
- All namespaces on the cluster can talk to each other by default.
- Single tenant (us).

**Minimum posture before any sponsor walkthrough:**
- Rotate API key; store only in `Secret` objects, never in docs or chat.
- Move from Bearer token to Azure AD / Workload Identity for new endpoints (existing
  endpoint can keep Bearer until it's replaced).
- Rate limit at the ingress (nginx annotation or equivalent); at minimum 10 req/s per IP.
- NetworkPolicy denying cross-namespace traffic by default; allow-list only what wave
  items need (RAG retriever can talk to vector DB, etc.).
- Document that the demo endpoint is internal-lab-only, not production.

**GPU sharing reality on this hardware:**
- Each DGX Spark has 1 GB10 GPU. No MIG.
- At any moment, each GPU is running ONE workload at a time.
- With ~8 model deployments from Wave 2 and only 2 GPUs in Region A, most deployments are
  "deployable, not always running." The engineer schedules which are warm at any time.
- Sponsor walkthrough requirement: when we click into a model, allow warm-up time off-camera
  (model swap on Ollama is seconds; vLLM pod swap is much longer). Pre-warm, don't hot-swap.
- Multi-tenant GPU sharing on GB10 is NOT a story. On GB200/GB300 with MIG it becomes one;
  flagged as future work, not a Spark-lab capability.

---

## Licensing and Provenance (Internal Audit)

Even for an internal lab, we respect model licenses. Sponsors and audit will care.

| Model | License | Internal lab use OK? | Notes |
|---|---|---|---|
| Qwen 3.5/3.6 family | Apache 2.0 (most) | Yes | Check specific SKU; some Qwen releases had custom terms |
| Llama 3.x | Llama Community License | Yes | <700M MAU; lab use is fine |
| Gemma | Gemma Terms of Use | Yes with responsible-use clauses | Avoid prohibited-use scenarios in demos |
| Mistral Large | Mistral MNPL / Commercial | Avoid - commercial use requires paid license | Use Apache-2.0 alternatives |
| Whisper | MIT | Yes | |
| bge-m3 / nomic-embed | Apache 2.0 / MIT | Yes | |

**Rule:** every `lab/<area>/<model>/` folder includes a `LICENSE.md` citing the model card
and the license text/link. If a model's license is unclear, default to not deploying it.
Prefer Apache-2.0 / MIT models when there's a real choice.

---

## Benchmark Methodology

One-off curl numbers (e.g., "54.6 t/s on one prompt") are not credible performance data.
We need a repeatable harness.

**Benchmark script requirements:**
- Lives in `lab/bench/`; source-controlled (per the Code Organization section)
- Parameters: engine (ollama/vllm/sglang), endpoint URL, auth, model, prompt length,
  generation length, concurrency, number of runs
- Reports: p50/p95/p99 latency, tokens/sec, prompt eval rate, memory footprint at peak
- Runs warm-up requests before measurement
- Dumps JSON results for ingestion into a Grafana dashboard or doc

**Methodology:**
- Minimum 20 runs per config; drop first 3 as warm-up
- Report ALL of: prompt length, generation length, concurrency, quant, engine version,
  driver version, date - numbers without these are useless
- Re-run baselines monthly; staleness kills credibility

**Where the existing "54.6 t/s" number goes:**
- Recorded with context in `docs/dgx-spark-inference-perf.md` with a date stamp
- Not quoted as "current performance" - quote the latest harness run instead
- The April 2026 measurement becomes a historical data point for regression checks

---

## Rollback and Environment Strategy

Today's situation: one cluster, one set of edge nodes, live endpoints that users may be
pointing at. Wave 2 deploys new workloads that could destabilize existing ones.

**Rules going forward:**
- Existing `qwen35-35b` and `qwen36-35b` namespaces are **protected**. Changes only via
  manifests under `lab/`, and only after a dry-run on a scratch namespace.
- New engines/models from Wave 2+ deploy into fresh namespaces; existing endpoints stay
  untouched.
- If we need a breaking migration (e.g., switch Ollama to a new engine parameter), do it
  in a new namespace, cut traffic over, then tear down the old.
- No `kubectl edit` on production namespaces; always through manifest PRs.

**A "staging" cluster would help but we don't have one.** Work within one cluster using
namespaces + RBAC + NetworkPolicy as soft isolation. When Regions B and C come online for
Wave 5, consider dedicating one region (or one Spark within a region) to staging.

---


## Verification

Per-wave functional checks. Storage Pain Journal metrics (logged at `lab/storage-pain-journal.md`)
are the quantitative side; the items below are the qualitative pass/fail.

- **Wave 1**: each deployed engine (Ollama, vLLM) responds to curl requests with correct
  timing stats. Engine manifests live under `inference/`. Prometheus/Grafana shows
  GPU metrics from both Sparks. Benchmark harness produces JSON output.
- **Wave 2**: each model class returns plausible output for its workload type (dense chat
  completes; vision identifies an image; RAG retrieves and cites; speech-to-text or
  reranker functional). Fine-tuned LoRA adapter scores higher than base on the eval
  harness.
- **Wave 3**: ConnectX-7 NCCL bandwidth meets R4's pass criteria. Both Sparks show GPU
  utilization during vLLM TP=2 and Megatron multi-node runs. Cordoning one node behaves
  as documented in W3.3 (no pretense of magic). Megatron checkpoints resume across pod
  restart.
- **Wave 4**: continuous pre-training run resumes after pod delete. MoE-vs-dense
  side-by-side dashboard renders.
- **Wave 5**: Region B and C nodes `Ready`; per-region dashboards visible in Grafana.
  Region kill triggers traffic failover within expected SLO. Cross-region egress
  matches projections. Federated round (or checkpoint-sync fallback) completes.
- **Storage Pain Journal**: every row in the metrics table has a measured value, not
  a TODO.