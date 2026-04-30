# Wave 1 transfer review

Per `plan.md` \u00a7"Per-wave transfer review checklist": for every shipped
item, what would a Wave-2 GB200/GB300 transplant cost? Filled in at Wave 1
close.

| Item   | Transplants as-is? | Changes needed                                                                                     | Re-test required                                    |
|--------|--------------------|----------------------------------------------------------------------------------------------------|------------------------------------------------------|
| W1.1 Ollama serving Qwen MoE        | Mostly | Drop `nodeSelector: dgx-spark-gb10` \u2192 use a GB200 node label. Bigger PVC if multi-model (Q4_K_M is 18.6 GB; FP16 is 60+). | Cold-load + first-token latency; storage-pain row.   |
| W1.1 Open WebUI                     | Yes    | None code-side. Re-issue cert if hostname changes. Sqlite \u2192 Postgres at multi-tenant scale.       | E2E chat through public ingress.                     |
| W1.2 vLLM serving Qwen MoE          | Partly | Drop GB10 MoE tuning ConfigMap; raise `--gpu-memory-utilization` (GB200 has 192\u202fGB HBM, no host page-cache contention). Pick a fresh quant (FP8 viable on GB200). | Full bench sweep (W1.6) + KV pool size + p50/p95.    |
| W1.3 Storage pain journal           | N/A    | Re-measure on GB200: page cache pressure should largely disappear with HBM. Keep journal format. | New rows comparing HBM vs Spark unified memory.      |
| W1.4 Ingress + TLS                  | Yes    | None. ClusterIssuer + ingress class are portable. Front Door (Wave 3) will sit in front. | Cert renewal + E2E HTTPS smoke.                      |
| W1.5 Observability                  | Mostly | dcgm-exporter on GB200 actually exports `DCGM_FI_DEV_FB_USED` (Spark Tegra silently omits it). Bench harness can revert to FB query. | Confirm metric set via `/metrics` curl + dashboards. |
| W1.6 Bench harness                  | Yes    | After GB200 dcgm reports FB, flip query back to `DCGM_FI_DEV_FB_USED` and rename JSON field. | Full sweep at GB200 concurrency frontier.            |

## Generic carry-overs

- **Node labels**: `lab.unbounded.cloud/hardware-class` and `region` are
  the join keys. Any GB200/GB300 wave needs the same labels applied
  before any of these manifests will schedule.
- **PVC class**: Spark uses `local-path` (one node = one volume, no
  rebalancing). GB200 in AKS will use `managed-csi` or premium SSD;
  the `storageClassName` field is the only knob.
- **Ingress hostname**: hardcoded today. Wave 3 lifts to Front Door;
  audit every README + manifest for hostname strings then.
- **Image registry**: docker.io / ghcr.io / nvcr.io today. Wave 2
  mirrors to ACR \u2014 update image refs in one pass.
