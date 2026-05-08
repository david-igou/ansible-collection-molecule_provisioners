# Migrating to `david_igou.molecule_provisioners`

If you have a molecule scenario using the upstream `platforms:` + `driver:` shape (now called _pre ansible-native_ in molecule's own docs) and you want to use this collection, here is the field-by-field translation.

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
  test_sequence: [dependency, syntax, create, prepare, converge, verify, destroy]

verifier:
  name: ansible
```

```yaml
# inventory/hosts.yml — describes WHICH instances to test
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
```

```yaml
# inventory/group_vars/molecule.yml — backend selector + DRY defaults
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"

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
  import_playbook: david_igou.molecule_provisioners.create
```

## Field-by-field translation

| Pre-ansible-native                                                              | Ansible-native (this collection)                                                                                                         |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `driver: name: default` + `options.ansible_connection_options.connection`       | gone — the role writes `ansible_connection` per host into the runtime inventory                                                          |
| `platforms[].name`                                                              | inventory host name under `groups.molecule.hosts.<name>`                                                                                 |
| `platforms[].image` (podman)                                                    | `hostvars[<name>].mp.podman.image`                                                                                                       |
| `platforms[].image` (kubevirt containerdisk)                                    | `hostvars[<name>].mp.kubevirt.image`                                                                                                     |
| `platforms[].command`, `.privileged`, `.volumes`, etc.                          | `hostvars[<name>].mp.podman.<field>` (or hoisted to `mp_defaults.podman` if shared)                                                     |
| `platforms[].kubevirt.namespace`, `.memory`, etc.                               | `hostvars[<name>].mp.kubevirt.<field>` (or hoisted to `mp_defaults.kubevirt`)                                                            |
| `provisioner.name: ansible` + `provisioner.playbooks.*`                         | `ansible.playbooks.*`                                                                                                                    |
| `provisioner.env.PROVISIONER`                                                   | `mp_backend` group var (this collection populates from `lookup('env', 'PROVISIONER')` in the example boilerplate, but the contract is `mp_backend`, not the env var name) |

## Steps

### 1. Add the dependency

In `requirements.yml`:

```yaml
collections:
  - name: david_igou.molecule_provisioners
    version: ">=1.0.0,<2.0.0"
```

### 2. Create the new inventory tree

```bash
mkdir -p extensions/molecule/<scenario>/inventory/group_vars
```

Translate each `platforms[]` entry into a host under `groups.molecule.hosts` in `inventory/hosts.yml` (see the After example above).

### 3. Replace the scenario's `molecule.yml`

Use the boilerplate from `docs/examples/molecule.yml`. The content is identical for every consumer.

### 4. Replace the lifecycle files

Replace `create.yml`/`destroy.yml`/`prepare.yml` with the one-liner FQCN imports from `docs/examples/`.

### 5. Verify

```bash
ansible-galaxy collection install -r requirements.yml
PROVISIONER=podman   molecule test -s <scenario>
PROVISIONER=kubevirt molecule test -s <scenario>   # if you have a cluster with KubeVirt
```

Both should pass. If a host fails with "missing mp.<backend> in inventory", you forgot to add the backend block to that host. If validation fails with "mp_backend must be one of ...", set `mp_backend` in `inventory/group_vars/molecule.yml`.

## What this collection does NOT support

- Backends other than podman and kubevirt (no docker, qemu/libvirt, cloud).
- KubeVirt service types other than NodePort.
- Mixing backends within a single scenario run.
- Molecule's `shared_state` pattern.
