# Architecture

Wave-agnostic view of how the lab is wired together. For the snapshot at
the close of Wave 1 (with the mermaid topology diagram), see
[docs/wave-1/architecture.md](docs/wave-1/architecture.md). For what's
actually running right now, see [STATE.md](STATE.md). For the strategic
plan and future waves, see [ROADMAP.md](ROADMAP.md).

## Hard rules

1. **`deploy/` vs. `inference/` etc.** — `deploy/` in
   [unbounded-kube](https://github.com/microsoft/unbounded-kube) is for
   unbounded-kube *component* manifests (machina, net, inventory) rendered
   via `make *-manifests`. This repo is for *AI workloads* running on top
   of unbounded-kube. The two never mix.
2. **Per-item conventions.** Each `<area>/<item>/` directory contains a
   `README.md` (what it deploys, what it proves, deploy + teardown), plain
   YAML manifests or a `kustomization.yaml`. Model license + provenance
   info goes in the README intro for any item shipping or running model
   weights.
3. **Canonical names.** Anywhere a model, namespace, label, or region is
   named, it must match an entry in [GLOSSARY.md](GLOSSARY.md). Glossary
   wins over any other doc.
4. **No third-party object stores.** No S3 / GCS / R2. Azure Blob / ACR
   only — the Microsoft-aligned narrative breaks if a slide shows AWS in
   the architecture diagram.

## Spine

One AKS control plane (Canada Central) plus DGX Spark edge nodes joined
via unbounded-agent + unbounded-net (WireGuard). Sparks live behind NAT;
the WireGuard tunnel is what makes them appear as ordinary kubelets to
the AKS API server.

| Pool / class                                | Arch  | Role                                                |
|---------------------------------------------|-------|-----------------------------------------------------|
| AKS `system`                                | amd64 | cert-manager, kube-prometheus-stack control loop    |
| AKS `gwmain`                                | amd64 | ingress-nginx, Open WebUI, bench Jobs, gateway pods |
| Edge: DGX Spark GB10 (Region A; B, C later) | arm64 | inference engines, dcgm-exporter, training/RAG/etc. |

## Region & node label conventions

Every Spark gets these labels at onboarding (idempotent; current setter is
[foundation/label-region-a.sh](foundation/label-region-a.sh)):

```
lab.unbounded.cloud/region=<a|b|c>
lab.unbounded.cloud/hardware-class=<dgx-spark-gb10|gb200|gb300>
```

Manifests use these in `nodeSelector` and `kubectl get nodes -L`. New
hardware classes are added to [GLOSSARY.md](GLOSSARY.md) when they land.

## Public hostname (`LAB_HOST`)

Public ingresses read their hostname from a per-namespace
`configMapGenerator` in `kustomization.yaml`. Committed defaults are
placeholders (`ollama.lab.example.com`, `chat.lab.example.com`); the
repo never carries an environment-specific name.

Override on the command line, never commit the literal:

```sh
make LAB_HOST=mychat.example.com w1.1-up
```

The Make target backs up the affected `kustomization.yaml`, runs `sed`
over the `host=` literal, applies, and restores the file (even on
Ctrl-C). Without `LAB_HOST` set, targets print a warning and apply with
the placeholder — fine for local kustomize dry-runs, will not produce a
working public endpoint.

## First-party Microsoft surfaces

The Microsoft alignment is a funded mandate: make Azure visible and
load-bearing throughout, not incidental. Status is tracked here and in
[ROADMAP.md](ROADMAP.md) §First-Party Microsoft Story.

| Layer                | Service                                  | Status today  |
|----------------------|------------------------------------------|---------------|
| Control plane        | AKS                                      | live          |
| Networking           | Azure CNI / Azure Load Balancer          | live          |
| DNS / TLS            | Azure DNS + cert-manager (ACME)          | live          |
| Container registry   | Azure Container Registry (ACR)           | partial — Wave 2 finishes the mirror |
| Object storage       | Azure Blob                               | planned — Wave 2 |
| Workload identity    | Azure Workload Identity                  | planned — Wave 2 |
| Monitoring           | Azure Monitor + Container Insights       | planned — augments W1.5 |
| Edge join            | unbounded-agent + unbounded-net          | live          |
| Geo routing          | Azure Front Door / Traffic Manager       | planned — Wave 5 |
| DR replication       | Azure Blob geo-replication               | planned — Wave 5 |

**Things to avoid:** pulling weights from Hugging Face Hub directly in a
production pod (mirror to ACR / Azure Blob); rolling our own auth where
Azure AD / Workload Identity fits; quoting Spark perf numbers as
predictive of GB200 perf.

## Persistence

| Where        | Volume class    | Backed by                    | Used for                                  |
|--------------|-----------------|------------------------------|-------------------------------------------|
| Spark nodes  | `local-path`    | XFS on local NVMe            | Inference engine model caches             |
| AKS pools    | `default` / `managed-csi` | Azure Disk         | Open WebUI sqlite, Prometheus TSDB, Grafana, bench results |

Local-path PVCs pin weights to one node by design (no cross-node
rebalancing). The pain this surfaces — re-pulls on PVC loss, no
cross-namespace dedup, thundering-herd on multi-node startup — is logged
in [JOURNAL.md](JOURNAL.md). That pain is the data foundation for the
future Unbounded Storage product; this lab does **not** deploy
Alluxio/Fluid/etc. to hide it.

## Ingress + auth pattern

One public hostname (`vapa-ollama.canadacentral.cloudapp.azure.com` on
the live cluster), four exposed paths:

| Path                       | Backend                                                 | Auth                  |
|----------------------------|---------------------------------------------------------|-----------------------|
| `/`                        | Open WebUI (`lab-openwebui`)                            | Open WebUI's own login |
| `/lab-api/ollama/`         | Ollama native API in `lab-ollama-qwen-moe`              | basic-auth (`lab-api-basic-auth`) |
| `/lab-api/vllm/`           | vLLM native OpenAI `/v1` in `lab-vllm-qwen-moe`         | basic-auth            |
| `/lab-api/vllm-ollama/`    | vLLM via Ollama-shim sidecar (`/api/...`) in same ns    | basic-auth            |

ingress-nginx + cert-manager `letsencrypt-prod`. Basic-auth is the
tactical choice today; AAD / Workload Identity replaces it before any
cross-region endpoint or external sharing (Wave 5 hard gate). New
inference engines (e.g. SGLang at W2.4) join this pattern by adding one
ingress object and one path prefix; nothing else changes.

## Observability

kube-prometheus-stack on the AKS system pool, dcgm-exporter as a
DaemonSet pinned to GPU nodes, node-exporter on every node. Grafana sits
in the same namespace (`lab-observability`). Prometheus scrapes vLLM
`/metrics` directly; dashboards live as ConfigMaps in
[observability/dashboards/](observability/dashboards/). DCGM on Spark
Tegra silently omits `DCGM_FI_DEV_FB_USED`; the bench harness uses
`DCGM_FI_DEV_POWER_USAGE` peak as the proxy "GPU was busy" signal until
we get to GB200, where FB comes back.

## Code organization

```
inference/<engine>-<model-shortname>/   # one inference workload per dir
training/<thing>/                       # training jobs (W2+)
models/<model>/                         # model-specific artifacts (W2+)
rag/                                    # embedding + vector store + LLM (W2+)
observability/                          # Prometheus + Grafana + DCGM
bench/                                  # repeatable benchmark harness
foundation/                             # one-shot bootstrap scripts (labels, etc.)
docs/runbooks/                          # operational playbooks (e.g. spark-reboot)
docs/wave-<N>/                          # frozen per-wave write-ups
sponsor-updates/                        # YYYY-MM.md monthly executive notes
```

Each `<area>/<item>/` is a self-contained kustomize bundle plus a README.
See the entry-point [README.md](README.md) for the current layout table.

## GB200 / GB300 transplant

Every wave closes with a transfer-review against the rubric in
[ROADMAP.md](ROADMAP.md) §GB200/GB300 Transfer Plan. Wave 1's review is
[docs/wave-1/transfer-review.md](docs/wave-1/transfer-review.md). The
generic carry-overs (node labels as join keys, `storageClassName` swap,
ingress hostname audit, image-registry mirror to ACR) apply to every
wave's items.
