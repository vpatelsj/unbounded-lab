# W1.1 — Ollama serving Qwen MoE on spark-3d37

Wave item: **W1.1** (see [ROADMAP.md](../../ROADMAP.md)).

Model: [`Qwen/Qwen3-30B-A3B`](https://huggingface.co/Qwen/Qwen3-30B-A3B) — Apache-2.0
(Alibaba Cloud). Served here as the GGUF Q4_K_M quantization via Ollama.

Deploys an Ollama server pinned to `spark-3d37` (Region A, GB10) with weights
on a local-path PVC. **No public Ingress.** Customer-facing access is via
Open WebUI (see [../openwebui/](../openwebui/)), which proxies Ollama
server-side over the cluster network. For direct API access during
development, use `kubectl port-forward`.

Public-API ingress with shared auth/TLS is W1.4's job.

## What this proves

- Ollama runs on ARM64 + GB10 (sm_120) under the `nvidia` RuntimeClass.
- Local-path PVC pins weights to the node; survives pod restarts; does not
  survive PVC deletion or node loss (W1.3 measures both).
- Engine parameters configured via env vars on the container, not a
  ConfigMap (Ollama reads env, not a config file).
- ClusterIP Service is enough for in-cluster consumers (Open WebUI, vLLM
  side-by-side comparisons in W1.2, the W1.6 benchmark harness).

## Files

| File | Role |
|---|---|
| `namespace.yaml` | `lab-ollama-qwen-moe` namespace |
| `statefulset.yaml` | Single-replica StatefulSet pinned to `spark-3d37` |
| `service.yaml` | ClusterIP `ollama` and headless `ollama-headless` |
| `kustomization.yaml` | Bundles everything |

## Deploy, status, teardown

```sh
make w1.1-ollama-up        # idempotent
make w1.1-ollama-status
make w1.1-ollama-down      # PVC is deleted with the namespace; redeploy triggers a fresh pull
```

Equivalent manual flow:

```sh
kubectl apply -k inference/ollama-qwen-moe
kubectl -n lab-ollama-qwen-moe rollout status statefulset/ollama --timeout=10m
```

## API access

In-cluster (default consumer pattern):

```
http://ollama.lab-ollama-qwen-moe.svc.cluster.local:11434
```

From your workstation (development / smoke tests):

```sh
kubectl -n lab-ollama-qwen-moe port-forward svc/ollama 11434:11434 &
curl -sS http://localhost:11434/api/version
curl -sS http://localhost:11434/api/generate \
  -d '{"model":"qwen3:30b-a3b","prompt":"hello","stream":false}' | jq -r .response
```

First-time model pull (this is the W1.3 cold-start measurement step; keep the
timing output):

```sh
kubectl -n lab-ollama-qwen-moe port-forward svc/ollama 11434:11434 &
START=$(date -u +%s)
curl -sS -X POST http://localhost:11434/api/pull \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:30b-a3b","stream":true}' \
  -o /tmp/ollama-pull.ndjson
echo "DURATION: $(( $(date -u +%s) - START ))s"
```

## Pain runbook

Record results in [JOURNAL.md](../../JOURNAL.md).

1. **Time to first inference after cold pod start.** Two variants:
   - Warm-PVC (pod restart, weights survive). `kubectl -n lab-ollama-qwen-moe delete pod ollama-0`; measure pod ready + first `/api/generate` reply.
   - Cold-PVC (PVC recreated). `make w1.1-ollama-down && make w1.1-ollama-up`; then run the timed pull above.
2. **Origin egress per pod start.** Read the final `total` byte count from
   the pull NDJSON (`tail -1 /tmp/ollama-pull.ndjson | jq .total`); for
   `qwen3:30b-a3b` Q4_K_M this is ~18.6 GB. To verify on the wire, run on
   `spark-3d37` while the pull is in flight:
   `sudo tcpdump -i any -w /tmp/ollama-pull.pcap host registry.ollama.ai`,
   then `capinfos -b /tmp/ollama-pull.pcap`.
3. **Cold start after node reboot (PVC survives).** Reboot
   `spark-3d37`; measure pod ready time after node returns; should be
   << first-pull time.
4. **Disk footprint.**
   ```sh
   kubectl debug node/spark-3d37 -it --image=alpine \
     -- du -sh /host/opt/local-path-provisioner
   ```
   (or read the pulled byte total from the NDJSON).

## Plan deviations

None. The W1.1 deployment matches the roadmap intent exactly: GGUF
Q4_K_M Qwen MoE on `spark-3d37`, env-var-configured, local-path PVC,
cluster-internal Service. The only Glossary-level note is that the model
generation actually shipped is Qwen 3 30B-A3B (not the roadmap's
prospective 3.5 35B-A3B); see [GLOSSARY.md](../../GLOSSARY.md).

## GB200 / GB300 carry-over

Per [docs/wave-1/transfer-review.md](../../docs/wave-1/transfer-review.md):
mostly transplants. Drop the `dgx-spark-gb10` `nodeSelector` for the
GB200 hardware-class label; size the PVC up if running multi-model
(Q4_K_M is 18.6 GB; FP16 is 60+ GB). Re-test the cold-load + first-token
latency on GB200 and append a row to [JOURNAL.md](../../JOURNAL.md).

## Known limitations

- **Single replica, single GPU.** No HA. Replacing the node loses the PVC.
- **No public ingress, no auth.** Cluster-internal only. W1.4 introduced
  the shared public-ingress + auth proxy pattern (see [../ingress/](../ingress/)).
- **No automated model pull.** The first pull is operator-driven so we get
  clean wall-clock numbers for W1.3. After the first pull the weights persist
  in the PVC and pod restarts are fast.
