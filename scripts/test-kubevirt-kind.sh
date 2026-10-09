#!/usr/bin/env bash
set -euo pipefail

for tool in kind kubectl make pytest molecule ansible-playbook; do
    command -v "$tool" >/dev/null || { printf 'Missing prerequisite: %s\n' "$tool" >&2; exit 1; }
done

test_directory=$(mktemp -d "${TMPDIR:-/tmp}/mp-kubevirt-test.XXXXXX")
cluster_name="mp-kubevirt-$(date +%s)-$$"
context="kind-${cluster_name}"
created=0
export KUBECONFIG="${test_directory}/kubeconfig"
export MOLECULE_NAMESPACE=molecule
export MP_TEST_STORAGE_CLASS=local-path
unset MP_TEST_CONNECTION_IP MP_TEST_DATASOURCE MP_TEST_DATASOURCE_NAMESPACE

cleanup() {
    status=$?
    trap - EXIT
    if [[ "$created" == 1 ]]; then
        if [[ "${KEEP_KUBEVIRT_TEST_CLUSTER:-0}" == 1 ]]; then
            printf 'Kept test cluster %s; kubeconfig path: %s\n' "$cluster_name" "$KUBECONFIG"
            exit "$status"
        fi
        if ! kind delete cluster --name "$cluster_name"; then
            printf 'Cleanup failed for %s; kubeconfig path: %s\n' "$cluster_name" "$KUBECONFIG" >&2
            exit 1
        fi
    fi
    rm -rf "$test_directory"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Refuse a collision before marking this cluster as ours to clean up.
existing_clusters=$(kind get clusters)
if grep -Fxq "$cluster_name" <<< "$existing_clusters"; then
    printf 'Cluster already exists: %s\n' "$cluster_name" >&2
    exit 1
fi
created=1
kind create cluster --name "$cluster_name" --image kindest/node:v1.31.0 --kubeconfig "$KUBECONFIG" --wait 120s
chmod 600 "$KUBECONFIG"
[[ "$(kubectl --context "$context" config current-context)" == "$context" ]]

kubectl --context "$context" apply -f https://github.com/kubevirt/kubevirt/releases/download/v1.8.4/kubevirt-operator.yaml
kubectl --context "$context" apply -f https://github.com/kubevirt/kubevirt/releases/download/v1.8.4/kubevirt-cr.yaml
kubectl --context "$context" -n kubevirt patch kubevirt kubevirt --type merge --patch '
spec:
  configuration:
    developerConfiguration:
      useEmulation: true
'
kubectl --context "$context" -n kubevirt wait --for=condition=Available kv/kubevirt --timeout=10m
kubectl --context "$context" create namespace molecule

kubectl --context "$context" apply -f https://github.com/kubevirt/containerized-data-importer/releases/download/v1.62.0/cdi-operator.yaml
kubectl --context "$context" -n cdi rollout status deployment/cdi-operator --timeout=5m
kubectl --context "$context" apply -f https://github.com/kubevirt/containerized-data-importer/releases/download/v1.62.0/cdi-cr.yaml
kubectl --context "$context" -n cdi wait --for=condition=Available cdi/cdi --timeout=10m
kubectl --context "$context" apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.31/deploy/local-path-storage.yaml
kubectl --context "$context" -n local-path-storage rollout status deployment/local-path-provisioner --timeout=5m

cd "$(dirname "${BASH_SOURCE[0]}")/.."
make test-kubevirt test-kubevirt-lifecycle test-kubevirt-access test-kubevirt-storage
