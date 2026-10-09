# Migrating to `david_igou.molecule_provisioners`

If you have a molecule scenario using the upstream `platforms:` + `driver:` shape (now called _pre ansible-native_ in molecule's own docs) and you want to use this collection, here is the field-by-field translation.

## Upgrading from 0.0.5-alpha to 0.0.6-alpha

KubeVirt run isolation is now enabled by default. Kubernetes resource names
include a unique run suffix; logical inventory host names remain unchanged.
Each run saves its resource mapping in the Molecule ephemeral directory and
uses it for retries and ownership-checked cleanup.

Destroy existing fixed-name guests with 0.0.5-alpha before upgrading. If you
have already upgraded, set this in `inventory/group_vars/molecule.yml` and
destroy with the original inventory:

```yaml
mp_kubevirt_run_isolation: false
```

Remove the override before the next create to use isolation, or keep it if
your scenario requires fixed names. Preserve the ephemeral directory until
destroy succeeds. The role does not adopt existing fixed-name guests into
an isolated run. See [run isolation and recovery](../roles/kubevirt/README.md#concurrent-runs-in-a-shared-namespace).

## Before — pre-ansible-native shape

```yaml
# molecule.yml
driver:
  name: default
  options:
    managed: true
    ansible_connection_options:
      connection: containers.podman.podman

platforms:
  - name: ubuntu-24
    image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
    command: /sbin/init
    privileged: true

provisioner:
  name: ansible
  playbooks:
    create: create.yml
    destroy: destroy.yml
    prepare: prepare.yml
```

## After — this collection's ansible-native shape

```yaml
# molecule.yml — boilerplate, identical for every consumer
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

```yaml
# inventory/hosts.yml — instances to test
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
```

```yaml
# inventory/group_vars/molecule.yml — backend selector and shared defaults
mp_backend: "{{ lookup('ansible.builtin.env', 'PROVISIONER') | default('podman', true) }}"

mp_defaults:
  podman:
    command: /sbin/init
    privileged: true
  kubevirt:
    namespace: molecule
    memory: 1Gi
    ssh_user: cloud-user
```

```yaml
# create.yml / destroy.yml / prepare.yml — one-liners using FQCN
- name: Provision molecule instances
  ansible.builtin.import_playbook: david_igou.molecule_provisioners.create
```

## Field-by-field translation

| Pre-ansible-native                                                        | Ansible-native (this collection)                                                                                                                                          |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `driver: name: default` + `options.ansible_connection_options.connection` | gone — the role writes `ansible_connection` per host into the runtime inventory                                                                                           |
| `platforms[].name`                                                        | inventory host name under `all.children.molecule.hosts.<name>`                                                                                                                  |
| `platforms[].image` (podman)                                              | `hostvars[<name>].mp.podman.image`                                                                                                                                        |
| `platforms[].image` (kubevirt containerdisk)                              | `hostvars[<name>].mp.kubevirt.boot_source` — set `type: container_disk` and `image: <containerdisk-url>`                                                                     |
| `platforms[].command`, `.privileged`, `.volumes`, etc.                    | `hostvars[<name>].mp.podman.<field>` (or hoisted to `mp_defaults.podman` if shared)                                                                                       |
| `platforms[].kubevirt.namespace`, `.memory`, etc.                         | `hostvars[<name>].mp.kubevirt.<field>` (or hoisted to `mp_defaults.kubevirt`)                                                                                             |
| `provisioner.name: ansible` + `provisioner.playbooks.*`                   | `ansible.playbooks.*`                                                                                                                                                     |
| `provisioner.env.PROVISIONER`                                             | `mp_backend` group var (this collection populates from `lookup('ansible.builtin.env', 'PROVISIONER')` in the example boilerplate, but the contract is `mp_backend`, not the env var name) |

## Steps

### 1. Add the dependency

Pin the dependency in `extensions/molecule/requirements-test.yml`:

```yaml
collections:
  - name: david_igou.molecule_provisioners
    version: 0.0.6-alpha
```

Copy [`examples/config.yml`](examples/config.yml) to
`extensions/molecule/config.yml` so every scenario uses this requirements file.
These docs describe `main`; use the matching release-tag documentation when
migrating to a pinned release.

### 2. Create the new inventory tree

```bash
mkdir -p extensions/molecule/<scenario>/inventory/group_vars
```

Translate each `platforms[]` entry into a host under `all.children.molecule.hosts` in `inventory/hosts.yml` (see the After example above).

### 3. Replace the scenario's `molecule.yml`

Copy [`examples/molecule.yml`](examples/molecule.yml) and adjust the scenario name and test sequence as needed. Copy [`examples/Makefile`](examples/Makefile) and [`examples/ansible.cfg`](examples/ansible.cfg) to the collection root.

### 4. Replace the lifecycle files

Replace `create.yml`/`destroy.yml`/`prepare.yml` with the one-liner FQCN imports from `docs/examples/`.

### 5. Verify

```bash
export MOLECULE_GLOB='extensions/molecule/*/molecule.yml'
ansible-galaxy collection install -r extensions/molecule/requirements-test.yml
PROVISIONER=podman   molecule test -s <scenario>
PROVISIONER=kubevirt molecule test -s <scenario>   # if you have a cluster with KubeVirt
```

Both should pass. If a host fails with "missing mp.<backend> in inventory", you forgot to add the backend block to that host. If validation fails with "mp_backend must be one of ...", set `mp_backend` in `inventory/group_vars/molecule.yml`.

## Unsupported configurations

- Cloud-provider backends (AWS, Azure, GCP).
- QEMU via libvirtd, qemu+ssh remote URIs, or NAT/bridge networking. The QEMU backend runs local processes with SLIRP networking.
- KubeVirt LoadBalancer or ClusterIP+port-forward services. `NodePort`, `None`, and `PodIP` are supported; see [SSH service types](../roles/kubevirt/README.md#ssh-service-types).
- Mixing backends within a single scenario run.
- Molecule's `shared_state` pattern.

## KubeVirt schema: `image:` → `boot_source:`

The bare `image:` shortcut was removed. Rewrite per-host blocks:

### Before

```yaml
mp:
  kubevirt:
    image: quay.io/containerdisks/ubuntu:24.04
```

### After

```yaml
mp:
  kubevirt:
    boot_source:
      type: container_disk
      image: quay.io/containerdisks/ubuntu:24.04
```

Four additional boot-source modes are available: `data_volume_url`,
`data_volume_pvc`, `data_volume_source_ref`, and `pvc`. See
[Boot sources](../roles/kubevirt/README.md#boot-sources).
