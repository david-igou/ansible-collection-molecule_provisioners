# david_igou.molecule_provisioners

Reusable [Molecule](https://ansible.readthedocs.io/projects/molecule/) provisioner playbooks and roles for testing Ansible collections.

Install this collection and import its `create.yml`/`destroy.yml`/`prepare.yml` playbooks from three one-line scenario files. Switch backends with one environment variable.

KubeVirt runs use isolated resource names by default. Set
`mp_kubevirt_run_isolation: false` in `inventory/group_vars/molecule.yml` when
fixed names are required. See the
[KubeVirt role](roles/kubevirt/README.md#concurrent-runs-in-a-shared-namespace) for
retry, cleanup and lost-state recovery.

## Supported backends

| Backend | When to use |
| --- | --- |
| `podman` (default) | Containers, fastest CI loop |
| `kubevirt` | Real VMs in a Kubernetes cluster (requires KubeVirt) |
| `qemu` | Real VMs via direct `qemu-system` process (no libvirtd) |
| `docker` | Containers using a local Docker daemon |

See the [Podman](roles/podman/README.md), [KubeVirt](roles/kubevirt/README.md),
[QEMU](roles/qemu/README.md), and [Docker](roles/docker/README.md) role READMEs
for their settings and requirements. KubeVirt also supports Windows guests over
PSRP or WinRM.

## Installing

The collection is published on [Ansible Galaxy](https://galaxy.ansible.com/ui/repo/published/david_igou/molecule_provisioners/).
This README describes `main`. For a pinned release, use the documentation at
its matching [release tag](https://github.com/david-igou/ansible-collection-molecule_provisioners/tags).
**Pin an exact version** so test runs stay deterministic — a floating `main`
can change provisioner behavior between runs with no change on your side.

The recommended pattern (used by the reference consumer
[`ansible-collection-armbian`](https://github.com/david-igou/ansible-collection-armbian))
centralizes the pin in one file shared by every scenario, instead of a
per-scenario `collections.yml` that drifts independently:

**1. Pin the version in `extensions/molecule/requirements-test.yml`:**

```yaml
collections:
  - name: david_igou.molecule_provisioners
    version: 0.0.6-alpha
```

**2. Wire it into every scenario once via `extensions/molecule/config.yml`:**

```yaml
dependency:
  name: galaxy
  enabled: true
  options:
    requirements-file: extensions/molecule/requirements-test.yml
```

Molecule auto-merges `extensions/molecule/config.yml` into every scenario — but
only when invoked from the **collection root** (see [Running tests](#running-tests)).
Both files ship ready-to-copy under [`docs/examples/`](docs/examples/).

<details>
<summary>Tracking unreleased changes (git install)</summary>

To test against unreleased changes, point at the git repo instead of a Galaxy
version. Molecule's `dependency` step also reads a scenario-local
**`collections.yml`** (not `requirements.yml`):

```yaml
collections:
  - name: https://github.com/david-igou/ansible-collection-molecule_provisioners.git
    type: git
    version: main # floats — not for reproducible runs
```

</details>

## Using

In your collection's `extensions/molecule/<scenario>/` directory:

**`molecule.yml`** (boilerplate, identical for every consumer):

```yaml
---
ansible:
  executor:
    args:
      ansible_playbook:
        - --inventory=inventory/
        - --inventory=${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/
  playbooks:
    create: create.yml
    destroy: destroy.yml
    prepare: prepare.yml
    converge: converge.yml
    verify: verify.yml

scenario:
  name: default
  test_sequence:
    - dependency
    - syntax
    - create
    - prepare
    - converge
    - verify
    - destroy

verifier:
  name: ansible
```

**`create.yml` / `destroy.yml` / `prepare.yml`** (one-liners using FQCN):

```yaml
- name: Provision molecule instances
  ansible.builtin.import_playbook: david_igou.molecule_provisioners.create
```

(Mirror this for `destroy.yml` and `prepare.yml`. Names are required by ansible-lint 26.4+.)

**`inventory/hosts.yml`** (instances to test in this scenario):

```yaml
all:
  children:
    molecule:
      hosts:
        ubuntu-24:
          mp:
            podman:
              image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
            kubevirt:
              boot_source:
                type: container_disk
                image: quay.io/containerdisks/ubuntu:24.04
              ssh_user: ubuntu
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              ssh_user: ubuntu
            docker:
              image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
```

**`inventory/group_vars/molecule.yml`** (backend selector and shared defaults):

```yaml
mp_backend: "{{ lookup('ansible.builtin.env', 'PROVISIONER') | default('podman', true) }}"

mp_defaults:
  podman:
    command: /sbin/init
    privileged: true
  kubevirt:
    namespace: molecule
    memory: 1Gi
    ssh_user: cloud-user
  qemu:
    cpus: 2
    memory: 1024
    ssh_user: cloud-user
  docker:
    command_handling: compatibility
    privileged: true
```

Switch backends at runtime: `PROVISIONER=podman molecule test` (or `kubevirt`, `qemu`, `docker`).

A single-backend scenario may hardcode `mp_backend: qemu`, but the env-driven
form above is preferred — it keeps the `PROVISIONER=<backend>` override working
and stays consistent with multi-backend repos.

See [`docs/examples/`](docs/examples/) for the canonical starter and [`docs/MIGRATION.md`](docs/MIGRATION.md) if you're translating from a `platforms:`-based scenario.

## Running tests

Molecule auto-discovers `extensions/molecule/config.yml` — which wires in the
pinned dependency (see [Installing](#installing)) — **only when invoked from the
collection root**. Run it from anywhere else and the deterministic dependency
step silently won't engage. Commit a `Makefile` at the collection root that sets
`MOLECULE_GLOB` and runs from root:

```makefile
export MOLECULE_GLOB := extensions/molecule/*/molecule.yml

.PHONY: test
test:
	molecule test --all
```

`make test` then runs every scenario. For one scenario: `molecule test -s <scenario>`.
Switch backends with `PROVISIONER=<backend> make test`.

Also commit an `ansible.cfg` at the collection root so collection resolution and
connection timeouts don't depend on each contributor's shell environment:

```ini
[defaults]
collections_path = ~/.ansible/collections

[persistent_connection]
connect_timeout = 120
command_timeout = 120
```

The `[persistent_connection]` bump matters for the `qemu` and `kubevirt`
backends: their SSH guests are frequently managed over `network_cli`
(community.routeros, community.network, …), and the default 30s timeouts are too
tight. Copies of both files ship under [`docs/examples/`](docs/examples/).

## Controller-host prerequisites by backend

For development of this provisioner collection itself, see
[Local testing](docs/TESTING.md). Pull-request Actions run one lint job;
unit, sanity, and backend integration suites run locally.

| Backend | Required on the molecule controller |
| --- | --- |
| `podman` | `podman` |
| `kubevirt` | `kubectl` + a kubeconfig pointing at a KubeVirt-enabled cluster |
| `qemu` | `qemu-system-x86_64`, `qemu-img`, `cloud-localds` (or `genisoimage`); OVMF firmware only when a host sets `firmware: uefi` |
| `docker` | A reachable local docker daemon and the `docker` python package (`pip install docker`) |

On a Debian/Ubuntu controller, the `qemu` backend's system prerequisites are:

```bash
apt-get install -y qemu-system-x86 qemu-utils cloud-image-utils ovmf sshpass
```

Notes for the VM backends (`qemu`, `kubevirt`):

- **`network_cli` guests:** install `ansible-pylibssh` (`pip install ansible-pylibssh`).
  paramiko fails to negotiate SSH with some network OSes (e.g. RouterOS); pylibssh
  is required for those guests.
- **OVMF path (qemu UEFI only):** the role defaults to the Fedora/RHEL paths
  `mp_qemu_ovmf_code: /usr/share/edk2/ovmf/OVMF_CODE.fd` and
  `mp_qemu_ovmf_vars: /usr/share/edk2/ovmf/OVMF_VARS.fd`. Debian/Ubuntu ship OVMF
  under `/usr/share/OVMF/`. Override both paths in `inventory/group_vars/molecule.yml`
  using a matching code/vars pair installed on your controller. For example,
  with the 4M firmware files:

  ```yaml
  mp_qemu_ovmf_code: /usr/share/OVMF/OVMF_CODE_4M.fd
  mp_qemu_ovmf_vars: /usr/share/OVMF/OVMF_VARS_4M.fd
  ```

## What's in the box

- `playbooks/{create,destroy,prepare}.yml` — top-level dispatchers; read `mp_backend` (driven by `$PROVISIONER` env var by convention), validate, dispatch.
- `playbooks/reset.yml` — standalone purge playbook (`david_igou.molecule_provisioners.reset`); currently removes podman containers labeled `owner=molecule`.
- `roles/podman/` — uses `containers.podman.podman_container` + `containers.podman.podman_network`.
- `roles/kubevirt/` — creates VirtualMachines from simple variables or a full `vm_definition`, manages generated data disks, and writes runtime connection inventory. It supports custom connection options and named TCP/UDP application endpoints. NodePort mode creates one run-owned Service per VM. See the [KubeVirt role reference](roles/kubevirt/README.md).
- `roles/qemu/` — caches base qcow2 images, builds NoCloud seed ISOs, launches per-VM `qemu-system-x86_64` processes with SLIRP `hostfwd` for SSH.
- `roles/docker/` — uses `community.docker.docker_container` + `community.docker.docker_network`.

Every backend writes runtime connection details for the hosts in the inventory's `molecule` group.

## Out of scope

- AWS, Azure, GCP backends
- qemu via libvirtd
- qemu remote / non-controller-local hosts
- qemu NAT or bridge networking (SLIRP only)
- LoadBalancer / ClusterIP+port-forward kubevirt service types
- Windows guests on backends other than KubeVirt; macOS guests
- Per-platform networks beyond `podman.podman_network` and `docker.networks`
- Docker image build at create time, private-registry login, remote/TLS docker daemons
- Molecule `shared_state` / shared default-scenario pattern

## Licensing

MIT License. See [LICENSE](LICENSE).
