# david_igou.molecule_provisioners

Reusable [Molecule](https://ansible.readthedocs.io/projects/molecule/) provisioner playbooks and roles for testing Ansible collections.

Stop redefining `create.yml`/`destroy.yml`/`prepare.yml` per repo. Install this collection, write three one-line files in your scenario, and switch backends with one env var.

## Supported backends (v1.0)

| Backend | When to use |
| --- | --- |
| `podman` (default) | Containers, fastest CI loop |
| `kubevirt` | Real VMs in a Kubernetes cluster (requires KubeVirt) |

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

In your collection's `extensions/molecule/<scenario>/` directory, create three one-line files:

```yaml
# create.yml
- import_playbook: david_igou.molecule_provisioners.create
```

```yaml
# destroy.yml
- import_playbook: david_igou.molecule_provisioners.destroy
```

```yaml
# prepare.yml
- import_playbook: david_igou.molecule_provisioners.prepare
```

Then point your scenario's `molecule.yml` at them and define your platforms with both `podman` and `kubevirt` blocks (consumers can omit a block if they never use that backend):

```yaml
provisioner:
  name: ansible
  playbooks:
    create: create.yml
    destroy: destroy.yml
    prepare: prepare.yml
    converge: converge.yml

platforms:
  - name: ubuntu-24
    podman:
      image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
      command: sleep 1d
    kubevirt:
      image: quay.io/containerdisks/ubuntu:24.04
      namespace: molecule
      ssh_service:
        type: NodePort
      ansible_user: cloud-user
      memory: 4Gi
      disk_size: 30Gi
```

Switch backends per run:

```bash
PROVISIONER=podman   molecule test    # default
PROVISIONER=kubevirt molecule test
```

A complete starter template is in [`docs/examples/`](docs/examples/). To migrate an existing collection, see [`docs/MIGRATION.md`](docs/MIGRATION.md).

## What's in the box

- `playbooks/{create,destroy,prepare}.yml` — top-level dispatchers; read `$PROVISIONER`, validate, dispatch.
- `roles/podman/` — uses `containers.podman.podman_container` + `containers.podman.podman_network`.
- `roles/kubevirt/` — generates an SSH keypair, creates `VirtualMachine` + `NodePort` Service per platform, writes the molecule inventory file.

Both roles produce a host group named `molecule` containing all platform hosts.

## Out of scope for v1.0

- docker, qemu/libvirt, AWS, Azure, GCP backends
- LoadBalancer / ClusterIP+port-forward kubevirt service types
- Windows/macOS guests
- Per-platform networks beyond `podman.podman_network`
- Molecule `shared_state` / shared default-scenario pattern

## Licensing

GPL v3.0 or later — see [LICENSE](LICENSE).
