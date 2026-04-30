#!/bin/bash
# Generates the shared htpasswd Secret used by both engine Ingresses.
# Output is two files (one per engine namespace) so each Ingress can
# reference a Secret in its own namespace; nginx-ingress requires the
# auth Secret to live in the same namespace as the Ingress unless the
# controller is started with --allow-cross-namespace-auth-secret=true,
# which we don't depend on.
#
# Usage:
#   ./make-htpasswd.sh <username> <password>
#   ./make-htpasswd.sh                       # prompts interactively
#
# The generated files are gitignored (`*.local.yaml`).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -eq 2 ]]; then
  USER="$1"
  PASS="$2"
elif [[ $# -eq 0 ]]; then
  read -r -p "username: " USER
  read -r -s -p "password: " PASS
  echo
else
  echo "usage: $0 [<user> <pass>]" >&2
  exit 2
fi

if ! command -v htpasswd >/dev/null 2>&1; then
  echo "htpasswd not found. Install apache2-utils / httpd-tools." >&2
  exit 1
fi

# Generate the htpasswd line. -n prints to stdout, -B uses bcrypt.
HTLINE="$(htpasswd -nbB "$USER" "$PASS")"
B64="$(printf '%s\n' "$HTLINE" | base64 | tr -d '\n')"

for NS in lab-ollama-qwen-moe lab-vllm-qwen-moe; do
  cat > "$HERE/auth-${NS}.local.yaml" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: lab-api-basic-auth
  namespace: ${NS}
  labels:
    lab.unbounded.cloud/wave: "1"
    lab.unbounded.cloud/wave-item: W1.4
type: Opaque
data:
  # nginx-ingress expects the htpasswd content under the key "auth".
  auth: ${B64}
EOF
  echo "wrote $HERE/auth-${NS}.local.yaml"
done

echo
echo "Apply with:"
echo "  kubectl apply -f $HERE/auth-lab-ollama-qwen-moe.local.yaml"
echo "  kubectl apply -f $HERE/auth-lab-vllm-qwen-moe.local.yaml"
