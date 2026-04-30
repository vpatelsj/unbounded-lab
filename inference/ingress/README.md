# W1.4 — Shared ingress + auth pattern for engine APIs

Wave item: **W1.4** (see [`../../plan.md`](../../plan.md)).

> *Plan W1.4 deliverable: "Short writeup showing how both engines (Ollama,
> vLLM) today sit behind the same ingress, cert, and auth proxy. SGLang
> joins this pattern at W2.4 — this is the demo story for 'standard k8s
> patterns apply to AI'."*

This page is that writeup. The manifests below are the implementation.

## What's actually exposed

| URL | Backend | Auth |
|---|---|---|
| `https://<host>/` | Open WebUI (`lab-openwebui/open-webui:8080`) | Open WebUI session login |
| `https://<host>/lab-api/ollama/*` | Ollama API (`lab-ollama-qwen-moe/ollama:11434`) | Basic auth |
| `https://<host>/lab-api/vllm/*` | vLLM native OpenAI API (`lab-vllm-qwen-moe/vllm:8000`) | Basic auth |
| `https://<host>/lab-api/vllm-ollama/*` | vLLM via the Ollama-compat shim sidecar (`lab-vllm-qwen-moe/vllm:11434`) | Basic auth |

`<host>` defaults to `vapa-ollama.canadacentral.cloudapp.azure.com` — the
same hostname Open WebUI already uses. **One DNS record, one ACME cert,
one auth secret per engine namespace, two engines.**

## Why share the hostname instead of a second FQDN

- The cert is issued by cert-manager via the same `letsencrypt-prod`
  ClusterIssuer the chat UI already uses; reusing the cert avoids a second
  ACME challenge and a second Azure public-IP DNS A record.
- nginx-ingress merges multiple `Ingress` resources that share a host, so
  the chat UI's `/` rule and the engine API path-prefix rules coexist
  cleanly. The cert is taken from whichever `Ingress` declares it (today
  that's `lab-openwebui/open-webui`); the API ingresses here intentionally
  omit a `tls:` block.
- W2.4 (SGLang) joins by adding one more `Ingress` with
  `path: /lab-api/sglang(/|$)(.*)` in `lab-sglang-*`. No new DNS, no new
  cert. That **is** the "standard k8s patterns apply to AI" demo story.

## Why basic auth and not OAuth/Entra ID

Two reasons, both explicit choices:

1. **Scope.** W1.4 is a one-page deliverable. Basic-auth via
   `nginx.ingress.kubernetes.io/auth-type: basic` is ~15 lines per
   `Ingress` and zero extra deployments. OAuth would add an
   `oauth2-proxy` Deployment, an Entra ID app registration, secrets, and
   a session cookie story. That's more first-party-Microsoft (per
   `plan.md` §First-Party Microsoft Story) but is properly Wave 2 work.
2. **API consumers.** The W1.6 benchmark harness and the W2.3 eval
   pipeline are headless clients hitting `/v1/chat/completions`. Basic
   auth is one HTTP header (`Authorization: Basic ...`). OAuth would
   need a token-fetch flow inside every Job. Basic auth is right for
   this audience.

The browser-facing chat UI keeps **its own** session login at `/`. Two
different auth surfaces is correct: the API audience is machines, the UI
audience is people, and they use different patterns.

## File layout

| File | Role |
|---|---|
| [`namespace.yaml`](namespace.yaml) | `lab-ingress` (holds the host ConfigMap; ingresses themselves live in their engine namespaces because nginx-ingress requires the `Ingress` and the auth Secret to share a namespace) |
| [`ingress-ollama.yaml`](ingress-ollama.yaml) | `Ingress` in `lab-ollama-qwen-moe`, mounts Ollama at `/lab-api/ollama/*` |
| [`ingress-vllm.yaml`](ingress-vllm.yaml) | `Ingress` in `lab-vllm-qwen-moe`, mounts vLLM at `/lab-api/vllm/*` and the Ollama-shim at `/lab-api/vllm-ollama/*` |
| [`kustomization.yaml`](kustomization.yaml) | Bundles the above and stamps the public hostname into both Ingresses' rules |
| [`make-htpasswd.sh`](make-htpasswd.sh) | Generates the per-namespace basic-auth Secrets (`auth-<ns>.local.yaml`, gitignored) |

## Deploy

Provision credentials (one-time; the Secret is replicated to both
namespaces because nginx-ingress requires the auth Secret to live in the
same namespace as the `Ingress`):

```sh
inference/ingress/make-htpasswd.sh ops "$(openssl rand -base64 24)"
kubectl apply -f inference/ingress/auth-lab-ollama-qwen-moe.local.yaml
kubectl apply -f inference/ingress/auth-lab-vllm-qwen-moe.local.yaml
```

Apply the ingresses:

```sh
make w1.4-up
# = kubectl apply -k inference/ingress
```

## Smoke test

```sh
HOST=vapa-ollama.canadacentral.cloudapp.azure.com
USER=ops
PASS=...   # whatever you passed to make-htpasswd.sh

# Auth required (401 without creds)
curl -sI https://$HOST/lab-api/ollama/api/version | head -1
# -> HTTP/2 401

# Authenticated
curl -sS -u $USER:$PASS https://$HOST/lab-api/ollama/api/version
curl -sS -u $USER:$PASS https://$HOST/lab-api/ollama/api/tags
curl -sS -u $USER:$PASS https://$HOST/lab-api/vllm/v1/models
curl -sS -u $USER:$PASS https://$HOST/lab-api/vllm-ollama/api/tags

# End-to-end generation
curl -sS -u $USER:$PASS https://$HOST/lab-api/vllm/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"Qwen/Qwen3-30B-A3B-GPTQ-Int4",
       "messages":[{"role":"user","content":"hello"}],
       "max_tokens":20}'
```

## Path-rewrite behavior

The path regex `/lab-api/<engine>(/|$)(.*)` captures everything after the
prefix into `$2`, and the annotation
`nginx.ingress.kubernetes.io/rewrite-target: /$2` strips the
`/lab-api/<engine>` prefix before forwarding upstream. So a request for
`https://<host>/lab-api/vllm/v1/models` reaches the vLLM container as a
`GET /v1/models`. This keeps the engine code unaware of the public path
prefix — same upstream code path for in-cluster, port-forwarded, and
public callers.

## Why a path prefix and not subdomains

Subdomains (`ollama.<host>`, `vllm.<host>`) would each need their own
ACME cert and either wildcard DNS or per-engine A records. Path prefixes
are simpler, share infrastructure, and keep the "uniform pattern" the
plan asks for: the *only* thing that varies between engines is one path
segment.

## Status

```sh
make w1.4-status
```

## Teardown

```sh
make w1.4-down
# Note: the basic-auth Secrets (auth-lab-*-qwen-moe.local.yaml) are not
# tracked by kustomize; delete them manually if you want to revoke creds.
```

## What changes when SGLang lands at W2.4

A single new file, `inference/ingress/ingress-sglang.yaml`, in the SGLang
namespace, with `/lab-api/sglang(/|$)(.*)` and the same auth annotations.
Same auth Secret pattern, same hostname, same cert, same nginx
`ingressClassName`. That's the W1.4 → W2.4 portability story.

## GB200/GB300 transfer

This whole pattern transplants unchanged. nginx-ingress, cert-manager,
and the basic-auth annotation are kubernetes-native and hardware-agnostic.
The only Spark-specific knob anywhere in the bundle is *zero*; this is one
of the cleanest items on the transfer-review checklist.
