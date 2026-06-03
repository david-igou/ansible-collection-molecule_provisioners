# david_igou.molecule_provisioners

Reusable [Molecule](https://ansible.readthedocs.io/projects/molecule/) provisioner playbooks and roles for testing Ansible collections.

Stop redefining `create.yml`/`destroy.yml`/`prepare.yml` per repo. Install this collection, write three one-line files in your scenario, and switch backends with one env var.

## Supported backends (v1.1)

| Backend | When to use |
| --- | --- |
| `podman` (default) | Containers, fastest CI loop |
| `kubevirt` | Real VMs in a Kubernetes cluster (requires KubeVirt) |
| `qemu` | Real VMs via direct `qemu-system` process (no libvirtd) |
| `docker` | Containers, when a local docker daemon is what's available |

## Installing

The collection is published on [Ansible Galaxy](https://galaxy.ansible.com/ui/repo/published/david_igou/molecule_provisioners/).
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
    version: 0.0.1-alpha
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
  test_sequence: [dependency, syntax, create, prepare, converge, verify, destroy]

verifier:
  name: ansible
```

**`create.yml` / `destroy.yml` / `prepare.yml`** (one-liners using FQCN):

```yaml
- name: Provision molecule instances
  import_playbook: david_igou.molecule_provisioners.create
```

(Mirror this for `destroy.yml` and `prepare.yml`. Names are required by ansible-lint 26.4+.)

**`inventory/hosts.yml`** (per scenario — describes WHICH instances to test):

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

**`inventory/group_vars/molecule.yml`** (backend selector + DRY defaults):

```yaml
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"

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

See [`docs/examples/`](docs/examples/) for the canonical starter and [`docs/MIGRATION.md`](docs/MIGRATION.md) if you're translating from a `platforms:`-based scenario.

## Controller-host prerequisites by backend

| Backend | Required on the molecule controller |
| --- | --- |
| `podman` | `podman` |
| `kubevirt` | `kubectl` + a kubeconfig pointing at a KubeVirt-enabled cluster |
| `qemu` | `qemu-system-x86_64`, `qemu-img`, `cloud-localds` (or `genisoimage`); OVMF firmware only when a host sets `firmware: uefi` |
| `docker` | A reachable local docker daemon and the `docker` python package (`pip install docker`) |

## What's in the box

- `playbooks/{create,destroy,prepare}.yml` — top-level dispatchers; read `mp_backend` (driven by `$PROVISIONER` env var by convention), validate, dispatch.
- `playbooks/reset.yml` — standalone purge playbook (`david_igou.molecule_provisioners.reset`); currently removes podman containers labeled `owner=molecule`.
- `roles/podman/` — uses `containers.podman.podman_container` + `containers.podman.podman_network`.
- `roles/kubevirt/` — generates an SSH keypair, creates `VirtualMachine` + `NodePort` Service per host, writes the molecule inventory file.
- `roles/qemu/` — caches base qcow2 images, builds NoCloud seed ISOs, launches per-VM `qemu-system-x86_64` processes with SLIRP `hostfwd` for SSH.
- `roles/docker/` — uses `community.docker.docker_container` + `community.docker.docker_network`.

Every backend produces a host group named `molecule` containing all platform hosts.

## Out of scope

- AWS, Azure, GCP backends
- qemu via libvirtd (use the `process` path that ships, or a future minor)
- qemu remote / non-controller-local hosts
- qemu NAT or bridge networking (SLIRP only in v1.1)
- LoadBalancer / ClusterIP+port-forward kubevirt service types
- Windows/macOS guests
- Per-platform networks beyond `podman.podman_network` and `docker.networks`
- Docker image build at create time, private-registry login, remote/TLS docker daemons
- Molecule `shared_state` / shared default-scenario pattern

## Licensing

GPL v3.0 or later — see [LICENSE](LICENSE).
