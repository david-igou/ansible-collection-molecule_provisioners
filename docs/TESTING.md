# Local testing

Pull-request Actions run one job: Ansible lint, YAML lint, and changelog lint.
Unit tests, sanity checks, collection builds, and backend integration tests run
locally. Galaxy publishing runs when a release is published.

## Setup

Use a source checkout in a development or execution environment with
Python 3.12+, GNU Make, and
the backend tools listed in the [README](../README.md#controller-host-prerequisites-by-backend).
For a host Python setup, create a virtual environment first:

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
```

`make install` installs the pinned lint tools, test dependencies, and Ansible
collections. Run all commands below from the collection root. Test targets
create an ignored `.build/ansible_collections/` layout so they work before a
Molecule scenario has created its own collection links.

## Offline checks

```bash
make lint
make test-unit
make build
make sanity
```

`make test-unit` runs the renderer and validation regressions without a cluster
or container backend. `make sanity` uses `ansible-test sanity --docker` and
requires a Docker-compatible container engine. Use `SANITY_ARGS` to select
checks or a Python version.

Renderer changes also need a check on an older supported Ansible version.
This example runs the offline suite with ansible-core 2.16 in a clean container:

```bash
podman run --rm \
  -v "$PWD:/work/ansible_collections/david_igou/molecule_provisioners" \
  -w /work/ansible_collections/david_igou/molecule_provisioners \
  -e ANSIBLE_COLLECTIONS_PATH=/work \
  python:3.12-slim bash -c '
    python -m pip install "ansible-core>=2.16,<2.17" pytest pyyaml
    ansible-galaxy collection install containers.podman kubernetes.core community.crypto community.docker community.general
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/unit -q -o addopts=""
  '
```

## Backend tests

| Target | Coverage | Prerequisites |
| --- | --- | --- |
| `make test-podman` | Default Molecule scenario | Podman |
| `make test-podman-startup` | Exited containers, diagnostics, retries, cleanup | Podman |
| `make test-docker` | Default Molecule scenario | Reachable Docker daemon |
| `make test-docker-functional` | Docker role regressions | Reachable Docker daemon |
| `make test-qemu` | Default Molecule scenario | QEMU and NoCloud ISO tools |
| `make test-qemu-functional` | Validation and BIOS/UEFI CPU launches | QEMU, with OVMF for UEFI |
| `make test-qemu-testing` | Real guest disk I/O, routed links, forwards, isolation, diagnostics and network_cli | QEMU, Debian cloud image, ISO tools, RouterOS connection plugin |
| `make test-kubevirt` | Default Molecule scenario | KubeVirt cluster selected by `KUBECONFIG` |
| `make test-kubevirt-lifecycle` | Overlapping runs, full definitions, failed cleanup | KubeVirt cluster |
| `make test-kubevirt-access` | Custom SSH, TCP/UDP endpoints, Service updates | KubeVirt cluster with reachable NodePorts |
| `make test-kubevirt-storage` | Managed-disk I/O, retries, cleanup, external storage | KubeVirt, CDI, suitable storage class |

Backend tests create and delete test infrastructure. Choose a test namespace
and verify the cluster identity before running against an existing cluster.
For OpenShift, use `oc whoami --show-server` and `oc whoami`.

For the KubeVirt regression targets, set `MOLECULE_NAMESPACE` to that namespace.
Set `MP_TEST_CONNECTION_IP` if the discovered node address is unreachable, and
`MP_TEST_STORAGE_CLASS` for storage tests (default: `standard`). The default
Molecule scenario uses the namespace configured in
`extensions/molecule/default/inventory/group_vars/molecule.yml`.

The optional golden-image test needs `MP_TEST_DATASOURCE` and, for a DataSource
outside the test namespace, `MP_TEST_DATASOURCE_NAMESPACE`. The cluster identity
needs permission to clone from that source namespace.

## Disposable local KubeVirt cluster

Install `kind` and `kubectl`, then run:

```bash
make test-kubevirt-kind
```

The script creates a uniquely named kind cluster with its own temporary
kubeconfig. It installs KubeVirt v1.8.4 in CPU emulation mode, CDI v1.62.0, and
local-path storage v0.0.31, then runs the default scenario and all three
KubeVirt regression targets. It deletes its cluster on success or failure.
The node image is `kindest/node:v1.31.0`, matching the previously verified setup.

Kind uses Docker by default. For Podman:

```bash
KIND_EXPERIMENTAL_PROVIDER=podman make test-kubevirt-kind
```

Rootless Podman needs kind's host prerequisites, including cgroup delegation.
See [kind's rootless guide](https://kind.sigs.k8s.io/docs/user/rootless/).
The controller also needs a route to the kind node's address and NodePorts;
with a rootless engine, this may require running inside its network namespace.

To retain a cluster for inspection:

```bash
KEEP_KUBEVIRT_TEST_CLUSTER=1 make test-kubevirt-kind
```

The script prints its cluster name and kubeconfig path. Use that path through
`KUBECONFIG` while inspecting it; remove the cluster with
`kind delete cluster --name <printed-name>` and delete its temporary directory
when finished. The script overrides inherited cluster test settings for its
own namespace, storage class, and node address.

The storage tests cover filesystem volumes. Driver-specific behavior, Block
volumes, other clone sources, and Windows authentication variants need tests
on the chosen cluster and guest images.

## QEMU guest regressions

`make test-qemu-testing` creates disposable local VMs, authenticates through the
public provisioner lifecycle, and destroys its runs in cleanup. Set
`MP_TEST_QEMU_IMAGE` to a pinned Debian 12 cloud-image URL or `file://` URL. The
default is the upstream rolling generic image; offline or reproducible runs
should supply their own image. `MP_TEST_QEMU_CACHE` selects a reusable image and
reservation directory. The tests need `ansible.netcommon`, `community.routeros`,
and Paramiko in the controller environment. The network_cli test runs the actual
RouterOS connection plugin against a small test CLI in a Linux guest; it does
not certify a particular CHR image.

The guest tests cover SCSI/SATA disk I/O and reboot persistence, two overlapping
runs, management-key injection, extra-interface NoCloud network configuration,
three-guest routing with a denied flow, real TCP/UDP application traffic, a
persisted UEFI variable, and recovery after partial create. A separate fixture
installs the built collection and checks Molecule list/login from a consumer.
The launch suite separately covers BIOS/UEFI and KVM/TCG CPU choices. KVM tests
skip if `/dev/kvm` is unavailable; UEFI tests skip if OVMF is absent.

Run the same guest target with no accessible KVM device to test TCG. Increase
`mp_qemu_wait_timeout` for slower consumer images. All runs must share the same
`mp_qemu_runtime_cache_dir` to coordinate host-port reservations. Keep ephemeral
state after a cleanup error and retry destroy; do not delete ownership files
while processes are running.
