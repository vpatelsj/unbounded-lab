# unbounded-lab Makefile (extracted from unbounded-kube)
#
# Public hostname for the W1.1 Open WebUI ingress (the customer-facing URL).
# Ollama itself is cluster-internal (no public ingress); Open WebUI proxies it.
# Override on the command line, do not commit:
#     make LAB_HOST=mychat.example.com w1.1-up
# When unset, the placeholder default from openwebui/kustomization.yaml is
# used, which will not produce a working public endpoint.
LAB_HOST ?=

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_.-]+:.*##/ { printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# Helper: if LAB_HOST is set, edit the named configMap host literal in the
# given kustomize dir, run the wrapped command, then restore the file.
# Uses sed (kustomize CLI is not a hard dependency).
# Args: $(1)=kustomize dir, $(2)=configmap name (informational), $(3)=command to run
define _with_host
	@set -eu; \
	dir='$(1)'; cmd='$(3)'; \
	if [ -n '$(LAB_HOST)' ]; then \
		cp "$$dir/kustomization.yaml" "$$dir/kustomization.yaml.bak"; \
		trap 'mv "$$dir/kustomization.yaml.bak" "$$dir/kustomization.yaml"' EXIT INT TERM; \
		sed -i -E "s|^([[:space:]]*-[[:space:]]*host=).*|\1$(LAB_HOST)|" "$$dir/kustomization.yaml"; \
		eval "$$cmd"; \
	else \
		echo "WARNING: LAB_HOST is not set; using kustomization placeholder host. Public ingress will not work."; \
		eval "$$cmd"; \
	fi
endef

.PHONY: help \
        w1.1-up w1.1-down w1.1-status \
        w1.1-ollama-up w1.1-ollama-down w1.1-ollama-status \
        w1.1-openwebui-up w1.1-openwebui-down w1.1-openwebui-status \
        w1.2-up w1.2-down w1.2-status \
        w1.2-vllm-up w1.2-vllm-down w1.2-vllm-status

# Aggregate W1.1 = Ollama engine + Open WebUI customer chat UI.
w1.1-up: w1.1-ollama-up w1.1-openwebui-up ## W1.1 deploy/redeploy: Ollama (Qwen MoE on spark-3d37) + Open WebUI. Set LAB_HOST=<fqdn> for the Open WebUI public hostname.

w1.1-down: w1.1-openwebui-down w1.1-ollama-down ## W1.1 tear down both (deletes namespaces + PVCs; weights re-pulled on next up)

w1.1-status: w1.1-ollama-status w1.1-openwebui-status ## W1.1 quick status (both)

# Per-component targets, so each can be brought up independently when needed.
w1.1-ollama-up: ## W1.1 Ollama engine only (cluster-internal; no public ingress).
	kubectl apply -k inference/ollama-qwen-moe
	kubectl -n lab-ollama-qwen-moe rollout status statefulset/ollama --timeout=10m

w1.1-ollama-down: ## W1.1 Ollama engine only - tear down
	kubectl delete -k inference/ollama-qwen-moe --ignore-not-found

w1.1-ollama-status: ## W1.1 Ollama engine only - status
	kubectl -n lab-ollama-qwen-moe get statefulset,pod,svc,ingress,certificate,pvc -o wide

w1.1-openwebui-up: ## W1.1 Open WebUI customer chat UI only. Set LAB_HOST=<fqdn> to override.
	@test -f inference/openwebui/secret.local.yaml || \
		( cd inference/openwebui && ./make-secrets.sh )
	$(call _with_host,inference/openwebui,open-webui-host,kubectl apply -k inference/openwebui)
	kubectl -n lab-openwebui rollout status deployment/open-webui --timeout=10m

w1.1-openwebui-down: ## W1.1 Open WebUI only - tear down (deletes namespace + chat history PVC)
	kubectl delete -k inference/openwebui --ignore-not-found

w1.1-openwebui-status: ## W1.1 Open WebUI only - status
	kubectl -n lab-openwebui get deployment,pod,svc,ingress,certificate,pvc -o wide

# W1.2 = vLLM serving Qwen MoE on spark-2c24 (cluster-internal; no public ingress).
# Pairs with W1.1 Ollama on spark-3d37 to surface the "same logical model, two
# engines, two quantizations" pain in W1.3.
w1.2-up: w1.2-vllm-up ## W1.2 deploy/redeploy: vLLM (Qwen MoE BF16 on spark-2c24)

w1.2-down: w1.2-vllm-down ## W1.2 tear down (deletes namespace + PVC; ~60 GB re-pulled on next up)

w1.2-status: w1.2-vllm-status ## W1.2 quick status

w1.2-vllm-up: ## W1.2 vLLM only. Cold rollout pulls ~60 GB safetensors; allow up to ~1 h.
	kubectl apply -k inference/vllm-qwen-moe
	kubectl -n lab-vllm-qwen-moe rollout status statefulset/vllm --timeout=60m

w1.2-vllm-down: ## W1.2 vLLM only - tear down
	kubectl delete -k inference/vllm-qwen-moe --ignore-not-found

w1.2-vllm-status: ## W1.2 vLLM only - status
	kubectl -n lab-vllm-qwen-moe get statefulset,pod,svc,configmap,pvc -o wide
