# W1.1 — Open WebUI customer-facing chat UI

Wave item: **W1.1** (see [ROADMAP.md](../../ROADMAP.md)).

Deploys [Open WebUI](https://github.com/open-webui/open-webui) (BSD-3) at
the root path of the lab's public hostname. It talks to the in-cluster
Ollama service over the cluster network, so the chat UI never round-trips
through a public ingress on the way to the model.

Open WebUI is the only public ingress in W1.1. The Ollama backend itself
has no public ingress; Open WebUI proxies it server-side.

## What this proves

- A customer can hit a single URL, sign up, and chat with the
  Spark-resident model.
- An off-the-shelf chat front end runs on the AKS system pool while the
  model engine runs on a Region-A DGX Spark, talking to each other across
  unbounded-net via cluster-internal DNS (no public hop on the model path).
- A non-GPU workload comfortably co-exists on the AKS pool while Sparks
  are reserved for inference engines.

## Files

| File | Role |
|---|---|
| `namespace.yaml` | `lab-openwebui` namespace |
| `pvc.yaml` | Azure Disk PVC (`default` SC), 10 GiB, holds chat history + embedding cache |
| `service.yaml` | ClusterIP `open-webui` :8080 |
| `deployment.yaml` | Single-replica Deployment, pinned to AKS amd64 system pool |
| `ingress.yaml` | nginx Ingress at host root, cert-manager TLS |
| `make-secrets.sh` | Generates `secret.local.yaml` with a fresh `WEBUI_SECRET_KEY`. Re-run to rotate sessions. |
| `secret.local.yaml` | Generated; gitignored. |
| `kustomization.yaml` | Bundles everything. |

## Deploy, status, teardown

```sh
make LAB_HOST=mychat.example.com w1.1-openwebui-up    # idempotent
make w1.1-openwebui-status
make w1.1-openwebui-down                               # deletes namespace AND chat-history PVC; signup state is lost
```

`LAB_HOST` overrides the `open-webui-host` configMap on the fly (the
target backs up and restores `kustomization.yaml`, even on Ctrl-C);
without it, the placeholder `chat.lab.example.com` is used and the
public endpoint will not work. Generates `secret.local.yaml` on first
run. Re-running does **not** rotate the session key; delete
`secret.local.yaml` and re-run to rotate (invalidates all logged-in
sessions).

### Hostname and routing

The public hostname is operator-supplied via the `configMapGenerator`
(default placeholder: `chat.lab.example.com`). Open WebUI is the only
public ingress in W1.1; it proxies the Ollama API server-side over the
cluster network, so customers only need this one URL.

### Why pinned to AKS, not a Spark

The PVC uses Azure Disk (RWO, attaches only to AKS-managed VMs). Open
WebUI is a light non-GPU workload, so we keep it on the cheap AKS system
pool and leave the Sparks for inference engines. Cross-node Service
traffic to the Spark-resident Ollama pod traverses unbounded-net.

## API access

Browser only — Open WebUI exposes a chat UI, not an API. First use:

1. Browse to your configured chat hostname (default placeholder:
   `https://chat.lab.example.com/`).
2. Sign up. **The first signup becomes admin.**
3. Subsequent signups land in `pending`. The admin approves them under
   `Admin Panel → Users`.
4. The model picker should already show `qwen3:30b-a3b`. Open WebUI
   auto-discovers it via
   `OLLAMA_BASE_URL=http://ollama.lab-ollama-qwen-moe.svc.cluster.local:11434`.

## Pain runbook

N/A — Open WebUI is a chat UI, not a measurement target. Ingestion /
RAG behavior may be measured in a future wave; for now, all storage-pain
rows attributed to W1.1 capture Ollama-side metrics. See
[../ollama-qwen-moe/README.md](../ollama-qwen-moe/README.md) §Pain runbook.

## Plan deviations

None at deploy time. The roadmap-flagged "future hooks" (W1.2 second
engine, W1.4 shared auth) have both shipped:

- **W1.2 — vLLM as a second engine.** Open WebUI's model picker now
  offers both engines side-by-side for the same Qwen3 30B-A3B weights
  (Ollama Q4_K_M and vLLM-via-shim).
- **W1.4 — shared auth on API paths.** Replaced per-engine ad-hoc auth
  with the basic-auth secret (`lab-api-basic-auth`) on `/lab-api/...`.
  Open WebUI's own login flow still fronts the chat UI at `/`; OIDC /
  OAuth2 is the next step before any cross-region endpoint (Wave 5).

## GB200 / GB300 carry-over

Per [docs/wave-1/transfer-review.md](../../docs/wave-1/transfer-review.md):
transplants as-is. Re-issue cert if the public hostname changes; consider
swapping SQLite → Postgres at multi-tenant scale. Re-test E2E chat
through the public ingress.

## Known limitations

- **Single replica.** Recreate strategy means a brief outage during rollouts.
- **No external auth on the chat UI.** Anyone can hit the signup page
  until you disable signups (set `ENABLE_SIGNUP=false` after the admin
  and approved users are created). W1.4 only shielded the API paths;
  the chat UI keeps Open WebUI's own login by design (different audience).
- **Admission policy is manual.** `DEFAULT_USER_ROLE=pending` blocks new
  signups from chatting until the admin approves them. There is no email
  notification configured.
