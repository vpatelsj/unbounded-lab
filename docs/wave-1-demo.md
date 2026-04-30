# Wave 1 demo

A 10-minute live walkthrough hitting every W1.x deliverable. Run from the
repo root with kubeconfig pointed at `apollo-lab-bou-gw`. Names match the
actual cluster (verified 2026-04-30).

| Component        | Namespace             | Pod / target                              | Service                                      |
|------------------|-----------------------|-------------------------------------------|----------------------------------------------|
| Ollama           | `lab-ollama-qwen-moe` | `ollama-0`                                | `ollama` :11434                              |
| vLLM             | `lab-vllm-qwen-moe`   | `vllm-0` (containers: `vllm`, `proxy`)    | `vllm` :8000 (OpenAI), :11434 (Ollama-shim)  |
| Open WebUI       | `lab-openwebui`       | `open-webui-*`                            | `open-webui` :80                             |
| Ingress (shared) | `lab-ingress`         | (kustomize host-literal CM only)          | via `ingress-nginx` LB                       |
| Observability    | `lab-observability`   | kube-prom-stack + dcgm-exporter DS        | `lab-obs-grafana` :80, `...-prometheus` :9090|
| Bench            | `lab-bench`           | Job `lab-bench-vllm-w1-2`                 | n/a                                          |

Public hostname: `https://vapa-ollama.canadacentral.cloudapp.azure.com`.

---

## 0. Pre-flight (30 s)

```bash
kubectl get nodes -L lab.unbounded.cloud/hardware-class,region
```
Expect 5 nodes: 2× system + 2× gwmain (amd64) + `spark-2c24` + `spark-3d37`
(arm64, label `lab.unbounded.cloud/hardware-class=dgx-spark-gb10`,
`region=a`).

```bash
kubectl get pods -A | grep -E "lab-(ollama|vllm|openwebui|observability|bench|ingress)"
```

All `Running` / `Ready`.

---

## 1. W1.4 — Public TLS endpoint (1 min)

Open in a browser:

> https://vapa-ollama.canadacentral.cloudapp.azure.com

Show the padlock → "Issued by: Let's Encrypt". Then:

```bash
curl -sI https://vapa-ollama.canadacentral.cloudapp.azure.com | head -5
kubectl -n lab-openwebui get ingress
kubectl -n lab-openwebui get certificate
```

Talking points: ingress-nginx + cert-manager `letsencrypt-prod`
ClusterIssuer; cert auto-renews; same hostname will sit behind Front Door
in Wave 3.

---

## 2. W1.1 — Open WebUI + Ollama chat (2 min)

In the browser, log into Open WebUI. Pick the Ollama-served model
(`qwen3:30b-a3b`, GGUF Q4_K_M on `spark-3d37`). Send:

> Write a haiku about unified memory.

Watch tokens stream. In a side terminal, prove Ollama actually served it:

```bash
kubectl -n lab-ollama-qwen-moe logs ollama-0 --tail=20
kubectl -n lab-ollama-qwen-moe exec ollama-0 -- ollama list
```

Talking points: 18.6 GB Q4_K_M GGUF on a local-path PVC; survives pod
restart; pinned to `spark-3d37` via `nodeSelector`.

---

## 3. W1.2 — vLLM serving Qwen MoE (1 min)

Switch the Open WebUI model dropdown to the vLLM-backed entry (the
Ollama-shim sidecar makes vLLM look like another Ollama model). Send:

> Same haiku, but make it about KV cache.

```bash
kubectl -n lab-vllm-qwen-moe get pods,svc
kubectl -n lab-vllm-qwen-moe logs vllm-0 -c vllm --tail=15 \
  | grep -E "engine|Avg|Running|KV"
```

Talking points: GPTQ-Int4 (16 GB on local PVC),
`--gpu-memory-utilization=0.22`, KV pool 9.31 GiB / 101 712 tokens — capped
not by GPU but by ~65 GiB of unreclaimable host page cache. That cap is
the bridge to the next step.

---

## 4. W1.3 — Storage pain receipts (30 s)

```bash
sed -n '1,40p' storage-pain-journal.md
```

Three real rows:
1. 31 GB FP8 weights downloaded then abandoned (vLLM 0.11.0 + GB10 sm_121a
   doesn't compile FP8 Marlin kernels yet).
2. 16 GB GPTQ-Int4 landed; 9.4 GB HF Xet leftover.
3. ~65 GiB unreclaimable host page cache on `spark-2c24` capping KV pool.

Reboot runbook ready:

```bash
ls docs/spark-reboot-runbook.md
```

---

## 5. W1.5 — Observability (2 min)

```bash
kubectl -n lab-observability get pods
```

Port-forward Grafana:

```bash
kubectl -n lab-observability port-forward svc/lab-obs-grafana 3000:80
```

Open http://localhost:3000 → log in → dashboard **"Spark GPU + vLLM"**:

- GPU power (`DCGM_FI_DEV_POWER_USAGE`) — flat ~32 W idle, jumps to ~36 W
  under bench load. (DCGM on Spark Tegra silently omits `FB_USED`, so
  power is the "GPU was busy" signal here. See
  [observability/values-dcgm-exporter.yaml](../observability/values-dcgm-exporter.yaml).)
- GPU util, mem-copy util, SM clock per Spark.
- vLLM `/metrics`: running requests, tokens/s, KV cache usage.

To make panels move, kick a single-prompt smoke through vLLM in another
terminal:

```bash
kubectl -n lab-vllm-qwen-moe port-forward svc/vllm 8000:8000 &
curl -s localhost:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"Qwen/Qwen3-30B-A3B-GPTQ-Int4","messages":[{"role":"user","content":"Count to 50."}]}' \
  | jq -r '.choices[0].message.content' | head -3
```

---

## 6. W1.6 — Reproducible bench (3 min)

The "press one button" demo:

```bash
make w1.6-run-vllm           # creates Job, waits for completion (~3 min)
make w1.6-results-fetch      # copies JSON off the PVC
jq '.phases[] | {concurrency,
                 aggregate_decode_tokens_per_s,
                 p50_latency_s,
                 peak_gpu_power_w}' \
  bench/results/lab-bench-vllm-w1-2.json
```

Expected (reproduced 2026-04-30):

| c  | aggregate t/s | p50 (s) | peak W |
|---:|--------------:|--------:|-------:|
|  1 |          62.5 |    2.05 |   32.3 |
|  4 |         199.6 |    2.21 |   35.8 |
|  8 |         299.2 |    2.47 |   35.8 |
| 16 |         462.2 |    2.81 |   35.8 |

Talking points: linear-ish scale to c=16, p50 only doubles for 7×
throughput, GB10 saturates near 36 W under load. Stdlib-only Python
harness, schema `unbounded-lab-bench/v1`, runs in `python:3.12-slim`.

---

## 7. The deliverable bundle (30 s)

```bash
ls docs/ sponsor-updates/ GLOSSARY.md
```

- [docs/architecture-wave-1.md](architecture-wave-1.md) — mermaid + the
  first-party Microsoft surfaces table.
- [docs/wave-1-state.md](wave-1-state.md) — endpoints, PVCs, headline
  numbers.
- [docs/wave-1-transfer-review.md](wave-1-transfer-review.md) — per-item
  GB200/GB300 transplant cost.
- [docs/spark-reboot-runbook.md](spark-reboot-runbook.md) — operational
  playbook for the 65 GiB page-cache problem.
- [GLOSSARY.md](../GLOSSARY.md) — names / labels / regions canon.
- [sponsor-updates/2026-04.md](../sponsor-updates/2026-04.md) — Wave 1
  closer.

---

## 8. Wave 2 preview (30 s)

Open [docs/wave-1-transfer-review.md](wave-1-transfer-review.md) and walk
the table. Short version: ingress, observability, Open WebUI, and the
bench harness all transplant cleanly to GB200; vLLM picks up FP8 + the
GB10 MoE tuning ConfigMap goes away; DCGM starts exporting `FB_USED` for
free, so the bench harness flips its peak query back.

---

## Cheat sheet

```bash
# Ollama
kubectl -n lab-ollama-qwen-moe logs ollama-0 --tail=20
kubectl -n lab-ollama-qwen-moe exec ollama-0 -- ollama list

# vLLM
kubectl -n lab-vllm-qwen-moe logs vllm-0 -c vllm --tail=20
kubectl -n lab-vllm-qwen-moe port-forward svc/vllm 8000:8000

# Grafana
kubectl -n lab-observability port-forward svc/lab-obs-grafana 3000:80

# Bench
make w1.6-run-vllm && make w1.6-results-fetch
```
