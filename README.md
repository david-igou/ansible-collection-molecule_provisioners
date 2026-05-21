# david_igou.molecule_provisioners

Reusable [Molecule](https://ansible.readthedocs.io/projects/molecule/) provisioner playbooks and roles for testing Ansible collections.

Stop redefining `create.yml`/`destroy.yml`/`prepare.yml` per repo. Install this collection, write three one-line files in your scenario, and switch backends with one env var.

## Supported backends (v1.1)

| Backend | When to use |
| --- | --- |
| `podman` (default) | Containers, fastest CI loop |
| `kubevirt` | Real VMs in a Kubernetes cluster (requires KubeVirt) |
| `qemu` | Real VMs via local libvirtd or direct `qemu-system` process |

## Installing

```bash
ansible-galaxy collection install david_igou.molecule_provisioners
```

Or via `requirements.yml`:

```yaml
collections:
  - name: david_igou.molecule_provisioners
    version: ">=1.0.0,<2.0.0"
```

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
              image: quay.io/containerdisks/ubuntu:24.04
              ssh_user: ubuntu
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              driver: libvirt
              ssh_user: ubuntu
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
    network:
      mode: slirp
```

Switch backends at runtime: `PROVISIONER=podman molecule test` or `PROVISIONER=kubevirt molecule test`.

See [`docs/examples/`](docs/examples/) for the canonical starter and [`docs/MIGRATION.md`](docs/MIGRATION.md) if you're translating from a `platforms:`-based scenario.

## Controller-host prerequisites by backend

| Backend | Required on the molecule controller |
| --- | --- |
| `podman` | `podman` |
| `kubevirt` | `kubectl` + a kubeconfig pointing at a KubeVirt-enabled cluster |
| `qemu` | `qemu-system-x86_64`, `qemu-img`, `cloud-localds` (or `genisoimage`); plus `libvirtd` reachable at the configured URI for `driver: libvirt` |

## What's in the box

- `playbooks/{create,destroy,prepare}.yml` — top-level dispatchers; read `mp_backend` (driven by `$PROVISIONER` env var by convention), validate, dispatch.
- `roles/podman/` — uses `containers.podman.podman_container` + `containers.podman.podman_network`.
- `roles/kubevirt/` — generates an SSH keypair, creates `VirtualMachine` + `NodePort` Service per platform, writes the molecule inventory file.

Both roles produce a host group named `molecule` containing all platform hosts.

## Out of scope for v1.0

- docker, AWS, Azure, GCP backends
- qemu/libvirt remote URIs and `network.mode: bridge` (planned for a later minor)
- LoadBalancer / ClusterIP+port-forward kubevirt service types
- Windows/macOS guests
- Per-platform networks beyond `podman.podman_network`
- Molecule `shared_state` / shared default-scenario pattern

## Licensing

GPL v3.0 or later — see [LICENSE](LICENSE).
