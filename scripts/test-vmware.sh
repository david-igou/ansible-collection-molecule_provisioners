#!/usr/bin/env bash
# Local opt-in VMware tests; pass credential values through environment only.
set -euo pipefail
: "${VMWARE_HOST:?Set VMWARE_HOST}"
: "${VMWARE_USER:?Set VMWARE_USER}"
: "${VMWARE_PASSWORD:?Set VMWARE_PASSWORD}"
: "${MP_TEST_VMWARE_EXPECTED_UUID:?Set the expected vCenter instance UUID}"
args=(run --rm --network host -v "$PWD:/work:Z" -e HOME=/root -e RUN_VMWARE_LIFECYCLE=1)
for name in VMWARE_HOST VMWARE_USER VMWARE_PASSWORD VMWARE_PORT VMWARE_VALIDATE_CERTS \
  MP_TEST_VMWARE_EXPECTED_UUID MP_TEST_VMWARE_DATACENTER MP_TEST_VMWARE_CLUSTER \
  MP_TEST_VMWARE_FOLDER MP_TEST_VMWARE_DATASTORE MP_TEST_VMWARE_NETWORK \
  MP_TEST_VMWARE_TEMPLATE MP_TEST_VMWARE_SECOND_TEMPLATE MP_TEST_VMWARE_ROUTE_HOST; do
  args+=(-e "$name")
done
if [[ -n "${MP_TEST_VMWARE_ROUTE_HOST:-}" ]]; then
  : "${SSH_AUTH_SOCK:?Guest route helper requires the existing SSH agent}"
  test -S "$SSH_AUTH_SOCK"
  test -f "$HOME/.ssh/known_hosts"
  args+=(-e SSH_AUTH_SOCK -v "$SSH_AUTH_SOCK:$SSH_AUTH_SOCK" \
    -v "$HOME/.ssh/known_hosts:/root/.ssh/known_hosts:ro")
fi
# A caller can select the host split-DNS stub for a private lab domain.
if [[ -n "${MP_TEST_VMWARE_DNS:-}" ]]; then
  args+=(--dns "$MP_TEST_VMWARE_DNS")
fi
args+=("${VMWARE_TEST_IMAGE:-localhost/mp-vmware-ee:dev}")
podman "${args[@]}" sh -c 'make -B /work/.build/ansible_collections/david_igou/molecule_provisioners && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/integration/vmware -v -o addopts=""'
