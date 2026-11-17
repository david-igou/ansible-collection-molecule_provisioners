# david_igou.molecule_provisioners

Molecule `create`, `prepare` and `destroy` playbooks for testing Ansible content
on Podman or Docker containers, or on QEMU, KubeVirt or VMware vSphere virtual
machines. A scenario lists its instances in inventory and imports the three
playbooks; switching backends is one variable.

| Backend | Instances | Controller needs |
| --- | --- | --- |
| [`podman`](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/backend_podman.html) | Containers | `podman` |
| [`docker`](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/backend_docker.html) | Containers | A Docker daemon |
| [`qemu`](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/backend_qemu.html) | Local VMs from cloud or appliance images | `qemu-system-x86_64`, `qemu-img`; `/dev/kvm` for speed; OVMF for UEFI |
| [`kubevirt`](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/backend_kubevirt.html) | VMs in a Kubernetes cluster, including Windows | `KUBECONFIG` for a cluster with KubeVirt (and CDI for imported disks) |
| [`vmware`](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/backend_vmware.html) | Clones of vSphere templates with cloud-init | vCenter access through the `VMWARE_*` variables; a route to the guests |

Requires ansible-core 2.19 or newer. Install the Python packages in
[`requirements.txt`](requirements.txt) on the controller (ansible-builder picks
them up automatically).

Documentation: **https://david-igou.github.io/ansible-collection-molecule_provisioners/**

## Quick start

Copy [`docs/examples/`](docs/examples/) into your collection: a pinned
dependency, a shared `extensions/molecule/config.yml`, and a scenario whose
`create.yml`, `prepare.yml` and `destroy.yml` import this collection's
playbooks. Then describe the instances in the scenario inventory:

```yaml
# inventory/group_vars/molecule.yml
mp_backend: "{{ lookup('ansible.builtin.env', 'PROVISIONER') | default('podman', true) }}"
mp_defaults:
  podman:
    image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
  qemu:
    image: https://cloud-images.ubuntu.com/releases/noble/release-20260518/ubuntu-24.04-server-cloudimg-amd64.img
    ssh_user: ubuntu

# inventory/hosts.yml
all:
  children:
    molecule:
      hosts:
        server: {}
        client: {}
```

`make test` (or `molecule test` from the collection root with
`MOLECULE_GLOB=extensions/molecule/*/molecule.yml`) runs the scenario in
containers; `PROVISIONER=qemu` runs it on VMs. The
[scenario guide](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/guide_scenarios.html) explains the layout and
settings merge, and the [migration guide](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/guide_migration.html)
covers `platforms:` scenarios and 0.0.7-alpha.

## Developing

```bash
make install         # Python requirements and dependency collections
make lint            # ansible-lint (production profile), yamllint, changelog lint
make test            # containers scenario on podman, then docker
make test-qemu       # two local VMs: BIOS and UEFI
make test-kubevirt   # needs KUBECONFIG for a cluster with KubeVirt and CDI
make docs            # lint and build the documentation site into .build/docs/build/html
make test-vmware     # needs VMWARE_* and MP_TEST_VMWARE_* (see the VMware backend guide)
scripts/test-kubevirt-kind.sh   # the KubeVirt scenario on a throwaway kind cluster
```

The scenarios in `extensions/molecule/` test this checkout through `.build/`,
which the Makefile puts first on the collection path. GitHub Actions runs lint
and builds the documentation site, publishing it to GitHub Pages from `main`;
run the scenarios for the backends you change before opening a pull request.
Guides live in `docs/docsite/rst/`; role reference pages come from each role's
`meta/argument_specs.yml`.

## License

MIT
