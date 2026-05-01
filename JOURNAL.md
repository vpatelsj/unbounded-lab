# Storage Pain Journal

Append-only record of measured AI-data friction encountered while building the
unbounded-lab. See [ROADMAP.md](ROADMAP.md) → Storage Pain Journal section for
the why.

**Rules:**
- One row per measurement event. Date stamped. Cluster + node identified.
- Numbers, not narrative. If a metric is "felt painful" but not measured, it does
  not belong here yet.
- Never edit an existing row; append a new row if the situation changes.
- This file is **internal**; sponsor updates summarize it.

## Entries

| Date | Wave item | Metric | Value | Notes / how measured |
|---|---|---|---|---|
| 2026-04-28 | W1.1 | Cold pull origin egress (qwen3:30b-a3b) | 18.557 GB | Final `total` byte count from `/api/pull` NDJSON on `spark-3d37` (Region A, GB10). Single 18556685856-byte blob `sha256:58574f...c8eabf` + 4 small manifest blobs. |
| 2026-04-28 | W1.1 | Cold pull wall time (registry.ollama.ai -> spark-3d37 PVC, through ingress) | 408 s | `curl -X POST /api/pull` from operator workstation. 18.557 GB / 408 s = 45.5 MB/s sustained. 19 x 1 GB parallel parts (Ollama default). Pull completed at 2026-04-28T17:13:21Z. |
| 2026-04-28 | W1.1 | Cold model load into GPU (first generate after pull) | 34.4 s | `load_duration` from first `/api/generate`. Model resident in 119.7 GiB GB10 unified memory. |
| 2026-04-28 | W1.1 | Warm tokens/sec (qwen3:30b-a3b Q4_K_M, single stream, think=false, 200 tokens) | 77.4 t/s | `eval_count / eval_duration` from `/api/generate`. Single GB10 GPU, prompt: "Write one paragraph about kubernetes." |
| 2026-04-28 | W1.1 | Warm-PVC pod restart -> Ready (weights survive) | 11.7 s | `kubectl delete pod ollama-0` -> `kubectl wait --for=condition=Ready`. PVC `models-ollama-0` not deleted; container restarts in place. |
| 2026-04-28 | W1.1 | Warm-PVC pod restart -> first inference reply | 45.9 s | 11.7 s pod-Ready + 34.6 s model reload into GPU + ~0.4 s eval. The model load is not amortized across pod restarts; it is paid every time the runner process starts. |
| 2026-04-28 | W1.1 | Disk footprint after pull (Ollama models dir) | 18.557 GB blob + ~5 KB manifests | Reported by `/api/tags` (`size: 18556699314`). |

### Observations (2026-04-28, W1.1)

- **Cold pull from `registry.ollama.ai`** at ~45 MB/s is bandwidth-bound on the
  Spark uplink, not CPU-bound. This is the "first time on this node" cost.
  Re-pulls of the same digest are no-ops because Ollama deduplicates by
  content-addressed blob.
- **Pod restart is fast (~12 s) but first-inference is dominated by the
  34 s model reload** into GPU memory. Until Ollama has a "keep model
  resident across runner restarts" option, every pod recreate pays this cost.
  Candidate "warm cache" requirement for the future Unbounded Storage product.
- **Same logical model, different engines, different quantizations.** Pending
  W1.2: once vLLM pulls Qwen3-30B-A3B BF16 (~60 GB HF safetensors) we can
  compare against the 18.6 GB GGUF Q4_K_M Ollama uses for "the same model."
- **Cross-namespace dedup is not free.** A second namespace pulling the same
  Ollama tag would pull the 18.6 GB blob again into its own PVC. To be
  measured at W2.x or whenever a second Ollama deployment lands.

| 2026-04-30 | W1.2 | Cold pull origin egress (Qwen/Qwen3-30B-A3B-FP8 attempt, aborted) | ~32.4 GB | 7-shard HF safetensors + tokenizer/config. Pulled via vLLM's HF Hub loader through `dnsPolicy: None` resolver path (8.8.8.8), 282 s wall = ~115 MB/s. Variant abandoned because vLLM v0.11's CUTLASS scaled-mm has no sm_121a kernel; `cutlass_scaled_mm` raised bare `RuntimeError: Error Internal` during `profile_run`. PVC bytes were not reclaimed (HF Hub cache layout). |
| 2026-04-30 | W1.2 | Cold pull origin egress (Qwen/Qwen3-30B-A3B-GPTQ-Int4) | ~17.0 GB | Single-shard HF safetensors. `Time spent downloading weights: 148.158 s` per vLLM log -> ~115 MB/s sustained. This is the variant that actually serves; W1.3 dedup-pain comparison anchors here. |
| 2026-04-30 | W1.2 | PVC residual after deviation chain (FP8 -> GPTQ-Int4) | 56 GiB | `du -sh /var/lib/vllm` inside vllm-0. Two failed/abandoned variants stayed in HF Hub blob cache because the ConfigMap-mounted layout has no automatic GC and DGX OS denies `drop_caches`. The "two engines, two quantizations" duplication story is amplified by every quantization attempt that landed before the working one. |
| 2026-04-30 | W1.2 | Cold model load into GPU (GPTQ-Int4, post-download) | 86.6 s | `Model loading took 15.6066 GiB and 86.581768 seconds` per vLLM log. 15.6 GiB resident weights footprint matches the safetensors size to within Marlin packing overhead. |
| 2026-04-30 | W1.2 | Engine init (profile_run + KV alloc + warmup) | 26.3 s | `init engine (profile, create kv cache, warmup model) took 26.34 seconds`. Includes the dummy forward pass that tripped CUTLASS / FlashInfer kernel-image errors on prior attempts. |
| 2026-04-30 | W1.2 | KV cache pool at `--gpu-memory-utilization=0.22` | 9.31 GiB / 101 712 tokens | `Available KV cache memory: 9.31 GiB`, `GPU KV cache size: 101,712 tokens`, `Maximum concurrency for 32,768 tokens per request: 3.10x`. Constrained by the unreclaimable 65 GiB host page cache on the Spark; raise utilization once the node is rebooted clean. |
| 2026-04-30 | W1.2 | Warm-PVC pod restart -> Ready 2/2 | ~140 s | `kubectl delete pod vllm-0` -> 2/2 Ready. Dominated by 84 s safetensors load + 26 s engine init; PVC re-use eliminates the 148 s download. |
| 2026-04-30 | W1.2 | Plan-deviation chain: BF16 -> FP8 -> GPTQ-Int4 | n/a | DGX OS keeps `/proc/sys/vm/drop_caches` read-only even from privileged pods using `nsenter` into init's mount/pid namespace; ~65 GiB of unified memory stays pinned in unreclaimable host page cache after Spark attaches. BF16 (~60 GB) does not fit until reboot. FP8 fits but breaks on missing sm_121a CUTLASS kernels. GPTQ-Int4 dispatches via Marlin and serves. **Reboot follow-up filed against W1.3.**

### Observations (2026-04-30, W1.2)

- **Two engines, three quantizations on disk for one logical model.** Ollama has
  the GGUF Q4_K_M (~18.6 GB) on `spark-3d37`. vLLM has the GPTQ-Int4 (~17 GB)
  *and* the abandoned FP8 (~32 GB) blobs on `spark-2c24`. Same logical model
  card; ~67 GB of bytes spread across two PVCs and two nodes. Cross-engine
  dedup is impossible at the file level (different formats); cross-engine
  dedup is impossible at the GPU level (different kernel families). This is
  the irreducible storage waste W1.3 quantifies.
- **Bleeding-edge silicon penalty.** GB10 reports `sm_121a`. PyTorch 2.x caps
  at `sm_120`. vLLM v0.11's prebuilt PTXAS, CUTLASS, and FlashInfer wheels
  collectively cost three iterations of the StatefulSet (`--enforce-eager`,
  GPTQ-Int4 to dodge CUTLASS scaled-mm, `VLLM_USE_FLASHINFER_SAMPLER=0`).
  Document each fix inline because GB200/GB300 will hit the same class of
  problem before vendors ship binary wheels for the new arch.
- **`port-forward` plus `dnsPolicy: None` is hostile.** The DNS override
  routes `localhost` lookups to public resolvers, killing kubectl's port-
  forward proxy. Smoke tests run from a transient `curlimages/curl` pod in
  the cluster instead. Capture this as a runbook entry, not a code fix \u2014
  the DNS override is load-bearing for HF Hub pulls over unbounded-net.

| 2026-04-30 | W1.3 | Same-model duplicate bytes across engines (Ollama GGUF Q4_K_M + vLLM GPTQ-Int4) | 18.557 GB + 16 GB = 34.6 GB | `du -sh /var/lib/ollama/models` on `spark-3d37` (Ollama PVC `models-ollama-0`, blob `sha256:58574f...c8eabf` = 18 556 685 856 B) plus `du -sh /var/lib/vllm/hf/hub/models--Qwen--Qwen3-30B-A3B-GPTQ-Int4` on `spark-2c24` (vLLM PVC `vllm-cache-vllm-0`). Same logical model card (`Qwen/Qwen3-30B-A3B`). Two engines, two formats, zero file-level overlap. |
| 2026-04-30 | W1.3 | Total duplicate bytes including abandoned variant (FP8 residual) | 18.557 GB + 16 GB + 31 GB + 9.4 GB = 74.96 GB | Adds `models--Qwen--Qwen3-30B-A3B-FP8` (31 GB) and the HF Xet chunk-cache (`/var/lib/vllm/hf/xet`, 9.4 GB) which the Hub loader leaves behind. The FP8 variant never served (sm_121a CUTLASS gap, see W1.2 row); its bytes still occupy the vLLM PVC. Cleaning the FP8 dir would reclaim 31 GB; the Xet cache regenerates if reaped. |
| 2026-04-30 | W1.3 | Cross-engine, cross-namespace, cross-node footprint for one logical model | 92.6 GB | `models-ollama-0` (18.6 GB on spark-3d37) + `vllm-cache-vllm-0` (74.0 GB on spark-2c24, where 56 GB is HF cache and ~18 GB is the operator's HF Hub mirror metadata + lock files). Two PVCs, two namespaces, two nodes. Unbounded Storage's "one canonical blob, projected views per engine" pitch lands directly here: today the lab pays full price for every engine that wants to serve `Qwen3-30B-A3B`. |
| 2026-04-30 | W1.3 | Spark host page-cache pinned by initial pulls (reboot-pending) | ~65 GiB unreclaimable | Per the W1.2 row "Plan-deviation chain": DGX OS denies `/proc/sys/vm/drop_caches` from privileged pods using `nsenter` into init's mount/pid namespace, and `MemAvailable` tracks ~65 GiB below `MemTotal` after a sequence of large HF Hub pulls. The follow-up scheduled for W1.3 is to reboot `spark-2c24`, re-deploy the same vLLM StatefulSet from the unchanged manifest, and record the post-reboot `--gpu-memory-utilization` headroom (currently capped at 0.22 by the page-cache squeeze; expect 0.85+ post-reboot). The W1.5 Grafana dashboard plots `node_memory_MemAvailable_bytes` and buff/cache to make this measurable rather than narrative. |
| 2026-04-30 | W1.3 | Reboot follow-up status | open | Filed as the only W1.3 carry-over into Wave 2. Blocked on coordinating downtime for `spark-2c24` (it serves the W1.2 vLLM endpoint and Open WebUI's vLLM backend through the W1.4 ingress). Action: drain the node, reboot, restore, re-deploy `inference/vllm-qwen-moe/` unchanged, append a row with the new `Available KV cache memory` and `MemAvailable` peak observed in Grafana over 1 hour idle. |

### Observations (2026-04-30, W1.3)

- **The "two engines, one model, double bytes" claim is now a measured 34.6
  GB**, not a hand-wave. Add the abandoned FP8 variant and Xet chunk cache
  and the lab is sitting on 75 GB of bytes for one logical model that
  serves at most ~17 GB worth of GPU weights at any moment.
- **Local-path PVCs survive node reboot.** The cold-start cost we measure
  post-reboot is therefore the *load-into-GPU* cost (W1.1: 34.4 s for
  Ollama, W1.2: 86.6 s for vLLM safetensors), not the
  *re-pull-from-internet* cost. This is the right measurement for the
  Unbounded Storage pitch — "what does the customer pay every time the
  process restarts" — because re-pulls are amortized away by any
  competent cache, but the GPU load is structural.
- **W1.5 makes this self-measuring.** The "Spark host: memory used %,
  buff/cache GiB" panels in the Grafana dashboard plot exactly the metric
  the reboot follow-up needs to show movement on. Once the reboot lands,
  the dashboard before/after is the artifact, not a one-off `cat
  /proc/meminfo`.
