# Ansible-native Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert `david_igou.molecule_provisioners` from molecule's pre-ansible-native `platforms:` shape to the ansible-native inventory shape — before v1.0 ships to Galaxy. Ansible-native becomes the v1.0 release shape; no backward-compat shim.

**Architecture:** The dispatcher → role → `tasks_from` pattern stays. The data source flips from `molecule_yml.platforms` to `groups['molecule']` + `hostvars[item].mp.<backend>`. A 3-level merge in each role layers role-shipped defaults under group-var defaults under per-host overrides. The single self-test scenario `extensions/molecule/default/` carries both backends' specs per host; CI runs it twice with `PROVISIONER` set differently.

**Tech Stack:** Ansible-core ≥ 2.15, molecule (ansible-native config shape — `ansible.executor` + `ansible.playbooks` blocks), pytest-ansible's `MoleculeScenario` fixture, kind + KubeVirt (`useEmulation`) for the kubevirt-backend self-test in CI.

**Source spec:** `docs/superpowers/specs/2026-05-08-ansible-native-conversion-design.md`

---

## File Structure

### Created

| File | Responsibility |
| --- | --- |
| `playbooks/group_vars/all.yml` | Declares `mp_supported_backends`. Loaded by every dispatcher play (`hosts: localhost` matches `all`). |
| `extensions/molecule/default/molecule.yml` | The single self-test scenario's molecule.yml in ansible-native shape. |
| `extensions/molecule/default/{create,destroy,prepare}.yml` | One-line `import_playbook` wrappers (FQCN). |
| `extensions/molecule/default/{converge,verify}.yml` | Self-test's own converge + verify (minimal — proves SSH/exec works). |
| `extensions/molecule/default/inventory/hosts.yml` | Two hosts, each with `mp.podman` and `mp.kubevirt` blocks. |
| `extensions/molecule/default/inventory/group_vars/molecule.yml` | `mp_backend` (env-driven) + `mp_defaults` for both backends. |
| `docs/examples/inventory/hosts.yml` | Consumer-facing inventory shape example. |
| `docs/examples/inventory/group_vars/molecule.yml` | Consumer-facing group_vars example. |
| `changelogs/fragments/ansible-native-v1.yml` | Release-note fragment. |

### Modified

| File | What changes |
| --- | --- |
| `playbooks/create.yml` | Read `mp_backend` from `hostvars[groups['molecule'][0]]`; validate every host has the active backend block; `include_role` unchanged. |
| `playbooks/destroy.yml` | Same dispatch logic as create. (No per-host block-existence assert — destroy is idempotent.) |
| `playbooks/prepare.yml` | Same dispatch logic; runs against `hosts: all` (not `localhost`) so the role can `wait_for_connection` per host. |
| `roles/podman/defaults/main.yml` | Add `mp_podman_role_defaults` (dict aggregating every optional field's default value). |
| `roles/kubevirt/defaults/main.yml` | Add `mp_kubevirt_role_defaults`. |
| `roles/podman/tasks/create.yml` | Add merge step at top (`_mp_specs`); loop over `groups['molecule']`; replace `item.podman.<f>` with `_mp_specs[item].<f>`; trim inventory-write block to only emit `ansible_connection: containers.podman.podman` per host. |
| `roles/podman/tasks/destroy.yml` | Loop over `groups['molecule']`; use host name (`item`) instead of `item.name`. |
| `roles/podman/tasks/prepare.yml` | No change to play body (already runs on each host). |
| `roles/podman/tasks/_networks.yml` | Loop over `groups['molecule']`; read `_mp_specs[item].podman_network`. |
| `roles/kubevirt/tasks/create.yml` | Add merge step; loop over `groups['molecule']`; standardize loop_var to `item`; trim inventory write. |
| `roles/kubevirt/tasks/destroy.yml` | Loop over `groups['molecule']`; use host name. |
| `roles/kubevirt/tasks/prepare.yml` | No change to play body. |
| `roles/kubevirt/tasks/_create_vm.yml` | Replace `vm.kubevirt.*` field accesses with `_mp_specs[item].*`. |
| `roles/kubevirt/tasks/_create_vm_dictionary.yml` | Same. |
| `roles/podman/meta/argument_specs.yml` | Drop platform-shape validation; describe role-level inputs only (`mp_podman_role_defaults`, `mp_podman_async_*`, etc.). |
| `roles/kubevirt/meta/argument_specs.yml` | Same. |
| `roles/podman/README.md`, `roles/kubevirt/README.md` | Replace platform-schema docs with `mp.<backend>.*` schema. |
| `.github/workflows/tests.yml` | Rename `integration` → `integration-podman`; rename `kubevirt` → `integration-kubevirt`; both run `pytest -k default`; update `all_green.needs`. |
| `docs/examples/molecule.yml` | Ansible-native shape (no `driver:`, no `platforms:`, no `provisioner:`). |
| `docs/MIGRATION.md` | Repurpose: from molecule v1 platforms-shape to this collection's ansible-native shape (the existing devhost-migration content is replaced wholesale). |
| `CLAUDE.md` | Update Architecture, Public-contract, Common-commands, lint, CI, "When updating provisioner logic" sections. The "Do not depend on `molecule-plugins`" section stays. |

### Deleted

| File | Reason |
| --- | --- |
| `extensions/molecule/podman/` (whole tree) | Superseded by `extensions/molecule/default/`. |
| `extensions/molecule/kubevirt/` (whole tree) | Superseded by `extensions/molecule/default/`. |
| `docs/examples/platforms.yml` | Superseded by `docs/examples/inventory/hosts.yml` + `docs/examples/inventory/group_vars/molecule.yml`. |

---

## Tasks

### Task 1: Add role-default dicts

**Files:**
- Modify: `roles/podman/defaults/main.yml`
- Modify: `roles/kubevirt/defaults/main.yml`

These dicts aggregate every per-host optional field's default value. The role's merge step layers them under `mp_defaults.<backend>` and `hostvars[item].mp.<backend>`. One dict-valued default keeps the merge expression a clean three-way `combine`.

- [ ] **Step 1: Append `mp_podman_role_defaults` to `roles/podman/defaults/main.yml`**

Append this block to the existing file (do not remove the existing `mp_podman_reserved_networks`, `mp_podman_async_*` defaults):

```yaml
# Per-host field defaults. Layered as: this dict <- mp_defaults.podman <- hostvars[item].mp.podman.
# Only `image` is required. Fields whose role default is "omit" (podman_network,
# tmpfs, exposed_ports, published_ports) are intentionally absent — the role's
# module calls use `| default(omit)` to handle their absence correctly.
mp_podman_role_defaults:
  command: /sbin/init
  privileged: false
  volumes: []
  capabilities: []
  env: {}
```

- [ ] **Step 2: Append `mp_kubevirt_role_defaults` to `roles/kubevirt/defaults/main.yml`**

Append:

```yaml
# Per-host field defaults. Layered as: this dict <- mp_defaults.kubevirt <- hostvars[item].mp.kubevirt.
# Only `image` is required and is therefore not present here.
mp_kubevirt_role_defaults:
  namespace: molecule
  ssh_user: cloud-user
  memory: 1Gi
  disk_size: 5Gi
  ssh_service:
    type: NodePort
```

- [ ] **Step 3: Lint**

Run: `ansible-lint roles/podman/defaults/main.yml roles/kubevirt/defaults/main.yml`
Expected: no findings.

- [ ] **Step 4: Commit**

```bash
git add roles/podman/defaults/main.yml roles/kubevirt/defaults/main.yml
git commit -m "feat(roles): add mp_<backend>_role_defaults dicts for ansible-native merge"
```

---

### Task 2: Add `mp_supported_backends` group var for dispatchers

**Files:**
- Create: `playbooks/group_vars/all.yml`

The dispatcher plays target `hosts: localhost` (and `all` for prepare). A `playbooks/group_vars/all.yml` file is automatically loaded for plays in `playbooks/`, regardless of what host they target, because `localhost` is in the `all` group.

- [ ] **Step 1: Create the file**

```yaml
---
# Loaded by every dispatcher play in playbooks/.
mp_supported_backends:
  - podman
  - kubevirt
```

- [ ] **Step 2: Lint**

Run: `yamllint playbooks/group_vars/all.yml`
Expected: no findings.

- [ ] **Step 3: Commit**

```bash
git add playbooks/group_vars/all.yml
git commit -m "feat(playbooks): add mp_supported_backends group var for dispatchers"
```

---

### Task 3: Rewrite the three dispatcher playbooks

**Files:**
- Modify: `playbooks/create.yml`
- Modify: `playbooks/destroy.yml`
- Modify: `playbooks/prepare.yml`

Each dispatcher reads `mp_backend` from the molecule group's hostvars, validates the inventory shape, and `include_role`s the matching role with the right `tasks_from`.

- [ ] **Step 1: Replace `playbooks/create.yml` with the full content below**

```yaml
---
- name: Molecule provisioner — create
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Assert molecule group exists in inventory
      ansible.builtin.assert:
        that:
          - "'molecule' in groups"
          - groups['molecule'] | length > 0
        fail_msg: "Inventory must define a 'molecule' group with at least one host."

    - name: Determine backend from molecule group
      ansible.builtin.set_fact:
        _mp_backend: "{{ hostvars[groups['molecule'][0]].mp_backend | default(none) }}"

    - name: Validate backend
      ansible.builtin.assert:
        that: _mp_backend in mp_supported_backends
        fail_msg: >-
          mp_backend must be one of {{ mp_supported_backends | join(', ') }}
          (got '{{ _mp_backend or '(unset)' }}'). Set it in
          inventory/group_vars/molecule.yml.

    - name: Validate every host has the active backend block
      ansible.builtin.assert:
        that: hostvars[item].mp[_mp_backend] is defined
        fail_msg: "Host '{{ item }}' is missing mp.{{ _mp_backend }} in inventory."
      loop: "{{ groups['molecule'] }}"

    - name: Run provisioner create
      ansible.builtin.include_role:
        name: "david_igou.molecule_provisioners.{{ _mp_backend }}"
        tasks_from: create
```

- [ ] **Step 2: Replace `playbooks/destroy.yml` with the content below**

(Same as create.yml minus the per-host block-existence assert — destroy is idempotent for hosts that were never created with the active backend.)

```yaml
---
- name: Molecule provisioner — destroy
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Assert molecule group exists in inventory
      ansible.builtin.assert:
        that:
          - "'molecule' in groups"
          - groups['molecule'] | length > 0
        fail_msg: "Inventory must define a 'molecule' group with at least one host."

    - name: Determine backend from molecule group
      ansible.builtin.set_fact:
        _mp_backend: "{{ hostvars[groups['molecule'][0]].mp_backend | default(none) }}"

    - name: Validate backend
      ansible.builtin.assert:
        that: _mp_backend in mp_supported_backends
        fail_msg: >-
          mp_backend must be one of {{ mp_supported_backends | join(', ') }}
          (got '{{ _mp_backend or '(unset)' }}').

    - name: Run provisioner destroy
      ansible.builtin.include_role:
        name: "david_igou.molecule_provisioners.{{ _mp_backend }}"
        tasks_from: destroy
```

- [ ] **Step 3: Replace `playbooks/prepare.yml` with the content below**

(Runs on `hosts: all` so each host's `wait_for_connection` runs against the right target. Backend determination and validation use `run_once: true` so they fire once per play, not per host.)

```yaml
---
- name: Molecule provisioner — prepare
  hosts: all
  gather_facts: false
  tasks:
    - name: Assert molecule group exists in inventory
      ansible.builtin.assert:
        that:
          - "'molecule' in groups"
          - groups['molecule'] | length > 0
        fail_msg: "Inventory must define a 'molecule' group with at least one host."
      run_once: true  # noqa: run-once[task]

    - name: Determine backend from molecule group
      ansible.builtin.set_fact:
        _mp_backend: "{{ hostvars[groups['molecule'][0]].mp_backend | default(none) }}"
      run_once: true  # noqa: run-once[task]

    - name: Validate backend
      ansible.builtin.assert:
        that: _mp_backend in mp_supported_backends
        fail_msg: >-
          mp_backend must be one of {{ mp_supported_backends | join(', ') }}
          (got '{{ _mp_backend or '(unset)' }}').
      run_once: true  # noqa: run-once[task]

    - name: Run provisioner prepare
      ansible.builtin.include_role:
        name: "david_igou.molecule_provisioners.{{ _mp_backend }}"
        tasks_from: prepare
```

- [ ] **Step 4: Lint**

Run: `ansible-lint playbooks/`
Expected: no findings.

- [ ] **Step 5: Commit**

```bash
git add playbooks/create.yml playbooks/destroy.yml playbooks/prepare.yml
git commit -m "feat(playbooks): dispatchers read mp_backend from inventory, validate groups['molecule']"
```

---

### Task 4: Refactor the podman role's task files

**Files:**
- Modify: `roles/podman/tasks/create.yml`
- Modify: `roles/podman/tasks/destroy.yml`
- Modify: `roles/podman/tasks/prepare.yml`
- Modify: `roles/podman/tasks/_networks.yml`

The merge step is added once at the top of `create.yml`, `destroy.yml`, and any task file that needs per-host fields (here, only create.yml needs it directly; destroy uses just the host name; _networks reads `podman_network` from the merged spec). For DRY, the merge logic could be factored to its own task file (e.g. `_compute_specs.yml`), but keeping it inline matches the existing role's style of repeating small task patterns rather than over-abstracting.

- [ ] **Step 1: Replace `roles/podman/tasks/create.yml` with the content below**

```yaml
---
# Compute per-host merged specs.
# Order: role defaults <- mp_defaults.podman <- hostvars[item].mp.podman.
- name: Initialize podman spec map
  ansible.builtin.set_fact:
    _mp_specs: {}

- name: Merge per-host specs with mp_defaults and role defaults
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: mp_podman_role_defaults
                 | combine(mp_defaults['podman'] | default({}))
                 | combine(hostvars[item].mp['podman'])
         }) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Build podman network list
  ansible.builtin.import_tasks: _networks.yml

- name: Create podman network(s)
  containers.podman.podman_network:
    name: "{{ item }}"
    state: present
  loop: "{{ __mp_podman_networks | default([]) | unique }}"
  when:
    - item not in mp_podman_reserved_networks
    - item is not match('^ns:')
    - item is not match('^container:')

# Launch all containers simultaneously (poll: 0), then wait for each below.
# A failure during the wait leaves still-running async jobs in flight;
# run destroy to clean up partially-created instances.
- name: Create molecule instance(s)
  containers.podman.podman_container:
    name: "{{ item }}"
    image: "{{ _mp_specs[item].image }}"
    state: started
    recreate: false
    command: "{{ _mp_specs[item].command | default(omit) }}"
    privileged: "{{ _mp_specs[item].privileged | default(false) }}"
    volume: "{{ _mp_specs[item].volumes | default(omit) }}"
    cap_add: "{{ _mp_specs[item].capabilities | default(omit) }}"
    expose: "{{ _mp_specs[item].exposed_ports | default(omit) }}"
    publish: "{{ _mp_specs[item].published_ports | default(omit) }}"
    network: "{{ _mp_specs[item].podman_network | default(omit) }}"
    env: "{{ _mp_specs[item].env | default({}) }}"
    tmpfs: "{{ _mp_specs[item].tmpfs | default(omit) }}"
  register: __mp_podman_create
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
  async: "{{ mp_podman_async_timeout }}"
  poll: 0

- name: Wait for instance(s) creation to complete
  ansible.builtin.async_status:
    jid: "{{ item.ansible_job_id }}"
  register: __mp_podman_jobs
  until: __mp_podman_jobs.finished
  retries: "{{ mp_podman_async_retries }}"
  delay: "{{ mp_podman_async_delay }}"
  loop: "{{ __mp_podman_create.results }}"
  loop_control:
    label: "{{ item.item }}"

# The consumer's static inventory already declares the molecule group. We only
# need to augment hosts with ansible_connection: containers.podman.podman so
# subsequent phases (prepare/converge/verify) talk to the containers via the
# podman connection plugin. Each molecule phase is a separate ansible-playbook
# invocation, so the augmentation has to be written to disk in
# molecule_ephemeral_directory/inventory/, which is in molecule's inventory chain.
- name: Build runtime connection dict
  ansible.builtin.set_fact:
    __mp_podman_runtime_hosts: >-
      {{ __mp_podman_runtime_hosts | default({})
         | combine({item: {'ansible_connection': 'containers.podman.podman'}}) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Write runtime connection inventory file
  vars:
    runtime_inventory:
      all:
        hosts: "{{ __mp_podman_runtime_hosts }}"
  ansible.builtin.copy:
    content: "{{ runtime_inventory | to_nice_yaml }}"
    dest: "{{ molecule_ephemeral_directory }}/inventory/molecule_runtime.yml"
    mode: "0600"

- name: Refresh inventory
  ansible.builtin.meta: refresh_inventory
```

- [ ] **Step 2: Replace `roles/podman/tasks/destroy.yml`**

```yaml
---
- name: Destroy molecule instance(s)
  containers.podman.podman_container:
    name: "{{ item }}"
    state: absent
  register: __mp_podman_destroy
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
  async: "{{ mp_podman_async_timeout }}"
  poll: 0

- name: Wait for instance(s) deletion to complete
  ansible.builtin.async_status:
    jid: "{{ item.ansible_job_id }}"
  register: __mp_podman_jobs
  until: __mp_podman_jobs.finished
  retries: "{{ mp_podman_async_retries }}"
  delay: "{{ mp_podman_async_delay }}"
  loop: "{{ __mp_podman_destroy.results }}"
  loop_control:
    label: "{{ item.item }}"

# Network cleanup needs the merged specs to know which networks were created.
# Defensive merge — a host may not have an mp.podman block on destroy if
# the consumer added it after a partial create, and destroy is idempotent.
- name: Initialize podman spec map
  ansible.builtin.set_fact:
    _mp_specs: {}

- name: Merge per-host specs with mp_defaults and role defaults
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: mp_podman_role_defaults
                 | combine(mp_defaults['podman'] | default({}))
                 | combine((hostvars[item].mp | default({}))['podman'] | default({}))
         }) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Build podman network list
  ansible.builtin.import_tasks: _networks.yml

- name: Delete podman network(s)
  containers.podman.podman_network:
    name: "{{ item }}"
    state: absent
  loop: "{{ __mp_podman_networks | default([]) | unique }}"
  when:
    - item not in mp_podman_reserved_networks
    - item is not match('^ns:')
    - item is not match('^container:')
```

- [ ] **Step 3: Replace `roles/podman/tasks/_networks.yml`**

```yaml
---
- name: Initialize podman network list
  ansible.builtin.set_fact:
    __mp_podman_networks: []

- name: Collect networks from per-host podman_network
  ansible.builtin.set_fact:
    __mp_podman_networks: >-
      {{ __mp_podman_networks +
         (_mp_specs[item].podman_network
          if (_mp_specs[item].podman_network is sequence
              and _mp_specs[item].podman_network is not string)
          else [_mp_specs[item].podman_network]) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
  when:
    - _mp_specs[item].podman_network is defined
    - _mp_specs[item].podman_network
```

- [ ] **Step 4: Verify `roles/podman/tasks/prepare.yml` is unchanged**

The existing file installs `sudo` against each host in the play and does not reference `molecule_yml.platforms`. No edit needed. Confirm by reading it — content should be the existing 5-line `package: name: sudo state: present` task.

- [ ] **Step 5: Lint**

Run: `ansible-lint roles/podman/`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add roles/podman/tasks/
git commit -m "feat(roles/podman): loop over groups['molecule'], 3-level merge, trim inventory write"
```

---

### Task 5: Refactor the kubevirt role's task files

**Files:**
- Modify: `roles/kubevirt/tasks/create.yml`
- Modify: `roles/kubevirt/tasks/destroy.yml`
- Modify: `roles/kubevirt/tasks/prepare.yml`
- Modify: `roles/kubevirt/tasks/_create_vm.yml`
- Modify: `roles/kubevirt/tasks/_create_vm_dictionary.yml`

Note: in v1 the kubevirt role uses `loop_var: vm` to make platform iteration read naturally inside the helper task files. v2 standardizes on the implicit `item` to match the podman role.

- [ ] **Step 1: Replace `roles/kubevirt/tasks/create.yml`**

```yaml
---
# Compute per-host merged specs.
# Order: role defaults <- mp_defaults.kubevirt <- hostvars[item].mp.kubevirt.
- name: Initialize kubevirt spec map
  ansible.builtin.set_fact:
    _mp_specs: {}

- name: Merge per-host specs with mp_defaults and role defaults
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: mp_kubevirt_role_defaults
                 | combine(mp_defaults['kubevirt'] | default({}))
                 | combine(hostvars[item].mp['kubevirt'])
         }) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

# An unset ssh_service.type defaults to NodePort everywhere (assert,
# Service-create when:, etc.) — keep both expressions in sync.
- name: Validate ssh_service.type per host
  ansible.builtin.assert:
    that:
      - (_mp_specs[item].ssh_service.type | default('NodePort'))
        in mp_kubevirt_allowed_ssh_service_types
    fail_msg: >-
      Host '{{ item }}' has unsupported ssh_service.type
      '{{ _mp_specs[item].ssh_service.type | default('(missing)') }}'.
      v1 supports only: {{ mp_kubevirt_allowed_ssh_service_types | join(', ') }}.
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Generate SSH key pair
  community.crypto.openssh_keypair:
    path: "{{ mp_kubevirt_ssh_key_path }}"
    type: ed25519
  register: __mp_kubevirt_ssh_keypair

- name: Set SSH public key fact
  ansible.builtin.set_fact:
    temporary_ssh_public_key: "{{ __mp_kubevirt_ssh_keypair.public_key }}"

- name: Create VirtualMachine in KubeVirt
  ansible.builtin.include_tasks: _create_vm.yml
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Create SSH NodePort Kubernetes service
  kubernetes.core.k8s:
    state: present
    definition:
      apiVersion: v1
      kind: Service
      metadata:
        name: "{{ item }}"
        namespace: "{{ _mp_specs[item].namespace }}"
      spec:
        ports:
          - port: 22
            protocol: TCP
            targetPort: 22
        selector:
          kubevirt.io/domain: "{{ item }}"
        type: NodePort
  when: (_mp_specs[item].ssh_service.type | default('NodePort')) == 'NodePort'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

# Look up cluster nodes once, before the per-VM dictionary loop, so every VM
# uses the same node IP. Subsequent dictionary build is purely local.
- name: Look up cluster nodes (for NodePort connection IP)
  kubernetes.core.k8s_info:
    api_version: v1
    kind: Node
  register: __mp_kubevirt_nodes

- name: Assert at least one cluster node is available
  ansible.builtin.assert:
    that: __mp_kubevirt_nodes.resources | length > 0
    fail_msg: "No cluster nodes returned from Kubernetes API. Check kubeconfig and cluster health."

- name: Choose first node InternalIP
  ansible.builtin.set_fact:
    __mp_kubevirt_node_ip: >-
      {{ (__mp_kubevirt_nodes.resources[0].status.addresses
          | selectattr('type', 'equalto', 'InternalIP') | list | first).address }}

- name: Initialize VM runtime inventory dict
  ansible.builtin.set_fact:
    __mp_kubevirt_runtime_hosts: {}

- name: Build VM runtime entries
  ansible.builtin.include_tasks: _create_vm_dictionary.yml
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Write runtime connection inventory file
  vars:
    runtime_inventory:
      all:
        hosts: "{{ __mp_kubevirt_runtime_hosts }}"
  ansible.builtin.copy:
    content: "{{ runtime_inventory | to_nice_yaml }}"
    dest: "{{ molecule_ephemeral_directory }}/inventory/molecule_runtime.yml"
    mode: "0600"

- name: Refresh inventory
  ansible.builtin.meta: refresh_inventory
```

- [ ] **Step 2: Replace `roles/kubevirt/tasks/_create_vm.yml`**

```yaml
---
- name: "Define VirtualMachine: {{ item }}"
  kubernetes.core.k8s:
    state: present
    definition:
      apiVersion: kubevirt.io/v1
      kind: VirtualMachine
      metadata:
        name: "{{ item }}"
        namespace: "{{ _mp_specs[item].namespace }}"
        labels:
          kubevirt.io/domain: "{{ item }}"
      spec:
        running: true
        template:
          metadata:
            labels:
              kubevirt.io/domain: "{{ item }}"
          spec:
            domain:
              cpu:
                cores: 2
              resources:
                requests:
                  memory: "{{ _mp_specs[item].memory }}"
              devices:
                disks:
                  - name: containerdisk
                    disk:
                      bus: virtio
                  - name: cloudinitdisk
                    disk:
                      bus: virtio
                interfaces:
                  - name: default
                    masquerade: {}
            networks:
              - name: default
                pod: {}
            volumes:
              - name: containerdisk
                containerDisk:
                  image: "{{ _mp_specs[item].image }}"
              - name: cloudinitdisk
                cloudInitNoCloud:
                  # Use the explicit `users:` list form so we always create the
                  # named user regardless of the containerdisk image's baked-in
                  # default_user (Ubuntu images have `ubuntu`, RHEL/CentOS
                  # images have `cloud-user`, etc.).
                  userData: |
                    #cloud-config
                    users:
                      - name: {{ _mp_specs[item].ssh_user }}
                        ssh_authorized_keys:
                          - {{ temporary_ssh_public_key }}
                        sudo: ALL=(ALL) NOPASSWD:ALL
                        shell: /bin/bash
                    chpasswd:
                      expire: false
```

- [ ] **Step 3: Replace `roles/kubevirt/tasks/_create_vm_dictionary.yml`**

```yaml
---
- name: "Look up NodePort service for: {{ item }}"
  kubernetes.core.k8s_info:
    api_version: v1
    kind: Service
    name: "{{ item }}"
    namespace: "{{ _mp_specs[item].namespace }}"
  register: __mp_kubevirt_svc
  until:
    - __mp_kubevirt_svc.resources | length > 0
    - (__mp_kubevirt_svc.resources[0].spec.ports[0].nodePort | default(0)) | int > 0
  retries: 10
  delay: 3

- name: "Add VM to runtime inventory dict: {{ item }}"
  ansible.builtin.set_fact:
    __mp_kubevirt_runtime_hosts: >-
      {{ __mp_kubevirt_runtime_hosts | combine({
          item: {
            'ansible_host': __mp_kubevirt_node_ip,
            'ansible_port': (__mp_kubevirt_svc.resources[0].spec.ports[0].nodePort),
            'ansible_user': _mp_specs[item].ssh_user,
            'ansible_ssh_private_key_file': mp_kubevirt_ssh_key_path,
            'ansible_connection': 'ssh',
            'ansible_ssh_common_args': '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
          }
      }) }}
```

- [ ] **Step 4: Replace `roles/kubevirt/tasks/destroy.yml`**

```yaml
---
# Recompute namespace per host for delete (we don't trust ephemeral state to
# survive across phases; mp_defaults provides the namespace if not per-host).
- name: Initialize kubevirt spec map
  ansible.builtin.set_fact:
    _mp_specs: {}

- name: Merge per-host specs with mp_defaults and role defaults
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: mp_kubevirt_role_defaults
                 | combine(mp_defaults['kubevirt'] | default({}))
                 | combine((hostvars[item].mp | default({}))['kubevirt'] | default({}))
         }) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Delete VirtualMachine in KubeVirt
  kubernetes.core.k8s:
    state: absent
    api_version: kubevirt.io/v1
    kind: VirtualMachine
    name: "{{ item }}"
    namespace: "{{ _mp_specs[item].namespace }}"
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Delete NodePort SSH service
  kubernetes.core.k8s:
    state: absent
    api_version: v1
    kind: Service
    name: "{{ item }}"
    namespace: "{{ _mp_specs[item].namespace }}"
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 5: Verify `roles/kubevirt/tasks/prepare.yml` is unchanged**

It does `wait_for_connection` with no platform iteration. No edit needed. Confirm by reading it — content should be a single `wait_for_connection` task using `mp_kubevirt_wait_timeout`.

- [ ] **Step 6: Lint**

Run: `ansible-lint roles/kubevirt/`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add roles/kubevirt/tasks/
git commit -m "feat(roles/kubevirt): loop over groups['molecule'], 3-level merge, trim inventory write"
```

---

### Task 6: Rewrite both roles' argument_specs

**Files:**
- Modify: `roles/podman/meta/argument_specs.yml`
- Modify: `roles/kubevirt/meta/argument_specs.yml`

`argument_specs` validates role-invocation-level inputs (vars passed to the role). Per-host validation lives in the role's task files via `assert`. The new `mp_*_role_defaults` dicts get described here so consumers reading the role's docs see them.

- [ ] **Step 1: Replace `roles/podman/meta/argument_specs.yml`**

```yaml
---
argument_specs:
  main:
    short_description: >-
      Default entry point — does nothing on its own; use tasks_from=create|destroy|prepare.
    options: {}
  create:
    short_description: Create podman containers and networks for hosts in groups['molecule'].
    options:
      mp_podman_role_defaults:
        type: dict
        description: >-
          Per-host field defaults (command, privileged, volumes, etc.).
          Layered as: this dict <- mp_defaults.podman <- hostvars[item].mp.podman.
      mp_podman_async_timeout:
        type: int
        default: 7200
        description: Async timeout (seconds) for bulk container create.
      mp_podman_async_retries:
        type: int
        default: 300
        description: Number of times to poll for async completion.
      mp_podman_async_delay:
        type: int
        default: 24
        description: >-
          Seconds between async_status polls. retries * delay should
          equal async_timeout (default 300 * 24 = 7200).
  destroy:
    short_description: Destroy podman containers and (non-reserved) networks.
    options:
      mp_podman_role_defaults:
        type: dict
      mp_podman_async_timeout:
        type: int
        default: 7200
      mp_podman_async_retries:
        type: int
        default: 300
      mp_podman_async_delay:
        type: int
        default: 24
  prepare:
    short_description: Run podman-side preparation tasks against created containers.
    options: {}
```

- [ ] **Step 2: Replace `roles/kubevirt/meta/argument_specs.yml`**

```yaml
---
argument_specs:
  main:
    short_description: >-
      Default entry point — does nothing on its own; use tasks_from=create|destroy|prepare.
    options: {}
  create:
    short_description: >-
      Create KubeVirt VirtualMachines and NodePort services for hosts in groups['molecule'].
    options:
      mp_kubevirt_role_defaults:
        type: dict
        description: >-
          Per-host field defaults (namespace, ssh_user, memory, disk_size, ssh_service.type).
          Layered as: this dict <- mp_defaults.kubevirt <- hostvars[item].mp.kubevirt.
      mp_kubevirt_ssh_key_path:
        type: path
        description: Where the SSH keypair is generated (defaults to molecule_ephemeral_directory/identity_file).
  destroy:
    short_description: Delete KubeVirt VirtualMachines and NodePort services.
    options:
      mp_kubevirt_role_defaults:
        type: dict
  prepare:
    short_description: Wait for KubeVirt-provisioned hosts to be reachable.
    options:
      mp_kubevirt_wait_timeout:
        type: int
        default: 120
```

- [ ] **Step 3: Lint**

Run: `ansible-lint roles/`
Expected: no findings.

- [ ] **Step 4: Commit**

```bash
git add roles/podman/meta/argument_specs.yml roles/kubevirt/meta/argument_specs.yml
git commit -m "feat(roles): rewrite argument_specs for v2 ansible-native shape"
```

---

### Task 7: Replace the self-test scenarios

**Files:**
- Delete: `extensions/molecule/podman/` (whole tree)
- Delete: `extensions/molecule/kubevirt/` (whole tree)
- Create: `extensions/molecule/default/molecule.yml`
- Create: `extensions/molecule/default/create.yml`
- Create: `extensions/molecule/default/destroy.yml`
- Create: `extensions/molecule/default/prepare.yml`
- Create: `extensions/molecule/default/converge.yml`
- Create: `extensions/molecule/default/verify.yml`
- Create: `extensions/molecule/default/inventory/hosts.yml`
- Create: `extensions/molecule/default/inventory/group_vars/molecule.yml`

The new scenario carries both backends' specs per host. CI runs it twice with different `PROVISIONER` values.

- [ ] **Step 1: Delete the v1 scenarios**

```bash
git rm -r extensions/molecule/podman extensions/molecule/kubevirt
```

- [ ] **Step 2: Create `extensions/molecule/default/molecule.yml`**

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

> **Implementation note:** The `--inventory=${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/` arg relies on molecule expanding env vars in `executor.args.ansible_playbook` values when constructing the playbook command. If the local self-test in Task 8 fails with "inventory not found" or the molecule_runtime.yml file is written but its hostvars don't take effect, fall back to ANSIBLE_INVENTORY. Add to molecule.yml:
> ```yaml
> ansible:
>   env:
>     ANSIBLE_INVENTORY: "inventory/:${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/"
> ```
> If that also fails, drop the second `--inventory` arg and have the role write `inventory/molecule_runtime.yml` (under the consumer's static inventory dir) on create and `git restore`-style remove it on destroy. Document the chosen mechanism in CLAUDE.md (Task 13).

- [ ] **Step 3: Create the three lifecycle one-liners**

`extensions/molecule/default/create.yml`:
```yaml
---
- name: Provision molecule instances
  import_playbook: david_igou.molecule_provisioners.create
```

`extensions/molecule/default/destroy.yml`:
```yaml
---
- name: Tear down molecule instances
  import_playbook: david_igou.molecule_provisioners.destroy
```

`extensions/molecule/default/prepare.yml`:
```yaml
---
- name: Prepare molecule instances
  import_playbook: david_igou.molecule_provisioners.prepare
```

- [ ] **Step 4: Create `extensions/molecule/default/converge.yml`**

```yaml
---
- name: Converge — verify SSH/exec works against every molecule host
  hosts: molecule
  gather_facts: true
  tasks:
    - name: Capture hostname and OS info
      ansible.builtin.debug:
        msg: "{{ ansible_hostname }} on {{ ansible_distribution }} {{ ansible_distribution_version }}"
```

- [ ] **Step 5: Create `extensions/molecule/default/verify.yml`**

```yaml
---
- name: Verify — assert every molecule host responded to gather_facts
  hosts: molecule
  gather_facts: false
  tasks:
    - name: Assert ansible_distribution is set
      ansible.builtin.assert:
        that: ansible_distribution is defined
        fail_msg: "ansible_distribution missing — host did not gather facts during converge."
```

- [ ] **Step 6: Create `extensions/molecule/default/inventory/hosts.yml`**

```yaml
---
all:
  children:
    molecule:
      hosts:
        instance-1:
          mp:
            podman:
              image: docker.io/geerlingguy/docker-fedora41-ansible:latest
            kubevirt:
              image: quay.io/kubevirt/fedora-cloud-container-disk-demo:latest
        instance-2:
          mp:
            podman:
              image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
            kubevirt:
              image: quay.io/containerdisks/ubuntu:24.04
              ssh_user: ubuntu
```

- [ ] **Step 7: Create `extensions/molecule/default/inventory/group_vars/molecule.yml`**

```yaml
---
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"

mp_defaults:
  podman:
    command: /sbin/init
    privileged: true
  kubevirt:
    namespace: "{{ lookup('env', 'MOLECULE_NAMESPACE') | default('molecule', true) }}"
    memory: 1Gi
    disk_size: 5Gi
    ssh_user: cloud-user
```

- [ ] **Step 8: Lint**

Run: `ansible-lint extensions/molecule/default/`
Expected: no findings.

- [ ] **Step 9: Commit**

```bash
git add -A extensions/molecule/
git commit -m "feat(extensions): replace per-backend scenarios with combined ansible-native scenario"
```

---

### Task 8: Run the self-test locally for both backends

This is the make-or-break end-to-end test. Both backends must pass against the new scenario before any docs/CI work.

- [ ] **Step 1: Install deps**

Run: `pip install -r requirements.txt -r test-requirements.txt`
Run: `ansible-galaxy collection install containers.podman kubernetes.core community.crypto`

- [ ] **Step 2: Symlink the repo into the canonical collection path (if not already)**

Run:
```bash
mkdir -p "$HOME/.ansible/collections/ansible_collections/david_igou"
ln -snf "$PWD" "$HOME/.ansible/collections/ansible_collections/david_igou/molecule_provisioners"
ansible-galaxy collection list | grep molecule_provisioners
```
Expected: shows `david_igou.molecule_provisioners 1.0.0`.

- [ ] **Step 3: Run the podman self-test**

Run: `PROVISIONER=podman pytest tests/integration -v -k default`
Expected: PASS. PLAY RECAP shows create/prepare/converge/verify/destroy tasks executing without error.

If FAIL: read the molecule output. Common issues:
- `groups['molecule']` undefined → static inventory not loaded → check `--inventory=inventory/` arg in molecule.yml.
- `_mp_specs[item].image` undefined → merge step didn't run → check that the role's tasks/create.yml has the `_mp_specs` initialization above any other loops.
- `ansible_connection` not applied → runtime inventory file not loaded → fall back to the alternative chain mechanism documented in Task 7's implementation note.

- [ ] **Step 4: Run the kubevirt self-test (requires `$KUBECONFIG`)**

If you have a cluster with KubeVirt: run `PROVISIONER=kubevirt pytest tests/integration -v -k default -s`.
Expected: PASS. Same PLAY RECAP shape, plus VirtualMachine + Service creation logged for each host.

If no local KubeVirt cluster: skip this step locally; it will be exercised in CI in Task 9.

- [ ] **Step 5: Commit any fixes uncovered by the self-test**

```bash
git add <changed-files>
git commit -m "fix: <concise description of the issue and fix>"
```

If no fixes needed, this step is a no-op.

---

### Task 9: Update CI workflow

**Files:**
- Modify: `.github/workflows/tests.yml`

Today: jobs `integration` (podman scenario) and `kubevirt` (kubevirt scenario). v2: `integration-podman` and `integration-kubevirt`, both running the same scenario with different `PROVISIONER`.

- [ ] **Step 1: Rename the `integration` job to `integration-podman` and update its pytest invocation**

In `.github/workflows/tests.yml`, replace the `integration:` job with:

```yaml
  integration-podman:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout into the canonical collection path
        uses: actions/checkout@v4
        with:
          path: ansible_collections/david_igou/molecule_provisioners

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install ansible + molecule + pytest plumbing
        working-directory: ansible_collections/david_igou/molecule_provisioners
        run: |
          python -m pip install --upgrade pip
          pip install ansible-core molecule \
                      pytest pytest-ansible pytest-xdist

      - name: Install collection dependencies
        working-directory: ansible_collections/david_igou/molecule_provisioners
        run: |
          ansible-galaxy collection install \
            containers.podman kubernetes.core community.crypto

      - name: Run podman integration tests
        working-directory: ansible_collections/david_igou/molecule_provisioners
        env:
          PROVISIONER: podman
          ANSIBLE_COLLECTIONS_PATH: ${{ github.workspace }}
        run: pytest tests/integration -v -k default
```

- [ ] **Step 2: Rename the `kubevirt` job to `integration-kubevirt` and update its pytest invocation**

Replace the `kubevirt:` job's "Run kubevirt scenario" step's `pytest` command with:
```bash
pytest tests/integration -v -k default -s -o addopts=""
```

And rename the job key from `kubevirt:` to `integration-kubevirt:`.

(Leave the kind setup, KubeVirt operator install, namespace creation, VM watcher, diagnostics, and "Assert a VirtualMachine existed during the run" steps unchanged.)

- [ ] **Step 3: Update `all_green.needs`**

Replace:
```yaml
needs:
  - changelog
  - build-import
  - sanity
  - unit-galaxy
  - unit-source
  - ansible-lint
  - integration
  - kubevirt
```

With:
```yaml
needs:
  - changelog
  - build-import
  - sanity
  - unit-galaxy
  - unit-source
  - ansible-lint
  - integration-podman
  - integration-kubevirt
```

And the python assertion:
```yaml
- run: >-
    python -c "assert 'failure' not in
    set([
    '${{ needs.changelog.result }}',
    '${{ needs.sanity.result }}',
    '${{ needs.unit-galaxy.result }}',
    '${{ needs.ansible-lint.result }}',
    '${{ needs.unit-source.result }}',
    '${{ needs.integration-podman.result }}',
    '${{ needs.integration-kubevirt.result }}'
    ])"
```

- [ ] **Step 4: Lint**

Run: `yamllint .github/workflows/tests.yml`
Expected: no findings.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: rename jobs to integration-{podman,kubevirt}, run combined scenario"
```

---

### Task 10: Update docs/examples/

**Files:**
- Delete: `docs/examples/platforms.yml`
- Modify: `docs/examples/molecule.yml`
- Create: `docs/examples/inventory/hosts.yml`
- Create: `docs/examples/inventory/group_vars/molecule.yml`
- (`docs/examples/{create,destroy,prepare}.yml` are unchanged — they're already FQCN one-liners.)

- [ ] **Step 1: Delete the platforms-shape example**

```bash
git rm docs/examples/platforms.yml
```

- [ ] **Step 2: Replace `docs/examples/molecule.yml` with the ansible-native shape**

```yaml
---
# Starter molecule.yml — copy into your collection's
# extensions/molecule/<scenario>/ directory.
# This file is identical for every consumer; the per-scenario variation
# lives in inventory/hosts.yml and inventory/group_vars/molecule.yml.

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

- [ ] **Step 3: Create `docs/examples/inventory/hosts.yml`**

```yaml
---
# Example inventory shape for david_igou.molecule_provisioners.
#
# Each host has both `mp.podman` and `mp.kubevirt` blocks so the same
# inventory works under either backend. Switch backends at runtime by
# setting PROVISIONER=podman|kubevirt — group_vars/molecule.yml maps
# that to mp_backend.

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

- [ ] **Step 4: Create `docs/examples/inventory/group_vars/molecule.yml`**

```yaml
---
# Backend selector. Drives which role the dispatcher includes.
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"

# Per-backend defaults shared across hosts. Per-host overrides go in hosts.yml
# under mp.<backend>.<field>. Role-shipped defaults apply when neither is set.
mp_defaults:
  podman:
    command: /sbin/init
    privileged: true
  kubevirt:
    namespace: molecule
    memory: 1Gi
    disk_size: 5Gi
    ssh_user: cloud-user
```

- [ ] **Step 5: Lint**

Run: `yamllint docs/examples/`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add -A docs/examples/
git commit -m "docs(examples): ansible-native inventory + molecule.yml starter"
```

---

### Task 11: Repurpose `docs/MIGRATION.md`

**Files:**
- Modify: `docs/MIGRATION.md`

The existing content covers migration from devhost-style `extensions/molecule/provisioners/` to a v1 platforms-shape consumer. Since v1 platforms-shape is no longer a target, the document repurposes to: "you have a molecule scenario using the upstream `platforms:` shape and want to use this collection's ansible-native shape — here's the field-by-field translation."

- [ ] **Step 1: Replace `docs/MIGRATION.md` with the content below**

```markdown
# Migrating to `david_igou.molecule_provisioners`

If you have a molecule scenario using the upstream `platforms:` + `driver:` shape (now called *pre ansible-native* in molecule's own docs) and you want to use this collection, here is the field-by-field translation.

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
    disk_size: 5Gi
    ssh_user: cloud-user
```

```yaml
# create.yml / destroy.yml / prepare.yml — one-liners using FQCN
- name: Provision molecule instances
  import_playbook: david_igou.molecule_provisioners.create
```

## Field-by-field translation

| Pre-ansible-native | Ansible-native (this collection) |
| --- | --- |
| `driver: name: default` + `options.ansible_connection_options.connection` | gone — the role writes `ansible_connection` per host into the runtime inventory |
| `platforms[].name` | inventory host name under `groups.molecule.hosts.<name>` |
| `platforms[].image` (podman) | `hostvars[<name>].mp.podman.image` |
| `platforms[].image` (kubevirt containerdisk) | `hostvars[<name>].mp.kubevirt.image` |
| `platforms[].command`, `.privileged`, `.volumes`, etc. | `hostvars[<name>].mp.podman.<field>` (or hoisted to `mp_defaults.podman` if shared) |
| `platforms[].kubevirt.namespace`, `.memory`, etc. | `hostvars[<name>].mp.kubevirt.<field>` (or hoisted to `mp_defaults.kubevirt`) |
| `provisioner.name: ansible` + `provisioner.playbooks.*` | `ansible.playbooks.*` |
| `provisioner.env.PROVISIONER` | `mp_backend` group var (this collection populates from `lookup('env', 'PROVISIONER')` in the example boilerplate, but the contract is `mp_backend`, not the env var name) |

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
```

- [ ] **Step 2: Lint**

Run: `prettier --check docs/MIGRATION.md`
Expected: no findings (or run `prettier --write` to auto-format).

- [ ] **Step 3: Commit**

```bash
git add docs/MIGRATION.md
git commit -m "docs(migration): rewrite for pre-ansible-native -> v2 ansible-native shape"
```

---

### Task 12: Update both role READMEs

**Files:**
- Modify: `roles/podman/README.md`
- Modify: `roles/kubevirt/README.md`

- [ ] **Step 1: Replace `roles/podman/README.md`**

```markdown
# `david_igou.molecule_provisioners.podman`

Molecule provisioner role for podman containers. Not invoked directly — invoked via the collection's top-level `playbooks/{create,destroy,prepare}.yml` dispatchers, which read `mp_backend` from the molecule group's hostvars.

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Computes per-host merged specs, creates user-defined podman networks, then creates containers from `hostvars[item].mp.podman.*` for each host in `groups['molecule']`. Writes `ansible_connection: containers.podman.podman` per host into the runtime inventory file. |
| `destroy` | Removes those containers and any non-reserved networks. |
| `prepare` | Installs `sudo` inside each container. |

## Inputs (per-host, in inventory)

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            podman:
              image: docker.io/...:tag         # required
              command: /sbin/init              # optional, role default '/sbin/init'
              privileged: false                # optional, role default false
              volumes: []                      # optional
              capabilities: []                 # optional
              podman_network: []               # optional, list or single string
              env: {}                          # optional
              tmpfs: []                        # optional
              exposed_ports: []                # optional
              published_ports: []              # optional
```

Shared defaults can be hoisted into `mp_defaults.podman` in `inventory/group_vars/molecule.yml` (overrides role defaults; per-host fields override mp_defaults). Field resolution order in the role: role defaults <- `mp_defaults.podman` <- `hostvars[item].mp.podman`.

## Role-level overrides

See `defaults/main.yml` (`mp_podman_role_defaults`, `mp_podman_async_*`, `mp_podman_reserved_networks`).
```

- [ ] **Step 2: Replace `roles/kubevirt/README.md`**

```markdown
# `david_igou.molecule_provisioners.kubevirt`

Molecule provisioner role for KubeVirt VMs. Not invoked directly — invoked via the collection's top-level `playbooks/{create,destroy,prepare}.yml` dispatchers, which read `mp_backend` from the molecule group's hostvars.

Requires:
- A reachable Kubernetes cluster with KubeVirt installed and a working `KUBECONFIG`.
- `kubernetes.core` and `community.crypto` collections (declared in `galaxy.yml`).

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Computes per-host merged specs, generates an SSH keypair, creates VirtualMachine + NodePort Service per host in `groups['molecule']`, writes runtime connection details (`ansible_host`, `ansible_port`, etc.) into the runtime inventory file. |
| `destroy` | Deletes VirtualMachine and NodePort Service per host. |
| `prepare` | `wait_for_connection` against each created host. |

## Inputs (per-host, in inventory)

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            kubevirt:
              image: quay.io/containerdisks/ubuntu:24.04   # required, containerdisk image
              namespace: molecule                          # optional, role default 'molecule'
              ssh_user: cloud-user                         # optional, role default 'cloud-user'
              memory: 1Gi                                  # optional, role default '1Gi'
              disk_size: 5Gi                               # optional, role default '5Gi'
              ssh_service:
                type: NodePort                             # optional, only NodePort supported in v1
```

Shared defaults can be hoisted into `mp_defaults.kubevirt` in `inventory/group_vars/molecule.yml`. Field resolution order: role defaults <- `mp_defaults.kubevirt` <- `hostvars[item].mp.kubevirt`.

## Role-level overrides

See `defaults/main.yml` (`mp_kubevirt_role_defaults`, `mp_kubevirt_ssh_key_path`, `mp_kubevirt_wait_timeout`, `mp_kubevirt_allowed_ssh_service_types`).

## v1 limitation

Only `ssh_service.type: NodePort` is supported. The role asserts this on create. LoadBalancer / ClusterIP+port-forward are out of scope for v1.
```

- [ ] **Step 3: Lint**

Run: `prettier --check roles/podman/README.md roles/kubevirt/README.md`
Expected: no findings.

- [ ] **Step 4: Commit**

```bash
git add roles/podman/README.md roles/kubevirt/README.md
git commit -m "docs(roles): rewrite README for ansible-native shape"
```

---

### Task 13: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

The current CLAUDE.md describes v1 platforms-shape semantics in several places. Update Architecture, Key files, Common commands, Public contract, and "When updating provisioner logic" sections. Leave the "Do not depend on `molecule-plugins`" section unchanged.

- [ ] **Step 1: Replace the "Architecture (one-paragraph version)" section**

Find:
```markdown
## Architecture (one-paragraph version)

Three top-level dispatcher playbooks (`playbooks/{create,destroy,prepare}.yml`) read `$PROVISIONER` (default `podman`), validate platform shape, and `include_role` into one of two roles (`roles/podman`, `roles/kubevirt`). Each role uses `tasks_from` for lifecycle dispatch. Consumers' scenario `create.yml`/`destroy.yml`/`prepare.yml` are one-liners that `import_playbook: david_igou.molecule_provisioners.<phase>`. Public contract: each platform in `molecule.yml` has multi-keyed `podman:` and/or `kubevirt:` blocks; same `molecule.yml` works under either backend by switching `$PROVISIONER`.
```

Replace with:
```markdown
## Architecture (one-paragraph version)

Three top-level dispatcher playbooks (`playbooks/{create,destroy,prepare}.yml`) read `mp_backend` from the molecule group's hostvars (`hostvars[groups['molecule'][0]].mp_backend`), validate the inventory shape, and `include_role` into one of two roles (`roles/podman`, `roles/kubevirt`). Each role uses `tasks_from` for lifecycle dispatch and starts with a 3-level merge (role defaults <- `mp_defaults.<backend>` <- `hostvars[item].mp.<backend>`) before looping `groups['molecule']`. Consumers' scenario `create.yml`/`destroy.yml`/`prepare.yml` are one-liners that `import_playbook: david_igou.molecule_provisioners.<phase>`. The molecule.yml itself uses molecule's ansible-native shape (`ansible:` block — no `driver:`, no `platforms:`, no `provisioner:`).
```

- [ ] **Step 2: Replace the "Key files" section**

Find:
```markdown
- `playbooks/{create,destroy,prepare}.yml` — dispatcher entry points; the `import_playbook` targets that consumers reference by FQCN.
- `roles/podman/tasks/{create,destroy,prepare,_networks}.yml` — podman lifecycle. `_networks.yml` is shared between create and destroy.
- `roles/kubevirt/tasks/{create,destroy,prepare,_create_vm,_create_vm_dictionary}.yml` — kubevirt lifecycle. `_create_vm*.yml` are per-platform helpers included in a loop with `loop_var: vm`.
- `extensions/molecule/{podman,kubevirt}/` — self-test scenarios. Discovered by `pytest_ansible.molecule_scenario` fixture in `tests/integration/test_integration.py`. The kubevirt scenario is cluster-agnostic — it talks to whatever `KUBECONFIG` points at, as long as KubeVirt is installed there. CI provisions kind + KubeVirt with `useEmulation` before running it.
- `docs/examples/` — copy-paste starter for consumers.
- `docs/MIGRATION.md` — converting devhost-style consumers.
```

Replace with:
```markdown
- `playbooks/{create,destroy,prepare}.yml` — dispatcher entry points; the `import_playbook` targets that consumers reference by FQCN.
- `playbooks/group_vars/all.yml` — declares `mp_supported_backends`.
- `roles/podman/tasks/{create,destroy,prepare,_networks}.yml` — podman lifecycle. `_networks.yml` is shared between create and destroy.
- `roles/kubevirt/tasks/{create,destroy,prepare,_create_vm,_create_vm_dictionary}.yml` — kubevirt lifecycle. `_create_vm*.yml` are per-host helpers included in a loop over `groups['molecule']`.
- `roles/<backend>/defaults/main.yml` — role-level defaults including the `mp_<backend>_role_defaults` dict that feeds the merge.
- `extensions/molecule/default/` — single self-test scenario carrying both backends' specs per host. Discovered by `pytest_ansible.molecule_scenario` fixture in `tests/integration/test_integration.py`. The kubevirt-backend run is cluster-agnostic — it talks to whatever `KUBECONFIG` points at, as long as KubeVirt is installed there. CI provisions kind + KubeVirt with `useEmulation` before running it.
- `docs/examples/` — copy-paste starter for consumers (`molecule.yml` boilerplate + `inventory/` shape).
- `docs/MIGRATION.md` — translating from molecule's pre-ansible-native `platforms:` shape to this collection.
```

- [ ] **Step 3: Replace the "Common commands" table**

Find:
```markdown
| Run podman self-test | `pytest tests/integration -v -k podman` |
| Run kubevirt self-test (requires `$KUBECONFIG` pointing at a cluster with KubeVirt) | `pytest tests/integration -v -k kubevirt` |
| Run a single scenario directly | `cd extensions/molecule/<podman\|kubevirt> && molecule test` |
```

Replace with:
```markdown
| Run podman self-test | `PROVISIONER=podman pytest tests/integration -v -k default` |
| Run kubevirt self-test (requires `$KUBECONFIG` pointing at a cluster with KubeVirt) | `PROVISIONER=kubevirt pytest tests/integration -v -k default` |
| Run a single scenario directly | `cd extensions/molecule/default && PROVISIONER=<backend> molecule test` |
```

- [ ] **Step 4: Replace the "Public contract" section**

Find the entire section starting with `## Public contract (the thing we don't break without a major bump)` through the next `##` header.

Replace with:
```markdown
## Public contract (the thing we don't break without a major bump)

The inventory shape consumers ship:

```yaml
all:
  children:
    molecule:
      hosts:
        <name>:
          mp:
            podman:                     # required when mp_backend == podman
              image: <str>              # required
              # optional: command, privileged, volumes, capabilities,
              # podman_network, env, tmpfs, exposed_ports, published_ports
            kubevirt:                   # required when mp_backend == kubevirt
              image: <str>              # required (containerdisk)
              namespace: <str>          # optional, role default 'molecule'
              ssh_user: <str>           # optional, role default 'cloud-user'
              memory: <str>             # optional, role default '1Gi'
              disk_size: <str>          # optional, role default '5Gi'
              ssh_service:
                type: NodePort          # optional, only NodePort in v1
```

Plus:
- `inventory/group_vars/molecule.yml` must define `mp_backend` (one of `mp_supported_backends`).
- `mp_defaults.<backend>.<field>` is an optional group-var layer between role defaults and per-host hostvars.
- `molecule.yml` uses molecule's ansible-native shape (`ansible:` block).

Breaking changes to the above keys → major version bump. New optional fields → minor.
```

- [ ] **Step 5: Replace the "When updating provisioner logic" section**

Find:
```markdown
## When updating provisioner logic

1. Make changes in the role (`roles/<backend>/tasks/`).
2. Run `ansible-lint roles/<backend>/`.
3. Run the self-test scenario: `cd extensions/molecule/<backend> && molecule test`.
4. If the change affects the platform schema, also update:
   - `roles/<backend>/meta/argument_specs.yml`
   - `roles/<backend>/README.md`
   - `docs/examples/platforms.yml`
   - the schema section above
```

Replace with:
```markdown
## When updating provisioner logic

1. Make changes in the role (`roles/<backend>/tasks/`).
2. Run `ansible-lint roles/<backend>/`.
3. Run the self-test scenario: `cd extensions/molecule/default && PROVISIONER=<backend> molecule test`.
4. If the change affects the per-host schema, also update:
   - `roles/<backend>/defaults/main.yml` (`mp_<backend>_role_defaults`)
   - `roles/<backend>/meta/argument_specs.yml`
   - `roles/<backend>/README.md`
   - `docs/examples/inventory/hosts.yml` (and `group_vars/molecule.yml` if a default value moves)
   - the schema section above
```

- [ ] **Step 6: Lint**

Run: `prettier --check CLAUDE.md`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): update for ansible-native shape (architecture, contract, commands)"
```

---

### Task 14: Add changelog fragment

**Files:**
- Modify: `changelogs/fragments/v1.0.0.yml` (already exists; rewrite)

The existing fragment describes a v1.0 release that selects backend via `$PROVISIONER`. Update to describe the v1.0 ansible-native shape that actually ships.

- [ ] **Step 1: Replace `changelogs/fragments/v1.0.0.yml`**

```yaml
---
release_summary: |
  Initial release. Reusable Molecule provisioner playbooks and roles
  for podman and kubevirt backends. Consumers describe test instances
  in inventory/hosts.yml using nested ``mp.<backend>.<field>`` hostvars
  and select the backend via the ``mp_backend`` group var (typically
  driven from the ``$PROVISIONER`` env var). Scenario lifecycle files
  become one-line ``import_playbook`` wrappers using FQCNs. The
  molecule.yml itself uses molecule's ansible-native shape — no
  ``driver:``, ``platforms:``, or ``provisioner:`` blocks.
major_changes:
  - Initial v1.0.0 release using molecule's ansible-native config shape.
```

- [ ] **Step 2: Lint**

Run: `yamllint changelogs/fragments/v1.0.0.yml`
Expected: no findings.

- [ ] **Step 3: Commit**

```bash
git add changelogs/fragments/v1.0.0.yml
git commit -m "changelog: rewrite v1.0.0 fragment for ansible-native shape"
```

---

### Task 15: Push and verify CI

- [ ] **Step 1: Push to `main`**

```bash
git push origin main
```

(If pre-commit's `no-commit-to-branch` blocks, the user has previously authorized `--no-verify` for this branch's documentation/CI work — confirm before bypassing.)

- [ ] **Step 2: Watch the CI run**

Run: `gh run watch` (or `gh run list --limit 1` to find the run id, then `gh run view <id> --log`)
Expected:
- `changelog`, `build-import`, `ansible-lint`, `sanity`, `unit-galaxy`, `unit-source` pass
- `integration-podman` PLAY RECAPs show create/prepare/converge/verify/destroy succeeding under `PROVISIONER=podman`
- `integration-kubevirt` PLAY RECAPs same under `PROVISIONER=kubevirt`; the "Assert a VirtualMachine existed during the run" guard passes
- `all_green` passes

- [ ] **Step 3: If CI fails, debug**

Read the failing job's log. Common kubevirt-side issues:
- `groups['molecule']` undefined in the dispatcher → consumer's `inventory/hosts.yml` not loaded → confirm `--inventory=inventory/` arg in molecule.yml, and that `pytest_ansible.molecule_scenario` is invoking molecule with the scenario's CWD.
- VirtualMachine never reaches Running → fall back to addopts override; check VM watcher trace and virt-launcher logs.

If the env-var-in-arg expansion turns out not to work in CI's molecule version: switch to the ANSIBLE_INVENTORY fallback documented in Task 7.

Fix, commit, push. Repeat until green.

---

## Self-Review (run by the engineer executing this plan)

After all tasks pass:

1. **Spec coverage:** every section of `docs/superpowers/specs/2026-05-08-ansible-native-conversion-design.md` is implemented by tasks above.
2. **No platforms-shape leftovers:** `grep -r "molecule_yml.platforms" .` returns nothing.
3. **No `$PROVISIONER` env-var reads in dispatchers:** `grep -r "PROVISIONER" playbooks/` returns nothing (the example boilerplate in `extensions/molecule/default/inventory/group_vars/molecule.yml` and `docs/examples/inventory/group_vars/molecule.yml` is the only place that mentions it, and that's intentional).
4. **Self-test runs both backends:** the GitHub Actions run shows two integration jobs, both green.
5. **Public contract is documented:** `CLAUDE.md` and the role READMEs describe the `mp.<backend>.*` schema with the same field names and required/optional split.
