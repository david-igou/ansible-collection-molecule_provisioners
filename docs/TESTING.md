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

`make install` installs the pinned lint tools, Python requirements and this
collection with its `galaxy.yml` dependencies. `test-requirements.yml` adds the
RouterOS collection used by appliance tests. Run all commands below from the
collection root. Test targets
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

The supported Ansible Core minimum is 2.19. `lint-requirements.txt` pins the
tested 2.19 release. Build the shared Python 3.12 EE and run the offline checks
inside it:

```bash
make ee
podman run --rm -v "$PWD:/work:Z" localhost/mp-ee:dev make lint test-unit build
```

Both `make test-aws` and `make test-vmware` use this image. Override `TEST_IMAGE`
to select another shared image. Its build installs Python packages from the
root requirements files and collection dependencies through `galaxy.yml`.
The packaged runtime `requirements.txt` is also discovered by Ansible Builder.

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
| `make test-aws` | EC2 lifecycle, networking, storage, transports and recovery | Podman, exported AWS credentials and explicit test account; [AWS setup](#aws) |
| `make test-vmware` | Installed native consumer lifecycle, retries and cleanup | Podman, vCenter credentials, expected identity and existing fixtures; [VMware setup](#vmware) |

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

## AWS

AWS tests run locally in a Podman execution environment; hosted Actions stay
lint-only. They create chargeable disposable infrastructure and always attempt
scoped teardown. Credentials alone never start tests: `make test-aws` explicitly
opts in, and requires `MP_TEST_AWS_ACCOUNT_ID` matching STS identity. All live tests
use **us-east-2** and discover an Amazon Linux 2023 AMI there. Windows cases also
discover an Amazon Windows Server 2022 image.

```bash
make ee
export AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2
export MP_TEST_AWS_ACCOUNT_ID=123456789012
export MP_TEST_AWS_CIDR=198.51.100.10/32
make test-aws
```

The target passes `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and optional
`AWS_SESSION_TOKEN` from the host into its execution environment. Export those
variables through your credential provider before invoking it; the target does
not mount `~/.aws` or pass a named profile into the container.
When `MP_TEST_AWS_CIDR` is omitted the harness discovers the controller's public
egress address through `checkip.amazonaws.com`; set it explicitly when SSH uses a
different egress path. Never add credentials to a test fixture or test report.
`AWS_TEST_ARGS` selects individual pytest cases. For a focused Linux lifecycle run:

```bash
make test-aws AWS_TEST_ARGS='-k managed_linux_lifecycle'
```

The harness retains private failure evidence under `.build/aws-live/`;
a cleanup failure reports its exact run
directory. Keep it until scoped destroy succeeds.

The tests exercise authenticated Linux/sudo, Windows WinRM and PSRP password
decryption, repeated create, existing-network preservation, inventory removal,
run isolation, failure recovery and independent SDK cleanup checks. Backend
validation/rendering and launch retry tests are also part of `make test-unit`.

## VMware

Build the shared Python 3.12 controller EE with `make ee`. The opt-in
`make test-vmware` target needs the vCenter environment credentials and all of:

- `MP_TEST_VMWARE_EXPECTED_UUID`: exact vCenter instance UUID, checked before mutation.
- `MP_TEST_VMWARE_DATACENTER`, `MP_TEST_VMWARE_CLUSTER`, `MP_TEST_VMWARE_FOLDER`:
  existing designated test placement.
- `MP_TEST_VMWARE_DATASTORE`, `MP_TEST_VMWARE_NETWORK`: existing writable datastore
  and DHCP port group.
- `MP_TEST_VMWARE_TEMPLATE`, `MP_TEST_VMWARE_SECOND_TEMPLATE`: Linux sources prepared
  for VMware GuestInfo/cloud-init, SSH, Python and sudo.

For the Signal9 split-DNS setup, also set `MP_TEST_VMWARE_DNS=127.0.0.53`.
The consumer and independent SDK audit honor `VMWARE_PORT` and
`VMWARE_VALIDATE_CERTS`; certificate validation defaults to true. Install the CA
in the controller EE, or explicitly set `VMWARE_VALIDATE_CERTS=false` for a lab
with a self-signed certificate.

Credentials alone never start a live test. The installed-consumer fixture creates
one clone from each template, creates a workload account and executes as that
account, and checks authenticated SSH/sudo, converge/idempotence,
Molecule list/login, repeated create with stable identities/keys, runtime overlay
recovery, repeated destroy and independent SDK verification that original VMs and
templates retain their names/template status/annotations. Failure evidence stays
private under `.build/vmware-live/`; preserve it after incomplete cleanup.

The optional `MP_TEST_VMWARE_ROUTE_HOST` is specific to the current lab's VPN/Docker
subnet overlap. With an existing SSH agent and known-hosts file, it adds only missing
private guest `/32` routes on that host through the existing `igou` interface and
removes precisely those routes in teardown. Ordinary routed environments should
leave it unset. The production role does not alter controller/VPN routes.

Offline validation/ownership/bootstrap tests run with `make test-unit`. Hosted
Actions remain lint-only. Full driver parity additionally needs Tools-based Linux
bootstrap, Windows/Sysprep/HTTPS WinRM, full/snapshot/linked disk backing, static and
distributed networking, negative ownership/recovery cases and dependency-version
coverage. Passing the initial GuestInfo consumer does not close that milestone.

Verified on 2026-11-12 against vSphere 8.0.2 using the existing Ubuntu 24.04 LTS
and CentOS Stream 9 GuestInfo templates and DHCP networking. The final native
`make test-vmware` run passed both guests without manual NIC recovery, including
real check mode without VM/key/journal creation, datasource detection, SSH/sudo,
login/list, converge/idempotence, stable identities/keys on retry, overlay recovery,
private persisted worker files and repeated cleanup. Its independent SDK audit
confirmed the created instance UUIDs were absent and original VM/template identity
and annotation fingerprints were unchanged. Temporary guest routes were removed.
Controller: Python 3.12.15, ansible-core 2.19.14, community.vmware 6.5.0,
vmware.vmware 2.10.0, vcf-sdk 9.0.0.0. This evidence covers GuestInfo Linux;
it does not certify the remaining parity cases above. That live result predates
the clone/key refactor and workload-account fixture. Those changes have offline
coverage and await another live run.
