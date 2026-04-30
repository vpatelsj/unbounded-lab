# Spark reboot runbook

When `node_memory_MemAvailable_bytes` on a Spark drops below ~5\u202fGiB and
the GPU buffer page cache (`Buffers + Cached`) is pinned around 65\u202fGiB,
inference latency spikes and KV pool capacity collapses (see
[`../storage-pain-journal.md`](../storage-pain-journal.md)). The only known
fix today is a full reboot of the Spark node. This doc is the procedure.

## Pre-checks

```bash
NODE=spark-2c24      # or spark-3d37
kubectl get node "$NODE" -o wide
kubectl top node "$NODE"
# Capture current page-cache pressure for the journal:
kubectl exec -n lab-observability ds/lab-obs-dcgm-dcgm-exporter -- \
  cat /proc/meminfo | grep -E "MemAvailable|Buffers|Cached|Slab" || true
```

Open Grafana \u2192 "Spark host memory" panel and screenshot the
`MemAvailable` + `Buffers+Cached` time series for the journal "before" row.

## Drain

Inference workloads on Sparks are stateful (vLLM, Ollama) but have local
PVCs that survive the reboot, so a careful drain works.

```bash
kubectl cordon "$NODE"

# Delete the inference pod(s) on this node so they don't fight us.
# vLLM lives on spark-2c24; Ollama on spark-3d37.
case "$NODE" in
  spark-2c24) kubectl -n lab-vllm    delete pod vllm-0    --wait=true ;;
  spark-3d37) kubectl -n lab-ollama  delete pod ollama-0  --wait=true ;;
esac

# DCGM exporter is fine to evict; it'll come back as part of the DS.
kubectl drain "$NODE" \
  --delete-emptydir-data \
  --ignore-daemonsets \
  --force \
  --timeout=5m
```

## Reboot

The Spark is bare metal under DGX OS 6, joined as an arc-enabled AKS
node. Two options, in order of preference:

1. **SSH + `sudo systemctl reboot`** if the node is reachable.
2. **DGX OS console** (BMC / monitor) if SSH is unreachable due to the
   memory pressure itself.

```bash
# Option 1
ssh "$NODE" sudo systemctl reboot
```

Expect the node to come back in 3\u20135 minutes.

## Restore

```bash
# Wait for the kubelet to re-register
kubectl wait --for=condition=Ready node/"$NODE" --timeout=10m

# Uncordon
kubectl uncordon "$NODE"

# Re-deploy / let StatefulSet rescheduler pick the node
case "$NODE" in
  spark-2c24)
    kubectl -n lab-vllm rollout status sts/vllm --timeout=10m
    ;;
  spark-3d37)
    kubectl -n lab-ollama rollout status sts/ollama --timeout=10m
    ;;
esac

# Smoke test
kubectl -n lab-vllm   port-forward svc/vllm   8000:8000 &  # one or the other
curl -s localhost:8000/v1/models | jq .
```

## Post-checks (record in journal)

After ~10 minutes of warm traffic:

```bash
kubectl top node "$NODE"
# Re-capture meminfo
ssh "$NODE" cat /proc/meminfo | grep -E "MemAvailable|Buffers|Cached|Slab"
```

Open the same Grafana panel; screenshot the "after" series. Append a row
to [`../storage-pain-journal.md`](../storage-pain-journal.md):

```
| YYYY-MM-DD | spark-2c24 | reboot | MemAvailable XX GiB \u2192 YY GiB | Buffers+Cached ZZ GiB \u2192 WW GiB | KV pool A \u2192 B GiB |
```

## Notes

- Never reboot both Sparks at once; you lose all inference capacity.
- The local PVCs survive (XFS on local-path), so vLLM/Ollama warm-load
  from the same on-disk weights and don't re-pull from HF.
- If `MemAvailable` recovers but `Buffers+Cached` immediately reflates
  to 65\u202fGiB on first inference request, that's the kernel page-cache
  pinning behavior under unified memory \u2014 file an upstream issue and
  add a row to the journal anyway.
