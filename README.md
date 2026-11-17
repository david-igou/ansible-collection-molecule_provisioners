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

A scenario is a few small files in your collection. The
[Getting started guide](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/guide_getting_started.html) builds this
one step by step, and [`docs/examples/`](docs/examples/) has every file ready
to copy:

```text
my_collection/
├── galaxy.yml
└── extensions/
    └── molecule/
        ├── config.yml              # settings shared by every scenario
        ├── requirements-test.yml   # pins this collection
        └── default/
            ├── molecule.yml        # the scenario's name
            ├── create.yml          # import_playbook: david_igou.molecule_provisioners.create
            ├── prepare.yml         # import_playbook: david_igou.molecule_provisioners.prepare
            ├── destroy.yml         # import_playbook: david_igou.molecule_provisioners.destroy
            ├── converge.yml        # runs the content you are testing
            ├── verify.yml          # checks the result
            └── inventory/
                ├── hosts.yml       # the instances
                └── group_vars/
                    └── molecule.yml   # mp_backend and shared settings
```

```console
$ MOLECULE_GLOB='extensions/molecule/*/molecule.yml' molecule test -s default
```

Moving a `platforms:`/`driver:` scenario? See the
[migration guide](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/guide_migration.html).

## Developing

```bash
make install         # Python requirements and dependency collections
make lint            # ansible-lint (production profile), yamllint, changelog lint
make test            # containers scenario on podman, then docker
make test-qemu       # two local VMs: BIOS and UEFI
make test-kubevirt   # needs KUBECONFIG for a cluster with KubeVirt and CDI
make docs            # lint and build the documentation site into .build/docs/build/html
make test-vmware     # needs VMWARE_* plus MP_TEST_VMWARE_{DATACENTER,CLUSTER,FOLDER,DATASTORE,NETWORK,TEMPLATE,SECOND_TEMPLATE}
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
