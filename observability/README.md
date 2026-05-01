# W1.5 — Observability foundation

Wave item: **W1.5** (see [ROADMAP.md](../ROADMAP.md)).

> *Roadmap W1.5 deliverable: "Prometheus + Grafana on AKS, DCGM exporter
> on each Spark, node-exporter on each node, initial Grafana dashboard.
> Every later wave's measurements go through this stack."*

## What this proves

- Standard kube-prometheus-stack runs on AKS unchanged; DCGM on the
  Sparks gives per-pod GPU attribution without manual joins.
- The metric pipeline is the same one every later wave's measurements
  feed; W1.6/W1.7 bench numbers, the W1.3 page-cache plot, future
  per-engine dashboards all land in the same Grafana.
- Multi-arch DaemonSet pinning works (node-exporter on every node;
  DCGM only on `dgx-spark-gb10`-labeled nodes).

## What's deployed

| Component | How | Where it runs |
|---|---|---|
| Prometheus + Alertmanager + Operator | `prometheus-community/kube-prometheus-stack` (Helm) | AKS system pool (amd64) |
| kube-state-metrics | bundled with kube-prometheus-stack | AKS system pool |
| node-exporter | bundled with kube-prometheus-stack DaemonSet | **Every** node — AKS pools *and* both Sparks (multi-arch image) |
| Grafana | bundled with kube-prometheus-stack | AKS system pool |
| DCGM exporter | `nvidia/dcgm-exporter` (Helm) | **Sparks only** (`nodeSelector: lab.unbounded.cloud/hardware-class=dgx-spark-gb10`) |
| Initial dashboard | ConfigMap (`unbounded-lab-overview`) | Auto-loaded by Grafana sidecar |

All workloads run as Deployments/DaemonSets in `lab-observability`. Per
the roadmap, **Grafana stays cluster-internal** (port-forward to reach
it); a public Grafana with shared auth would expand the W1.4 auth
surface and is deferred.

## Files

| File | Role |
|---|---|
| [`namespace.yaml`](namespace.yaml) | `lab-observability` namespace |
| [`values-kube-prometheus-stack.yaml`](values-kube-prometheus-stack.yaml) | Helm values for the kube-prometheus-stack chart |
| [`values-dcgm-exporter.yaml`](values-dcgm-exporter.yaml) | Helm values for the NVIDIA dcgm-exporter chart |
| [`dashboards/unbounded-lab-overview.yaml`](dashboards/unbounded-lab-overview.yaml) | Initial Grafana dashboard ConfigMap |

Chart versions are pinned in the top-level [Makefile](../Makefile)
(`KPS_CHART_VERSION`, `DCGM_CHART_VERSION`) so re-deploys are
reproducible. Bump them with one targeted PR.

## Deploy, status, teardown

```sh
make w1.5-up           # idempotent
make w1.5-status       # pods, daemonsets
make w1.5-down         # uninstall both Helm releases + namespace
```

Equivalent manual flow:

```sh
kubectl apply -f observability/namespace.yaml
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add nvidia https://nvidia.github.io/dcgm-exporter/helm-charts
helm repo update

helm upgrade --install lab-obs prometheus-community/kube-prometheus-stack \
  -n lab-observability --version 84.4.0 \
  -f observability/values-kube-prometheus-stack.yaml

helm upgrade --install lab-obs-dcgm nvidia/dcgm-exporter \
  -n lab-observability --version 4.8.1 \
  -f observability/values-dcgm-exporter.yaml

kubectl apply -f observability/dashboards/
```

## API access

Grafana is cluster-internal; reach it via port-forward:

```sh
make w1.5-grafana-pwd        # prints the auto-generated admin password
make w1.5-grafana            # port-forwards :3000 -> svc/lab-obs-grafana:80
# open http://localhost:3000 — admin / <pwd>
```

The "Unbounded Lab — Overview" dashboard is auto-loaded; it covers:

- **Spark GPU**: utilization %, framebuffer used (GiB), power (W),
  temperature (°C) per GB10. Driven by DCGM. The W1.2 unreclaimable
  buff/cache pain is plotted directly so the reboot follow-up can be
  measured visually.
- **Cluster**: nodes ready, pods running/failed in `lab-*` namespaces,
  container restarts in the last 24 h.
- **Spark host**: memory used %, buff/cache GiB. PVC usage across all
  `lab-*` namespaces (including Ollama and vLLM weights).

### What Prometheus scrapes today

- node-exporter on every node (AKS + Spark) — host-level metrics.
- kube-state-metrics — pod / deployment / PVC / node state.
- DCGM exporter on each Spark — GPU metrics with **pod/container labels**
  (e.g. `pod=vllm-0`, `container=vllm`, `namespace=lab-vllm-qwen-moe`),
  so the dashboard can attribute GPU draw to specific lab workloads
  without manual joins.
- Prometheus itself, the operator, alertmanager, kubelet, kube-apiserver
  (the standard kube-prometheus-stack scrape set).

### What it does NOT scrape yet

- **Inference engine application metrics.** vLLM exposes `/metrics` natively
  (Prometheus format) but we have not added a `ServiceMonitor` for it yet.
  Ollama does not expose Prometheus metrics; we'd need a sidecar exporter
  if request-rate is needed.
- **W1.6 benchmark results.** The benchmark harness emits a JSON results
  file; if we want time-series of repeated runs, future-W1.6 work can push
  via Pushgateway. Not in scope today.
- **Open WebUI metrics.** Not exposed by upstream.

The dashboard is an *operational* view (is everything alive, are GPUs busy,
are PVCs filling up). Application-level latency / throughput dashboards
land with W1.6 + an inference ServiceMonitor in a follow-up.

## Pain runbook

N/A directly — observability is the *substrate* every other component's
pain runbook reads from. The two known pain interactions are documented
on the components that surface them:

- DCGM-on-Tegra silently omits `DCGM_FI_DEV_FB_USED`; the bench harness
  uses `DCGM_FI_DEV_POWER_USAGE` peak instead. See
  [bench/README.md](../bench/README.md) and
  [docs/wave-1/transfer-review.md](../docs/wave-1/transfer-review.md).
- The W1.2 page-cache pinning shows up directly on the "Spark host" panel;
  the reboot procedure is [docs/runbooks/spark-reboot.md](../docs/runbooks/spark-reboot.md).

## Plan deviations

None. The roadmap calls for kube-prometheus-stack + DCGM + node-exporter
+ initial Grafana dashboard; that's exactly what shipped. Two deferred
items are explicitly Wave 2:

- ServiceMonitor for vLLM `/metrics` (deferred to W2.x application-metrics
  dashboard work).
- Public Grafana with shared auth (deferred — would expand the W1.4 auth
  surface).

## GB200 / GB300 carry-over

Per [docs/wave-1/transfer-review.md](../docs/wave-1/transfer-review.md):
mostly transplants.

- kube-prometheus-stack: transplants unchanged.
- node-exporter: transplants unchanged.
- DCGM exporter: transplants unchanged. Same NVML/CDI path on Grace+Blackwell.
- Dashboard: transplants unchanged. `DCGM_FI_DEV_*` field names are stable
  across DCGM versions and across Hopper/Blackwell/Grace generations.

The only thing that changes is the dashboard reading "1 GPU per node"
because GB200/GB300 nodes have multiple GPUs each. The metric names
already include `gpu="0"` etc. so no PromQL changes are needed; just add
panels that aggregate across `gpu` labels per host. DCGM on GB200 also
exports `DCGM_FI_DEV_FB_USED` natively (Spark Tegra silently omits it),
so the bench harness can revert its peak-FB query.

## Known gotchas

- **DCGM exporter memory limit.** The default 256 MiB OOMs on first sample
  (Field collection allocates GiBs of buffers). Values file requests 512
  MiB and limits at 2 GiB.
- **DCGM exporter does not advertise `nvidia.com/gpu`.** It uses the host's
  NVML socket and `/var/lib/kubelet/pod-resources` for pod-attribution
  joins; it does *not* take a GPU slot from the device plugin. That's why
  W1.1 Ollama and W1.2 vLLM still get their GPU even with DCGM running on
  the same node.
- **Grafana sidecar dashboard pickup is namespace-wide** because we set
  `searchNamespace: ALL`. Future waves can drop a ConfigMap labeled
  `grafana_dashboard: "1"` in *any* namespace and Grafana will load it.
  Convention: put per-wave dashboards in the corresponding lab namespace
  (e.g. `lab-vllm-qwen-moe/vllm-dashboard`).
