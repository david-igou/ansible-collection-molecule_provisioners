# david_igou.molecule_provisioners

Molecule `create`, `prepare` and `destroy` playbooks for testing Ansible content
on Podman or Docker containers, or on QEMU, KubeVirt or VMware vSphere virtual
machines. A scenario lists its instances in inventory and imports the three
playbooks; switching backends is one variable.

| Backend | Instances | Controller needs |
| --- | --- | --- |
| [`podman`](roles/podman/README.md) | Containers | `podman` |
| [`docker`](roles/docker/README.md) | Containers | A Docker daemon |
| [`qemu`](roles/qemu/README.md) | Local VMs from cloud or appliance images | `qemu-system-x86_64`, `qemu-img`; `/dev/kvm` for speed; OVMF for UEFI |
| [`kubevirt`](roles/kubevirt/README.md) | VMs in a Kubernetes cluster, including Windows | `KUBECONFIG` for a cluster with KubeVirt (and CDI for imported disks) |
| [`vmware`](roles/vmware/README.md) | Clones of vSphere templates with cloud-init | vCenter access through the `VMWARE_*` variables; a route to the guests |

Requires ansible-core 2.19 or newer. Install the Python packages in
[`requirements.txt`](requirements.txt) on the controller (ansible-builder picks
them up automatically).

## Using it

A consumer collection keeps one pinned dependency and one shared Molecule
config, then a small directory per scenario. Copy the files from
[`docs/examples/`](docs/examples/).

```text
extensions/molecule/
├── config.yml               # shared Molecule settings and the pinned dependency
├── requirements-test.yml    # david_igou.molecule_provisioners, exact version
└── default/
    ├── molecule.yml         # scenario name only
    ├── create.yml           # import_playbook: david_igou.molecule_provisioners.create
    ├── prepare.yml          # import_playbook: david_igou.molecule_provisioners.prepare
    ├── destroy.yml          # import_playbook: david_igou.molecule_provisioners.destroy
    ├── converge.yml
    ├── verify.yml
    └── inventory/
        ├── hosts.yml
        └── group_vars/molecule.yml
```

Instances are the hosts of the `molecule` group. `mp_backend` picks the backend,
`mp_defaults.<backend>` holds settings shared by every host, and a host's own
`mp.<backend>` overrides them:

```yaml
# inventory/group_vars/molecule.yml
mp_backend: "{{ lookup('ansible.builtin.env', 'PROVISIONER') | default('podman', true) }}"
mp_defaults:
  podman:
    image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
  qemu:
    image: https://cloud-images.ubuntu.com/releases/noble/release-20260518/ubuntu-24.04-server-cloudimg-amd64.img
    ssh_user: ubuntu
  kubevirt:
    boot_source:
      type: container_disk
      image: quay.io/containerdisks/ubuntu:24.04
    ssh_user: ubuntu

# inventory/hosts.yml
all:
  children:
    molecule:
      hosts:
        server: {}
        client:
          mp:
            qemu:
              memory: 2048
```

`PROVISIONER=qemu molecule test` then runs the same scenario on VMs. Run
Molecule from the collection root with
`MOLECULE_GLOB=extensions/molecule/*/molecule.yml` so it finds `config.yml`; the
example `Makefile` sets this.

Each backend merges its own instance defaults, then `mp_defaults.<backend>`,
then `mp.<backend>`, and rejects settings it does not know. `ansible-doc -t role
david_igou.molecule_provisioners.<backend> -e instance` lists the QEMU, KubeVirt
and VMware settings; the container backends accept any option of the underlying
`podman_container` or `docker_container` module.

### How it works

`create` and `destroy` are plays on `hosts: molecule` that run every task on the
controller, so each host reads its own inventory variables and hosts are
provisioned in parallel. Create writes the connection details (container
connection, or SSH address, port and key) to
`$MOLECULE_EPHEMERAL_DIRECTORY/inventory/molecule_runtime.yml`, which the
second `--inventory` argument in `config.yml` loads for every later phase.
`prepare` waits until each instance accepts an Ansible connection. Scenarios can
chain their own plays before or after any of the three imports.

Coming from a `platforms:` and `driver:` scenario, or from 0.0.7-alpha? See
[docs/MIGRATION.md](docs/MIGRATION.md).

## Developing

```bash
make install         # Python requirements and dependency collections
make lint            # ansible-lint (production profile), yamllint, changelog lint
make test            # containers scenario on podman, then docker
make test-qemu       # two local VMs: BIOS and UEFI
make test-kubevirt   # needs KUBECONFIG for a cluster with KubeVirt and CDI
make test-vmware     # needs VMWARE_* and MP_TEST_VMWARE_* (see roles/vmware/README.md)
scripts/test-kubevirt-kind.sh   # the KubeVirt scenario on a throwaway kind cluster
```

The scenarios in `extensions/molecule/` test this checkout through `.build/`,
which the Makefile puts first on the collection path. GitHub Actions runs lint
only; run the scenarios for the backends you change before opening a pull
request.

## License

MIT
