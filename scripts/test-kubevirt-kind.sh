#!/usr/bin/env bash
# Run the kubevirt Molecule scenario on a disposable kind cluster with
# KubeVirt and CDI. The cluster and its private kubeconfig are deleted on exit
# unless KEEP_KUBEVIRT_TEST_CLUSTER=1.
set -euo pipefail

KUBEVIRT_VERSION=v1.8.4
CDI_VERSION=v1.62.0
KIND_NODE_IMAGE=kindest/node:v1.31.0

for tool in kind kubectl make molecule; do
    command -v "$tool" >/dev/null || { printf 'Missing prerequisite: %s\n' "$tool" >&2; exit 1; }
done

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/mp-kubevirt-test.XXXXXX")
cluster="mp-kubevirt-$$"
export KUBECONFIG="${work_dir}/kubeconfig"
export MOLECULE_NAMESPACE=molecule

cleanup() {
    status=$?
    if [[ "${KEEP_KUBEVIRT_TEST_CLUSTER:-0}" == 1 ]]; then
        printf 'Kept cluster %s; KUBECONFIG=%s\n' "$cluster" "$KUBECONFIG"
    else
        kind delete cluster --name "$cluster" --kubeconfig "$KUBECONFIG" || status=1
        rm -rf "$work_dir"
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

kind create cluster --name "$cluster" --image "$KIND_NODE_IMAGE" --kubeconfig "$KUBECONFIG" --wait 120s
chmod 600 "$KUBECONFIG"

release=https://github.com/kubevirt/kubevirt/releases/download/$KUBEVIRT_VERSION
kubectl apply -f "$release/kubevirt-operator.yaml"
kubectl apply -f "$release/kubevirt-cr.yaml"
# Fall back to software emulation when the kind node cannot use /dev/kvm.
if ! "${KIND_EXPERIMENTAL_PROVIDER:-docker}" exec "${cluster}-control-plane" test -c /dev/kvm; then
    printf 'No usable /dev/kvm in the kind node; enabling KubeVirt emulation\n'
    kubectl -n kubevirt patch kubevirt kubevirt --type merge \
        --patch '{"spec": {"configuration": {"developerConfiguration": {"useEmulation": true}}}}'
fi
kubectl -n kubevirt wait --for=condition=Available kv/kubevirt --timeout=15m

release=https://github.com/kubevirt/containerized-data-importer/releases/download/$CDI_VERSION
kubectl apply -f "$release/cdi-operator.yaml"
kubectl -n cdi rollout status deployment/cdi-operator --timeout=5m
kubectl apply -f "$release/cdi-cr.yaml"
kubectl -n cdi wait --for=condition=Available cdi/cdi --timeout=10m
# CDI does not know kind's local-path provisioner; tell it how to claim volumes.
kubectl wait --for=create storageprofile/standard --timeout=2m
kubectl patch storageprofile standard --type merge \
    --patch '{"spec": {"claimPropertySets": [{"accessModes": ["ReadWriteOnce"], "volumeMode": "Filesystem"}]}}'

kubectl create namespace "$MOLECULE_NAMESPACE"

# virt-api restarts once after KubeVirt reports Available; wait until its
# webhooks admit a VirtualMachine.
probe='{"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine",
  "metadata": {"name": "probe", "namespace": "'"$MOLECULE_NAMESPACE"'"},
  "spec": {"runStrategy": "Halted", "template": {"spec": {"domain": {"devices": {}}}}}}'
for _ in $(seq 60); do
    kubectl create --dry-run=server -f - <<<"$probe" >/dev/null 2>&1 && break
    sleep 5
done
kubectl create --dry-run=server -f - <<<"$probe" >/dev/null

cd "$(dirname "${BASH_SOURCE[0]}")/.."
make test-kubevirt
