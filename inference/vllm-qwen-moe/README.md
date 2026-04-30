# W1.2 - vLLM serving Qwen MoE on spark-2c24

Wave item: **W1.2** (see [`../../plan.md`](../../plan.md)).

Deploys a [vLLM](https://github.com/vllm-project/vllm) OpenAI-compatible
server pinned to `spark-2c24` (Region A, GB10) with weights cached on a
local-path PVC. Serves the **same logical model** as W1.1's Ollama
deployment (`Qwen/Qwen3-30B-A3B`) but at a **different quantization**
(GPTQ-Int4 safetensors ~17 GB vs Ollama's GGUF Q4_K_M ~18.6 GB) — this
asymmetry is exactly the dedup-pain W1.3 measures.

> **Plan deviation — model variant.** The plan calls for BF16 (~60 GB).
> Two compounding GB10 realities forced the variant choice:
>
> 1. **Memory pressure.** A freshly-attached spark-2c24 has ~65 GiB of host
>    page cache pinning unified memory; DGX OS keeps `/proc/sys/vm/drop_caches`
>    read-only even from a privileged pod via `nsenter`. Until the node is
>    rebooted, BF16 will not fit.
> 2. **vLLM v0.11 + sm_121a.** GB10 reports compute capability `sm_121a`.
>    vLLM v0.11's prebuilt CUTLASS scaled-mm kernel (the FP8 path) targets
>    sm_80/89/90/100 only — `cutlass_scaled_mm` raises bare
>    `RuntimeError: Error Internal` during `profile_run` on FP8 weights.
>    GPTQ-Int4 dispatches to Marlin instead, which works.
>
> The full deviation chain (BF16 → FP8 → GPTQ-Int4) is captured in
> [`../../storage-pain-journal.md`](../../storage-pain-journal.md). Revisit
> when (a) Spark is rebooted clean, and/or (b) vLLM ships GB10-aware
> CUTLASS / FlashInfer kernels.

**No public Ingress.** Cluster-internal only. Open WebUI talks to vLLM via
two service ports:

- `vllm:8000` — native OpenAI API (`/v1/chat/completions`, `/v1/completions`).
- `vllm:11434` — Ollama-compatible shim (`/api/chat`, `/api/generate`,
  `/api/tags`, ...) served by a tiny Python sidecar so existing Ollama
  clients (including the W1.1 measurement runbook) work against vLLM
  unchanged. Public-API ingress with shared auth/TLS is W1.4's job.

## What it proves

- vLLM runs on ARM64 + GB10 (sm_121a) under the `nvidia` RuntimeClass on a
  Spark joined to AKS via unbounded-agent.
- Same `lab.unbounded.cloud/hardware-class=dgx-spark-gb10` label, same
  PVC-on-local-path pattern, same RuntimeClass as W1.1 — multiple inference
  engines coexist on one platform with one set of conventions.
- The MoE Triton kernel tuning surface is wired: `VLLM_TUNED_CONFIG_FOLDER`
  -> ConfigMap. The actual GB10-tuned JSON is a TODO documented inline (see
  [`configmap-tuning.yaml`](configmap-tuning.yaml)) and is the
  Spark-specific learning artifact called out in the GB200/GB300 transfer
  plan.
- The sidecar pattern (Ollama-compat shim) demonstrates that "standard k8s
  patterns apply to AI" — the integration is a stdlib Python script in a
  ConfigMap, not a custom image.
- The bring-up exercised four distinct GB10 + vLLM-v0.11 incompatibilities
  worth noting for future Wave items and any GB200/GB300 transfer:
  - **Image arch.** vLLM tags before `v0.11.0` are amd64-only on Docker Hub.
  - **Service-link env collision.** A k8s Service named `vllm` injects
    `$VLLM_PORT=tcp://...` into all pods in the namespace; vLLM reads that
    as its own listen-port and crashes. Fix: `enableServiceLinks: false`.
  - **PTXAS sm_121a.** vLLM v0.11's bundled Triton/PTXAS only goes up to
    sm_120. Fix: `--enforce-eager` (skips inductor/CUDA-graph compile).
  - **Prebuilt CUDA kernels.** CUTLASS scaled-mm (FP8) and FlashInfer
    (`TopKMaskLogits` sampler) ship without sm_121 binaries. Fix: prefer a
    Marlin-dispatched quantization (GPTQ-Int4) and force the native
    PyTorch sampler with `VLLM_USE_FLASHINFER_SAMPLER=0`.

## Files

| File | Role |
|---|---|
| `namespace.yaml` | `lab-vllm-qwen-moe` namespace |
| `configmap-tuning.yaml` | `VLLM_TUNED_CONFIG_FOLDER` contents (placeholder JSON, see file) |
| `configmap-proxy.yaml` | Stdlib-only Python proxy: Ollama API -> vLLM OpenAI API |
| `statefulset.yaml` | Single-replica StatefulSet pinned to `spark-2c24`, vllm + proxy sidecar |
| `service.yaml` | ClusterIP `vllm` (ports 8000+11434) and headless `vllm-headless` |
| `kustomization.yaml` | Bundles everything |

## Deploy

```sh
make w1.2-vllm-up
```

Equivalent manual flow:

```sh
kubectl apply -k inference/vllm-qwen-moe
kubectl -n lab-vllm-qwen-moe rollout status statefulset/vllm --timeout=60m
```

The first rollout pulls ~17 GB of GPTQ-Int4 safetensors from Hugging Face
Hub. The StatefulSet's `startupProbe` allows up to ~60 minutes for this;
subsequent restarts re-use the PVC and come up in seconds.

If the model becomes gated, drop a token in:

```sh
kubectl -n lab-vllm-qwen-moe create secret generic hf-token \
  --from-literal=token=hf_xxx
```

(The env var is wired with `optional: true`; the secret can be absent.)

## Reaching the API

In-cluster (default consumer pattern):

```
http://vllm.lab-vllm-qwen-moe.svc.cluster.local:8000   # native OpenAI
http://vllm.lab-vllm-qwen-moe.svc.cluster.local:11434  # Ollama-compat shim
```

From your workstation (development / smoke tests):

```sh
kubectl -n lab-vllm-qwen-moe port-forward svc/vllm 8000:8000 11434:11434 &

# Native OpenAI API
curl -sS http://localhost:8000/v1/models | jq
curl -sS http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "Qwen/Qwen3-30B-A3B-GPTQ-Int4",
        "messages": [{"role":"user","content":"hello"}],
        "stream": false
      }' | jq -r '.choices[0].message.content'

# Ollama-compat shim (same routes Open WebUI uses against W1.1)
curl -sS http://localhost:11434/api/tags | jq
curl -sS http://localhost:11434/api/generate \
  -d '{"model":"qwen3:30b-a3b","prompt":"hello","stream":false}' \
  | jq -r .response
```

> **`port-forward` + `dnsPolicy: None` gotcha.** The pod overrides DNS to
> public resolvers (8.8.8.8/1.1.1.1) to work around Spark CoreDNS
> instability for HF Hub pulls. kubectl's port-forward proxy resolves
> `localhost` *inside* the pod's netns and the override sometimes routes
> that lookup to 8.8.8.8 instead of `/etc/hosts`, killing the forward with
> `lookup localhost on 8.8.8.8:53: no such host`. Workaround: smoke-test
> from a transient curl pod in the cluster instead, e.g.
>
> ```sh
> kubectl -n lab-vllm-qwen-moe run vllm-test --rm -i --restart=Never \
>   --image=curlimages/curl:8.10.1 -- \
>   -sS http://vllm.lab-vllm-qwen-moe.svc:8000/v1/models
> ```

## Status

```sh
make w1.2-vllm-status
```

## Teardown

```sh
make w1.2-vllm-down
# The PVC is deleted with the namespace. Re-deploying triggers a fresh ~17 GB
# safetensors pull — that is the W1.3 cold-start measurement event.
```

## Pain measurement runbook (W1.3, vLLM half)

Pair these with the Ollama numbers already in
[`../../storage-pain-journal.md`](../../storage-pain-journal.md). All
measurements run on `spark-2c24`.

1. **Cold pull origin egress (Qwen3-30B-A3B-GPTQ-Int4).** Tear down and
   redeploy:
   ```sh
   make w1.2-vllm-down && make w1.2-vllm-up
   kubectl -n lab-vllm-qwen-moe logs -f statefulset/vllm -c vllm | grep -i 'download\|fetched'
   ```
   Cross-check on the wire from `spark-2c24`:
   ```sh
   sudo tcpdump -i any -w /tmp/vllm-pull.pcap host huggingface.co or host cdn-lfs.huggingface.co
   capinfos -b /tmp/vllm-pull.pcap
   ```
   Compare with W1.1's 18.557 GB. The delta is the "same model, two
   engines, two quantizations" duplicate-bytes-on-disk story.
2. **Cold pull wall time -> first inference.** Time from `make w1.2-vllm-up`
   to the first non-error reply on `/v1/chat/completions`. Record the
   pod-Ready gap, the safetensors-download gap, and the model-load-into-GPU
   gap separately; vLLM logs all three.
3. **Warm-PVC pod restart -> first inference reply.** `kubectl -n
   lab-vllm-qwen-moe delete pod vllm-0`; weights survive on the PVC; measure
   pod-Ready and first reply.
4. **Disk footprint after pull.** From a privileged debug pod on
   `spark-2c24`:
   ```sh
   kubectl debug node/spark-2c24 -it --image=alpine \
     -- du -sh /host/opt/local-path-provisioner
   ```
5. **Sustainable batch x context x prompt throughput.** Run the W1.6
   benchmark harness against `vllm:8000`. Numbers land in
   [`../../docs/w1.2-vllm-sanity.md`](../../docs/w1.2-vllm-sanity.md), per the
   plan's W1.2 sanity-check requirement.

## MoE Triton kernel tuning (Spark-specific learning artifact)

The placeholder JSON in [`configmap-tuning.yaml`](configmap-tuning.yaml) is
intentionally empty. Generate the real one **on `spark-2c24`** with
vLLM's tuning script (inside the running container or an equivalent
ad-hoc pod):

```sh
python -m vllm.model_executor.layers.fused_moe.tuning \
  --model Qwen/Qwen3-30B-A3B --tp-size 1 --dtype bfloat16
```

The script writes `E=<experts>,N=<intermediate>,device_name=NVIDIA_GB10.json`
into the local cache. Copy that file's contents into the ConfigMap (replace
`{}` and rename the key if E/N differ from the placeholder), redeploy, then
re-run W1.6 benchmarks to quantify the lift.

This file is **hardware-specific** and will need a re-tune on
GB200/GB300 — see the GB200 / GB300 Transfer Plan section of `plan.md`.

## Known limitations

- **Single replica, single GPU.** No HA. Replacing the node loses the PVC.
- **No public ingress, no auth.** Cluster-internal only. W1.4 will introduce
  the shared public-API ingress + auth proxy pattern.
- **Ollama-compat shim is intentionally minimal.** Implements only the
  routes Open WebUI and the W1.1 runbook actually call (`/api/version`,
  `/api/tags`, `/api/show`, `/api/chat`, `/api/generate`, no-op `/api/pull`).
  Anything else returns 404; clients should fall back to `/v1/*` against
  port 8000 directly.
- **Tuned MoE kernel JSON is a placeholder.** Performance numbers measured
  before the JSON is filled in are baseline-only and must be re-measured
  after tuning lands.
