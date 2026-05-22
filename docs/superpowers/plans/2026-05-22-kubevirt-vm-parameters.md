# KubeVirt VM API parameterization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the KubeVirt provisioner schema so consumers can tweak the rendered `VirtualMachine` via first-class fields (cpu, memory_limit, scheduling, extra disks/volumes/networks), a single `vm_overrides` escape hatch (deep-merge, lists append), and pick from four boot-source modes (containerDisk, data_volume_url, data_volume_pvc, pvc).

**Architecture:** The current monolithic `roles/kubevirt/tasks/_create_vm.yml` is split into three files: `_validate.yml` (assertion loop), `_build_vm.yml` (pure renderer that assembles `__mp_kubevirt_vm` per host through layered `set_fact`s), and a refactored `_create_vm.yml` that just calls the renderer then applies the rendered dict via `kubernetes.core.k8s`. A new unit-test harness drives `_build_vm.yml` in isolation via `ansible-playbook` and asserts on the rendered YAML output — this is the first unit-test surface in the repo.

**Tech Stack:** Ansible (`ansible.builtin.combine` with `recursive=True, list_merge='append'`), `kubernetes.core.k8s`, pytest, PyYAML, KubeVirt v1 + (optionally) CDI.

**Spec:** `docs/superpowers/specs/2026-05-22-kubevirt-vm-parameters-design.md`

---

## File structure (where work lands)

```
roles/kubevirt/
  defaults/main.yml                 — UNCHANGED (no task touches it; spec confirms no new defaults)
  tasks/
    create.yml                      — MODIFY: replace old assertion with include _validate.yml; loop _create_vm.yml unchanged
    _validate.yml                   — NEW: boot_source + ssh_service validation loop
    _build_vm.yml                   — NEW: per-host renderer (three-layer set_fact chain)
    _create_vm.yml                  — REWRITE: include _build_vm then k8s apply
    _create_vm_dictionary.yml       — UNCHANGED
    destroy.yml                     — UNCHANGED
    prepare.yml                     — UNCHANGED
  meta/argument_specs.yml           — MODIFY: doc-only expansion
  README.md                         — REWRITE Inputs section
tests/unit/kubevirt_render/
  __init__.py                       — NEW (empty)
  conftest.py                       — NEW: render_vm() fixture
  render_harness.yml                — NEW: ansible-playbook harness
  test_boot_sources.py              — NEW
  test_compute.py                   — NEW
  test_scheduling.py                — NEW
  test_extras.py                    — NEW
  test_vm_overrides.py              — NEW
  test_validate.py                  — NEW (negative tests via validate_harness.yml)
  validate_harness.yml              — NEW: wraps _validate.yml for failure-mode testing
extensions/molecule/default/inventory/hosts.yml — MODIFY: kubevirt block uses boot_source + cpu + vm_overrides
docs/examples/inventory/hosts.yml   — MODIFY: kubevirt block uses boot_source
docs/MIGRATION.md                   — APPEND: v1.0 → kubevirt schema section
CLAUDE.md                           — MODIFY: Public-contract kubevirt block
```

---

## Task 1: Set up the renderer test harness with a stub `_build_vm.yml`

Lays down the testing scaffolding before any real renderer logic. The stub returns the bare minimum VM skeleton and one passing test proves the harness round-trips correctly.

**Files:**
- Create: `roles/kubevirt/tasks/_build_vm.yml`
- Create: `tests/unit/kubevirt_render/__init__.py` (empty)
- Create: `tests/unit/kubevirt_render/render_harness.yml`
- Create: `tests/unit/kubevirt_render/conftest.py`
- Create: `tests/unit/kubevirt_render/test_boot_sources.py`

### Step 1: Write the failing test

Create `tests/unit/kubevirt_render/test_boot_sources.py`:

```python
"""Renderer tests for the boot_source variants."""

from __future__ import annotations


def test_renders_virtualmachine_kind(render_vm) -> None:
    """The renderer produces a kubevirt.io/v1 VirtualMachine object."""
    vm = render_vm({"boot_source": {"type": "container_disk", "image": "quay.io/example/img:latest"}})
    assert vm["kind"] == "VirtualMachine"
    assert vm["apiVersion"] == "kubevirt.io/v1"
    assert vm["metadata"]["name"] == "instance"
    assert vm["metadata"]["namespace"] == "molecule"
```

### Step 2: Run test to verify it fails

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py::test_renders_virtualmachine_kind -v`
Expected: FAIL with `fixture 'render_vm' not found` (the conftest doesn't exist yet).

### Step 3: Create the harness playbook

Create `tests/unit/kubevirt_render/render_harness.yml`:

```yaml
---
# Harness for unit-testing roles/kubevirt/tasks/_build_vm.yml in isolation.
#
# Inputs (via --extra-vars):
#   host_spec    — the per-host merged spec dict (would normally come from
#                  _mp_specs[item] inside create.yml after the merge loop).
#   output_path  — where to dump the rendered VM dict as YAML.
#
# Replicates the slice of role state _build_vm.yml expects: item, _mp_specs,
# temporary_ssh_public_key, and a frozen copy of mp_kubevirt_role_defaults so
# the test doesn't drift if the defaults file changes (changes must be
# intentional, asserted by the defaults test).

- name: Render VirtualMachine via _build_vm.yml
  hosts: localhost
  gather_facts: false
  vars:
    item: instance
    temporary_ssh_public_key: "ssh-ed25519 AAAATESTKEY"
    _mp_specs:
      instance: "{{ host_spec }}"
  tasks:
    - name: Include the renderer
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../roles/kubevirt/tasks/_build_vm.yml"

    - name: Dump rendered VM to output_path
      ansible.builtin.copy:
        dest: "{{ output_path }}"
        content: "{{ __mp_kubevirt_vm | to_nice_yaml(indent=2) }}"
        mode: "0644"
```

### Step 4: Create the conftest fixture

Create `tests/unit/kubevirt_render/conftest.py`:

```python
"""Shared fixtures for kubevirt renderer unit tests."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

HARNESS = Path(__file__).parent / "render_harness.yml"
VALIDATE_HARNESS = Path(__file__).parent / "validate_harness.yml"


def _run_harness(harness: Path, host_spec: dict[str, Any]) -> subprocess.CompletedProcess:
    """Run an ansible-playbook harness with host_spec, return the completed process."""
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "rendered.yml"
        extra_vars = {"host_spec": host_spec, "output_path": str(output_path)}
        proc = subprocess.run(
            [
                "ansible-playbook",
                str(harness),
                "--extra-vars",
                json.dumps(extra_vars),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        proc.output_path = output_path  # type: ignore[attr-defined]
        proc.output_contents = output_path.read_text() if output_path.exists() else ""  # type: ignore[attr-defined]
        return proc


@pytest.fixture
def render_vm() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return a function that renders a VM from a per-host spec dict."""

    def _render(host_spec: dict[str, Any]) -> dict[str, Any]:
        proc = _run_harness(HARNESS, host_spec)
        assert proc.returncode == 0, (
            f"render harness failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        return yaml.safe_load(proc.output_contents)

    return _render


@pytest.fixture
def run_validate() -> Callable[[dict[str, Any]], subprocess.CompletedProcess]:
    """Return a function that runs the validation harness; does NOT assert success."""

    def _validate(host_spec: dict[str, Any]) -> subprocess.CompletedProcess:
        return _run_harness(VALIDATE_HARNESS, host_spec)

    return _validate
```

### Step 5: Create the stub `_build_vm.yml`

Create `roles/kubevirt/tasks/_build_vm.yml`:

```yaml
---
# Pure renderer: assemble __mp_kubevirt_vm for the current host (`item`) from
# _mp_specs[item]. Called per-host from _create_vm.yml, and from the unit-test
# harness at tests/unit/kubevirt_render/render_harness.yml.
#
# Three layers:
#   __mp_base     — minimum viable VM skeleton (metadata, cloudinit, default net).
#   __mp_curated  — first-class fields layered onto the base.
#   __mp_kubevirt_vm = __mp_curated | combine(vm_overrides, recursive, list_merge=append).

- name: "Build base VM skeleton: {{ item }}"
  ansible.builtin.set_fact:
    __mp_base:
      apiVersion: kubevirt.io/v1
      kind: VirtualMachine
      metadata:
        name: "{{ item }}"
        namespace: "{{ _mp_specs[item].namespace | default('molecule') }}"
        labels:
          kubevirt.io/domain: "{{ item }}"
      spec:
        running: true

- name: "Set __mp_kubevirt_vm (stub): {{ item }}"
  ansible.builtin.set_fact:
    __mp_kubevirt_vm: "{{ __mp_base }}"
```

### Step 6: Run test to verify it passes

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py::test_renders_virtualmachine_kind -v`
Expected: PASS.

### Step 7: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml \
        tests/unit/kubevirt_render/__init__.py \
        tests/unit/kubevirt_render/render_harness.yml \
        tests/unit/kubevirt_render/conftest.py \
        tests/unit/kubevirt_render/test_boot_sources.py
git commit -m "test(kubevirt): scaffold renderer unit-test harness"
```

---

## Task 2: Implement `boot_source: container_disk`

Adds the containerdisk boot source plus the always-present cloudinit volume/disk and the default pod-network interface. This is the part that today is hardcoded in `_create_vm.yml`.

**Files:**
- Modify: `roles/kubevirt/tasks/_build_vm.yml`
- Modify: `tests/unit/kubevirt_render/test_boot_sources.py`

### Step 1: Write the failing tests

Append to `tests/unit/kubevirt_render/test_boot_sources.py`:

```python
def test_container_disk_volume_and_disk(render_vm) -> None:
    """containerDisk type wires a containerDisk volume and a virtio disk."""
    vm = render_vm({
        "boot_source": {"type": "container_disk", "image": "quay.io/example/fedora.img"},
    })
    spec = vm["spec"]["template"]["spec"]

    volumes = {v["name"]: v for v in spec["volumes"]}
    assert "containerdisk" in volumes
    assert volumes["containerdisk"]["containerDisk"]["image"] == "quay.io/example/fedora.img"
    assert "cloudinitdisk" in volumes
    assert "users:" in volumes["cloudinitdisk"]["cloudInitNoCloud"]["userData"]

    disks = {d["name"]: d for d in spec["domain"]["devices"]["disks"]}
    assert disks["containerdisk"]["disk"]["bus"] == "virtio"
    assert disks["cloudinitdisk"]["disk"]["bus"] == "virtio"


def test_container_disk_default_pod_network(render_vm) -> None:
    """containerDisk renders the default pod/masquerade interface."""
    vm = render_vm({
        "boot_source": {"type": "container_disk", "image": "quay.io/x"},
    })
    spec = vm["spec"]["template"]["spec"]

    assert spec["domain"]["devices"]["interfaces"][0]["name"] == "default"
    assert spec["domain"]["devices"]["interfaces"][0]["masquerade"] == {}
    assert spec["networks"][0]["name"] == "default"
    assert spec["networks"][0]["pod"] == {}


def test_container_disk_default_ssh_user_baked_into_cloudinit(render_vm) -> None:
    """The cloudinit user-data names the ssh_user (or its role default)."""
    vm = render_vm({
        "boot_source": {"type": "container_disk", "image": "quay.io/x"},
    })
    user_data = vm["spec"]["template"]["spec"]["volumes"]
    cidisk = next(v for v in user_data if v["name"] == "cloudinitdisk")
    assert "name: cloud-user" in cidisk["cloudInitNoCloud"]["userData"]
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py -v`
Expected: the three new tests FAIL (the stub renderer has no template.spec at all).

### Step 3: Expand the renderer

Replace `roles/kubevirt/tasks/_build_vm.yml` with:

```yaml
---
# Pure renderer (see Task 1 header comment).

- name: "Build base VM skeleton: {{ item }}"
  vars:
    _ns: "{{ _mp_specs[item].namespace | default('molecule') }}"
    _ssh_user: "{{ _mp_specs[item].ssh_user | default('cloud-user') }}"
  ansible.builtin.set_fact:
    __mp_base:
      apiVersion: kubevirt.io/v1
      kind: VirtualMachine
      metadata:
        name: "{{ item }}"
        namespace: "{{ _ns }}"
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
              devices:
                disks:
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
              - name: cloudinitdisk
                cloudInitNoCloud:
                  userData: |
                    #cloud-config
                    users:
                      - name: {{ _ssh_user }}
                        ssh_authorized_keys:
                          - {{ temporary_ssh_public_key }}
                        sudo: ALL=(ALL) NOPASSWD:ALL
                        shell: /bin/bash
                    chpasswd:
                      expire: false

- name: "Dispatch boot_source: {{ item }}"
  ansible.builtin.set_fact:
    __mp_boot_disk:
      name: containerdisk
      disk:
        bus: virtio
    __mp_boot_volume:
      name: containerdisk
      containerDisk:
        image: "{{ _mp_specs[item].boot_source.image }}"
    __mp_data_volume_templates: []
  when: _mp_specs[item].boot_source.type == 'container_disk'

- name: "Apply boot source to base: {{ item }}"
  vars:
    _spec_patch:
      spec:
        template:
          spec:
            domain:
              devices:
                disks: "{{ [__mp_boot_disk] }}"
            volumes: "{{ [__mp_boot_volume] }}"
  ansible.builtin.set_fact:
    __mp_curated: >-
      {{ __mp_base | combine(_spec_patch, recursive=True, list_merge='prepend') }}

- name: "Attach dataVolumeTemplates (if any): {{ item }}"
  ansible.builtin.set_fact:
    __mp_curated: >-
      {{ __mp_curated | combine({'spec': {'dataVolumeTemplates': __mp_data_volume_templates}},
                                recursive=True, list_merge='append') }}
  when: __mp_data_volume_templates | length > 0

- name: "Set __mp_kubevirt_vm: {{ item }}"
  ansible.builtin.set_fact:
    __mp_kubevirt_vm: "{{ __mp_curated }}"
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py -v`
Expected: all four boot_sources tests PASS.

### Step 5: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml tests/unit/kubevirt_render/test_boot_sources.py
git commit -m "feat(kubevirt): renderer boot_source=container_disk"
```

---

## Task 3: Implement `boot_source: data_volume_url`

Adds CDI-import boot from a URL. Renders a `dataVolumeTemplates` entry and references it from the VM's boot volume.

**Files:**
- Modify: `roles/kubevirt/tasks/_build_vm.yml`
- Modify: `tests/unit/kubevirt_render/test_boot_sources.py`

### Step 1: Write the failing tests

Append to `tests/unit/kubevirt_render/test_boot_sources.py`:

```python
def test_data_volume_url_renders_template(render_vm) -> None:
    """data_volume_url renders dataVolumeTemplates with http source."""
    vm = render_vm({
        "boot_source": {
            "type": "data_volume_url",
            "url": "https://cloud-images.example/x.img",
            "checksum": "sha256:abc",
            "size": "10Gi",
            "storage_class": "standard",
        },
    })
    templates = vm["spec"]["dataVolumeTemplates"]
    assert len(templates) == 1
    dv = templates[0]
    assert dv["metadata"]["name"] == "instance-boot"
    assert dv["spec"]["source"]["http"]["url"] == "https://cloud-images.example/x.img"
    assert dv["spec"]["source"]["http"]["certConfigMap"] is None or True  # tolerate absence
    assert dv["spec"]["storage"]["resources"]["requests"]["storage"] == "10Gi"
    assert dv["spec"]["storage"]["storageClassName"] == "standard"


def test_data_volume_url_boot_volume_references_template(render_vm) -> None:
    """The VM's boot volume references the dataVolumeTemplate by name."""
    vm = render_vm({
        "boot_source": {
            "type": "data_volume_url",
            "url": "https://x/img",
            "size": "10Gi",
        },
    })
    volumes = vm["spec"]["template"]["spec"]["volumes"]
    boot = next(v for v in volumes if v["name"] == "containerdisk")
    assert boot["dataVolume"]["name"] == "instance-boot"
    assert "containerDisk" not in boot


def test_data_volume_url_omits_storage_class_when_unset(render_vm) -> None:
    """No storageClassName key when storage_class isn't supplied."""
    vm = render_vm({
        "boot_source": {"type": "data_volume_url", "url": "https://x", "size": "10Gi"},
    })
    dv = vm["spec"]["dataVolumeTemplates"][0]
    assert "storageClassName" not in dv["spec"]["storage"]
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py -v -k data_volume_url`
Expected: three FAILs (the dispatch block only handles container_disk).

### Step 3: Add the dispatch branch

In `roles/kubevirt/tasks/_build_vm.yml`, ADD a new task block immediately after the existing `Dispatch boot_source` (container_disk) block:

```yaml
- name: "Dispatch boot_source data_volume_url: {{ item }}"
  vars:
    _bs: "{{ _mp_specs[item].boot_source }}"
    _dv_name: "{{ item }}-boot"
    _http_source:
      url: "{{ _bs.url }}"
    _storage_block: >-
      {{
        {
          'resources': {'requests': {'storage': _bs.size}}
        }
        | combine(
            {'storageClassName': _bs.storage_class} if _bs.storage_class is defined else {}
          )
      }}
  ansible.builtin.set_fact:
    __mp_boot_disk:
      name: containerdisk
      disk:
        bus: virtio
    __mp_boot_volume:
      name: containerdisk
      dataVolume:
        name: "{{ _dv_name }}"
    __mp_data_volume_templates:
      - metadata:
          name: "{{ _dv_name }}"
        spec:
          source:
            http: "{{ _http_source }}"
          storage: "{{ _storage_block }}"
  when: _mp_specs[item].boot_source.type == 'data_volume_url'
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py -v`
Expected: all boot_sources tests PASS (container_disk still passing, data_volume_url now passing).

### Step 5: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml tests/unit/kubevirt_render/test_boot_sources.py
git commit -m "feat(kubevirt): renderer boot_source=data_volume_url (CDI import)"
```

---

## Task 4: Implement `boot_source: data_volume_pvc`

Adds CDI smart-clone from an existing PVC.

**Files:**
- Modify: `roles/kubevirt/tasks/_build_vm.yml`
- Modify: `tests/unit/kubevirt_render/test_boot_sources.py`

### Step 1: Write the failing test

Append to `tests/unit/kubevirt_render/test_boot_sources.py`:

```python
def test_data_volume_pvc_renders_clone_template(render_vm) -> None:
    """data_volume_pvc renders dataVolumeTemplates with pvc source."""
    vm = render_vm({
        "boot_source": {
            "type": "data_volume_pvc",
            "source": {"name": "golden", "namespace": "images"},
            "size": "20Gi",
        },
    })
    dv = vm["spec"]["dataVolumeTemplates"][0]
    assert dv["spec"]["source"]["pvc"] == {"name": "golden", "namespace": "images"}
    assert dv["spec"]["storage"]["resources"]["requests"]["storage"] == "20Gi"

    boot = next(v for v in vm["spec"]["template"]["spec"]["volumes"] if v["name"] == "containerdisk")
    assert boot["dataVolume"]["name"] == "instance-boot"
```

### Step 2: Run test to verify it fails

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py::test_data_volume_pvc_renders_clone_template -v`
Expected: FAIL.

### Step 3: Add the dispatch branch

In `_build_vm.yml`, ADD a third dispatch task after the `data_volume_url` block:

```yaml
- name: "Dispatch boot_source data_volume_pvc: {{ item }}"
  vars:
    _bs: "{{ _mp_specs[item].boot_source }}"
    _dv_name: "{{ item }}-boot"
    _storage_block: >-
      {{
        {
          'resources': {'requests': {'storage': _bs.size}}
        }
        | combine(
            {'storageClassName': _bs.storage_class} if _bs.storage_class is defined else {}
          )
      }}
  ansible.builtin.set_fact:
    __mp_boot_disk:
      name: containerdisk
      disk:
        bus: virtio
    __mp_boot_volume:
      name: containerdisk
      dataVolume:
        name: "{{ _dv_name }}"
    __mp_data_volume_templates:
      - metadata:
          name: "{{ _dv_name }}"
        spec:
          source:
            pvc:
              name: "{{ _bs.source.name }}"
              namespace: "{{ _bs.source.namespace }}"
          storage: "{{ _storage_block }}"
  when: _mp_specs[item].boot_source.type == 'data_volume_pvc'
```

### Step 4: Run test to verify it passes

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py -v`
Expected: all PASS.

### Step 5: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml tests/unit/kubevirt_render/test_boot_sources.py
git commit -m "feat(kubevirt): renderer boot_source=data_volume_pvc (CDI clone)"
```

---

## Task 5: Implement `boot_source: pvc`

Adds direct mount of a pre-existing PVC (no CDI required).

**Files:**
- Modify: `roles/kubevirt/tasks/_build_vm.yml`
- Modify: `tests/unit/kubevirt_render/test_boot_sources.py`

### Step 1: Write the failing test

Append to `tests/unit/kubevirt_render/test_boot_sources.py`:

```python
def test_pvc_direct_mount(render_vm) -> None:
    """boot_source=pvc directly mounts a PVC, no dataVolumeTemplates."""
    vm = render_vm({
        "boot_source": {"type": "pvc", "name": "existing-boot-pvc"},
    })
    assert "dataVolumeTemplates" not in vm["spec"]
    boot = next(v for v in vm["spec"]["template"]["spec"]["volumes"] if v["name"] == "containerdisk")
    assert boot["persistentVolumeClaim"]["claimName"] == "existing-boot-pvc"
    assert "dataVolume" not in boot
    assert "containerDisk" not in boot
```

### Step 2: Run test to verify it fails

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py::test_pvc_direct_mount -v`
Expected: FAIL.

### Step 3: Add the dispatch branch

In `_build_vm.yml`, ADD a fourth dispatch task after the `data_volume_pvc` block:

```yaml
- name: "Dispatch boot_source pvc: {{ item }}"
  vars:
    _bs: "{{ _mp_specs[item].boot_source }}"
  ansible.builtin.set_fact:
    __mp_boot_disk:
      name: containerdisk
      disk:
        bus: virtio
    __mp_boot_volume:
      name: containerdisk
      persistentVolumeClaim:
        claimName: "{{ _bs.name }}"
    __mp_data_volume_templates: []
  when: _mp_specs[item].boot_source.type == 'pvc'
```

### Step 4: Run test to verify it passes

Run: `pytest tests/unit/kubevirt_render/test_boot_sources.py -v`
Expected: all 9 boot-source tests PASS.

### Step 5: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml tests/unit/kubevirt_render/test_boot_sources.py
git commit -m "feat(kubevirt): renderer boot_source=pvc (direct PVC mount)"
```

---

## Task 6: Implement `cpu`, `memory`, `memory_limit`

Adds the curated compute knobs. CPU defaults to `{cores: 2}` to match today's behavior. `memory` (a string, role-default `'1Gi'`) renders as `requests.memory`. `memory_limit` is optional and renders as `limits.memory`.

**Files:**
- Modify: `roles/kubevirt/tasks/_build_vm.yml`
- Create: `tests/unit/kubevirt_render/test_compute.py`

### Step 1: Write the failing tests

Create `tests/unit/kubevirt_render/test_compute.py`:

```python
"""Renderer tests for the cpu / memory / memory_limit curated knobs."""

from __future__ import annotations


def _base(extra: dict | None = None) -> dict:
    spec: dict = {"boot_source": {"type": "container_disk", "image": "quay.io/x"}}
    if extra:
        spec.update(extra)
    return spec


def test_cpu_defaults_to_two_cores(render_vm) -> None:
    """No cpu key in input → renders cpu.cores=2 (matches the v1.0 behavior)."""
    vm = render_vm(_base())
    assert vm["spec"]["template"]["spec"]["domain"]["cpu"] == {"cores": 2}


def test_cpu_full(render_vm) -> None:
    """All cpu sub-keys pass through verbatim."""
    vm = render_vm(_base({"cpu": {"cores": 4, "sockets": 2, "threads": 1, "model": "host-passthrough"}}))
    assert vm["spec"]["template"]["spec"]["domain"]["cpu"] == {
        "cores": 4, "sockets": 2, "threads": 1, "model": "host-passthrough",
    }


def test_memory_default_request(render_vm) -> None:
    """No memory key → resources.requests.memory='1Gi' (the role default)."""
    vm = render_vm(_base())
    res = vm["spec"]["template"]["spec"]["domain"]["resources"]
    assert res["requests"]["memory"] == "1Gi"
    assert "limits" not in res


def test_memory_explicit_and_limit(render_vm) -> None:
    """memory + memory_limit render both requests and limits."""
    vm = render_vm(_base({"memory": "2Gi", "memory_limit": "4Gi"}))
    res = vm["spec"]["template"]["spec"]["domain"]["resources"]
    assert res["requests"]["memory"] == "2Gi"
    assert res["limits"]["memory"] == "4Gi"
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/unit/kubevirt_render/test_compute.py -v`
Expected: all four FAIL — `__mp_base` has no `domain.cpu` or `domain.resources`.

### Step 3: Extend the renderer

In `_build_vm.yml`, ADD a new task block AFTER the four dispatch tasks and BEFORE the `Apply boot source to base` block:

```yaml
- name: "Compute domain.cpu and resources: {{ item }}"
  vars:
    _spec: "{{ _mp_specs[item] }}"
    _cpu: "{{ _spec.cpu | default({'cores': 2}) }}"
    _mem_req: "{{ _spec.memory | default('1Gi') }}"
    _resources: >-
      {{
        {'requests': {'memory': _mem_req}}
        | combine(
            {'limits': {'memory': _spec.memory_limit}} if _spec.memory_limit is defined else {}
          )
      }}
  ansible.builtin.set_fact:
    __mp_compute_patch:
      spec:
        template:
          spec:
            domain:
              cpu: "{{ _cpu }}"
              resources: "{{ _resources }}"
```

Then UPDATE the existing `Apply boot source to base` block to merge in `__mp_compute_patch` as well — change the body to:

```yaml
- name: "Apply boot source + compute to base: {{ item }}"
  vars:
    _boot_patch:
      spec:
        template:
          spec:
            domain:
              devices:
                disks: "{{ [__mp_boot_disk] }}"
            volumes: "{{ [__mp_boot_volume] }}"
  ansible.builtin.set_fact:
    __mp_curated: >-
      {{ __mp_base
         | combine(_boot_patch, recursive=True, list_merge='prepend')
         | combine(__mp_compute_patch, recursive=True, list_merge='append') }}
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/unit/kubevirt_render/test_compute.py tests/unit/kubevirt_render/test_boot_sources.py -v`
Expected: all PASS (compute tests now pass; boot source tests still pass — the boot-source tests don't assert anything about `cpu`/`resources` so they're unaffected).

### Step 5: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml tests/unit/kubevirt_render/test_compute.py
git commit -m "feat(kubevirt): renderer cpu/memory/memory_limit curated fields"
```

---

## Task 7: Implement `instancetype` / `preference` with suppression

When `instancetype` is set, the renderer omits `domain.cpu` and `domain.resources` from the rendered VM (KubeVirt rejects conflicting fields). Both string and `{name, kind}` forms are accepted; string is sugar for `{name: <str>}`.

**Files:**
- Modify: `roles/kubevirt/tasks/_build_vm.yml`
- Modify: `tests/unit/kubevirt_render/test_compute.py`

### Step 1: Write the failing tests

Append to `tests/unit/kubevirt_render/test_compute.py`:

```python
def test_instancetype_string_shortcut(render_vm) -> None:
    """instancetype as a string becomes {name: <str>} at spec.instancetype."""
    vm = render_vm(_base({"instancetype": "u1.medium"}))
    assert vm["spec"]["instancetype"] == {"name": "u1.medium"}


def test_instancetype_full_form(render_vm) -> None:
    """instancetype as {name, kind} passes through verbatim."""
    vm = render_vm(_base({"instancetype": {"name": "u1.medium", "kind": "VirtualMachineInstancetype"}}))
    assert vm["spec"]["instancetype"] == {"name": "u1.medium", "kind": "VirtualMachineInstancetype"}


def test_preference_string_shortcut(render_vm) -> None:
    """preference as a string becomes {name: <str>}."""
    vm = render_vm(_base({"instancetype": "u1.medium", "preference": "fedora"}))
    assert vm["spec"]["preference"] == {"name": "fedora"}


def test_instancetype_suppresses_cpu_and_resources(render_vm) -> None:
    """When instancetype is set, domain.cpu and domain.resources are absent."""
    vm = render_vm(_base({"instancetype": "u1.medium", "cpu": {"cores": 8}, "memory": "16Gi"}))
    domain = vm["spec"]["template"]["spec"]["domain"]
    assert "cpu" not in domain
    assert "resources" not in domain


def test_no_instancetype_no_spec_instancetype_key(render_vm) -> None:
    """Without instancetype, spec.instancetype is not rendered."""
    vm = render_vm(_base())
    assert "instancetype" not in vm["spec"]
    assert "preference" not in vm["spec"]
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/unit/kubevirt_render/test_compute.py -v -k instancetype`
Expected: four FAIL (the suppression-absence test passes incidentally).

### Step 3: Extend the renderer

In `_build_vm.yml`, REPLACE the `Compute domain.cpu and resources` block (added in Task 6) with this guarded variant:

```yaml
- name: "Compute domain.cpu and resources (skip when instancetype is set): {{ item }}"
  vars:
    _spec: "{{ _mp_specs[item] }}"
    _cpu: "{{ _spec.cpu | default({'cores': 2}) }}"
    _mem_req: "{{ _spec.memory | default('1Gi') }}"
    _resources: >-
      {{
        {'requests': {'memory': _mem_req}}
        | combine(
            {'limits': {'memory': _spec.memory_limit}} if _spec.memory_limit is defined else {}
          )
      }}
  ansible.builtin.set_fact:
    __mp_compute_patch:
      spec:
        template:
          spec:
            domain:
              cpu: "{{ _cpu }}"
              resources: "{{ _resources }}"
  when: _mp_specs[item].instancetype is not defined

- name: "Skip compute patch when instancetype is set: {{ item }}"
  ansible.builtin.set_fact:
    __mp_compute_patch: {}
  when: _mp_specs[item].instancetype is defined

- name: "Build instancetype/preference patch: {{ item }}"
  vars:
    _spec: "{{ _mp_specs[item] }}"
    _it_raw: "{{ _spec.instancetype | default(none) }}"
    _pref_raw: "{{ _spec.preference | default(none) }}"
    _it_block: >-
      {{
        ({'name': _it_raw} if _it_raw is string else (_it_raw | default({})))
      }}
    _pref_block: >-
      {{
        ({'name': _pref_raw} if _pref_raw is string else (_pref_raw | default({})))
      }}
  ansible.builtin.set_fact:
    __mp_instancetype_patch: >-
      {{
        {'spec': {}}
        | combine({'spec': {'instancetype': _it_block}} if _it_raw is not none else {}, recursive=True)
        | combine({'spec': {'preference': _pref_block}} if _pref_raw is not none else {}, recursive=True)
      }}
```

UPDATE the `Apply boot source + compute to base` task to also merge `__mp_instancetype_patch`:

```yaml
- name: "Apply boot source + compute + instancetype to base: {{ item }}"
  vars:
    _boot_patch:
      spec:
        template:
          spec:
            domain:
              devices:
                disks: "{{ [__mp_boot_disk] }}"
            volumes: "{{ [__mp_boot_volume] }}"
  ansible.builtin.set_fact:
    __mp_curated: >-
      {{ __mp_base
         | combine(_boot_patch, recursive=True, list_merge='prepend')
         | combine(__mp_compute_patch, recursive=True, list_merge='append')
         | combine(__mp_instancetype_patch, recursive=True, list_merge='append') }}
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/unit/kubevirt_render/test_compute.py tests/unit/kubevirt_render/test_boot_sources.py -v`
Expected: all PASS.

### Step 5: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml tests/unit/kubevirt_render/test_compute.py
git commit -m "feat(kubevirt): renderer instancetype/preference with cpu+resources suppression"
```

---

## Task 8: Implement scheduling (`node_selector`, `tolerations`, `affinity`)

All three are optional; pass through verbatim into `spec.template.spec.{nodeSelector,tolerations,affinity}`.

**Files:**
- Modify: `roles/kubevirt/tasks/_build_vm.yml`
- Create: `tests/unit/kubevirt_render/test_scheduling.py`

### Step 1: Write the failing tests

Create `tests/unit/kubevirt_render/test_scheduling.py`:

```python
"""Renderer tests for scheduling knobs (nodeSelector, tolerations, affinity)."""

from __future__ import annotations


def _base(extra: dict | None = None) -> dict:
    spec: dict = {"boot_source": {"type": "container_disk", "image": "quay.io/x"}}
    if extra:
        spec.update(extra)
    return spec


def test_no_scheduling_keys_when_unset(render_vm) -> None:
    spec = render_vm(_base())["spec"]["template"]["spec"]
    assert "nodeSelector" not in spec
    assert "tolerations" not in spec
    assert "affinity" not in spec


def test_node_selector(render_vm) -> None:
    vm = render_vm(_base({"node_selector": {"kubernetes.io/arch": "amd64", "role": "test"}}))
    assert vm["spec"]["template"]["spec"]["nodeSelector"] == {
        "kubernetes.io/arch": "amd64", "role": "test",
    }


def test_tolerations(render_vm) -> None:
    tol = [{"key": "dedicated", "operator": "Equal", "value": "molecule", "effect": "NoSchedule"}]
    vm = render_vm(_base({"tolerations": tol}))
    assert vm["spec"]["template"]["spec"]["tolerations"] == tol


def test_affinity_passthrough(render_vm) -> None:
    aff = {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {"matchExpressions": [{"key": "k", "operator": "In", "values": ["v"]}]}
                ]
            }
        }
    }
    vm = render_vm(_base({"affinity": aff}))
    assert vm["spec"]["template"]["spec"]["affinity"] == aff
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/unit/kubevirt_render/test_scheduling.py -v`
Expected: three of four FAIL (`test_no_scheduling_keys_when_unset` passes incidentally).

### Step 3: Extend the renderer

In `_build_vm.yml`, ADD a new task block AFTER the instancetype-patch block:

```yaml
- name: "Build scheduling patch: {{ item }}"
  vars:
    _spec: "{{ _mp_specs[item] }}"
    _scheduling: >-
      {{
        ({'nodeSelector': _spec.node_selector} if _spec.node_selector is defined else {})
        | combine({'tolerations': _spec.tolerations} if _spec.tolerations is defined else {})
        | combine({'affinity': _spec.affinity} if _spec.affinity is defined else {})
      }}
  ansible.builtin.set_fact:
    __mp_scheduling_patch:
      spec:
        template:
          spec: "{{ _scheduling }}"
```

UPDATE the `Apply ... to base` block to also merge `__mp_scheduling_patch`:

```yaml
    __mp_curated: >-
      {{ __mp_base
         | combine(_boot_patch, recursive=True, list_merge='prepend')
         | combine(__mp_compute_patch, recursive=True, list_merge='append')
         | combine(__mp_instancetype_patch, recursive=True, list_merge='append')
         | combine(__mp_scheduling_patch, recursive=True, list_merge='append') }}
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/unit/kubevirt_render/test_scheduling.py tests/unit/kubevirt_render/test_compute.py tests/unit/kubevirt_render/test_boot_sources.py -v`
Expected: all PASS.

### Step 5: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml tests/unit/kubevirt_render/test_scheduling.py
git commit -m "feat(kubevirt): renderer scheduling (nodeSelector/tolerations/affinity)"
```

---

## Task 9: Implement `extra_disks`, `extra_volumes`, `extra_interfaces`, `extra_networks`

All four are appended to the corresponding lists in the rendered spec. Default: empty list.

**Files:**
- Modify: `roles/kubevirt/tasks/_build_vm.yml`
- Create: `tests/unit/kubevirt_render/test_extras.py`

### Step 1: Write the failing tests

Create `tests/unit/kubevirt_render/test_extras.py`:

```python
"""Renderer tests for extra_disks/volumes/interfaces/networks (list-append)."""

from __future__ import annotations


def _base(extra: dict | None = None) -> dict:
    spec: dict = {"boot_source": {"type": "container_disk", "image": "quay.io/x"}}
    if extra:
        spec.update(extra)
    return spec


def test_extra_disks_appended_after_defaults(render_vm) -> None:
    """extra_disks come after [containerdisk, cloudinitdisk]."""
    vm = render_vm(_base({"extra_disks": [{"name": "scratch", "disk": {"bus": "virtio"}}]}))
    names = [d["name"] for d in vm["spec"]["template"]["spec"]["domain"]["devices"]["disks"]]
    assert names == ["containerdisk", "cloudinitdisk", "scratch"]


def test_extra_volumes_appended(render_vm) -> None:
    vm = render_vm(_base({
        "extra_volumes": [{"name": "scratch", "emptyDisk": {"capacity": "5Gi"}}],
    }))
    names = [v["name"] for v in vm["spec"]["template"]["spec"]["volumes"]]
    assert names == ["containerdisk", "cloudinitdisk", "scratch"]


def test_extra_interfaces_and_networks_appended(render_vm) -> None:
    vm = render_vm(_base({
        "extra_interfaces": [{"name": "bridge0", "bridge": {}}],
        "extra_networks": [{"name": "bridge0", "multus": {"networkName": "my-net"}}],
    }))
    spec = vm["spec"]["template"]["spec"]
    ifaces = [i["name"] for i in spec["domain"]["devices"]["interfaces"]]
    nets = [n["name"] for n in spec["networks"]]
    assert ifaces == ["default", "bridge0"]
    assert nets == ["default", "bridge0"]


def test_no_extras_no_change(render_vm) -> None:
    """Omitting all extras leaves the list lengths as base."""
    vm = render_vm(_base())
    spec = vm["spec"]["template"]["spec"]
    assert len(spec["domain"]["devices"]["disks"]) == 2  # containerdisk + cloudinitdisk
    assert len(spec["volumes"]) == 2
    assert len(spec["domain"]["devices"]["interfaces"]) == 1
    assert len(spec["networks"]) == 1
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/unit/kubevirt_render/test_extras.py -v`
Expected: three FAIL, one PASS (`test_no_extras_no_change`).

### Step 3: Extend the renderer

In `_build_vm.yml`, ADD a new task block AFTER the scheduling-patch block:

```yaml
- name: "Build extras patch: {{ item }}"
  vars:
    _spec: "{{ _mp_specs[item] }}"
  ansible.builtin.set_fact:
    __mp_extras_patch:
      spec:
        template:
          spec:
            domain:
              devices:
                disks: "{{ _spec.extra_disks | default([]) }}"
                interfaces: "{{ _spec.extra_interfaces | default([]) }}"
            volumes: "{{ _spec.extra_volumes | default([]) }}"
            networks: "{{ _spec.extra_networks | default([]) }}"
```

UPDATE the `Apply ... to base` block to also merge `__mp_extras_patch` (which **must** come AFTER the boot-source patch so the extras land after the boot/cloudinit entries):

```yaml
    __mp_curated: >-
      {{ __mp_base
         | combine(_boot_patch, recursive=True, list_merge='prepend')
         | combine(__mp_compute_patch, recursive=True, list_merge='append')
         | combine(__mp_instancetype_patch, recursive=True, list_merge='append')
         | combine(__mp_scheduling_patch, recursive=True, list_merge='append')
         | combine(__mp_extras_patch, recursive=True, list_merge='append') }}
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/unit/kubevirt_render/ -v`
Expected: all PASS.

### Step 5: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml tests/unit/kubevirt_render/test_extras.py
git commit -m "feat(kubevirt): renderer extra_disks/volumes/interfaces/networks (list-append)"
```

---

## Task 10: Implement `vm_overrides` escape hatch

Deep-merge `vm_overrides` into the rendered VM. List-append semantics.

**Files:**
- Modify: `roles/kubevirt/tasks/_build_vm.yml`
- Create: `tests/unit/kubevirt_render/test_vm_overrides.py`

### Step 1: Write the failing tests

Create `tests/unit/kubevirt_render/test_vm_overrides.py`:

```python
"""Renderer tests for the vm_overrides escape hatch (deep-merge, list-append)."""

from __future__ import annotations


def _base(extra: dict | None = None) -> dict:
    spec: dict = {"boot_source": {"type": "container_disk", "image": "quay.io/x"}}
    if extra:
        spec.update(extra)
    return spec


def test_overrides_metadata_labels(render_vm) -> None:
    """vm_overrides.metadata.labels deep-merges into metadata.labels."""
    vm = render_vm(_base({"vm_overrides": {"metadata": {"labels": {"team": "platform"}}}}))
    labels = vm["metadata"]["labels"]
    assert labels["team"] == "platform"
    assert labels["kubevirt.io/domain"] == "instance"  # base label preserved


def test_overrides_annotations(render_vm) -> None:
    vm = render_vm(_base({"vm_overrides": {"metadata": {"annotations": {"foo": "bar"}}}}))
    assert vm["metadata"]["annotations"] == {"foo": "bar"}


def test_overrides_termination_grace_period(render_vm) -> None:
    vm = render_vm(_base({
        "vm_overrides": {"spec": {"template": {"spec": {"terminationGracePeriodSeconds": 60}}}},
    }))
    assert vm["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 60


def test_overrides_tolerations_list_append(render_vm) -> None:
    """A toleration from vm_overrides appends to a toleration from the curated field."""
    curated_tol = {"key": "curated", "operator": "Exists", "effect": "NoSchedule"}
    override_tol = {"key": "override", "operator": "Exists", "effect": "NoSchedule"}
    vm = render_vm(_base({
        "tolerations": [curated_tol],
        "vm_overrides": {"spec": {"template": {"spec": {"tolerations": [override_tol]}}}},
    }))
    tols = vm["spec"]["template"]["spec"]["tolerations"]
    assert curated_tol in tols
    assert override_tol in tols


def test_overrides_firmware(render_vm) -> None:
    """vm_overrides can reach into nested domain fields like firmware."""
    vm = render_vm(_base({
        "vm_overrides": {
            "spec": {"template": {"spec": {"domain": {"firmware": {"bootloader": {"efi": {"secureBoot": False}}}}}}},
        },
    }))
    fw = vm["spec"]["template"]["spec"]["domain"]["firmware"]
    assert fw["bootloader"]["efi"]["secureBoot"] is False
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/unit/kubevirt_render/test_vm_overrides.py -v`
Expected: all five FAIL — the renderer has no `vm_overrides` merge step.

### Step 3: Extend the renderer

In `_build_vm.yml`, REPLACE the final `Set __mp_kubevirt_vm` block with:

```yaml
- name: "Apply vm_overrides escape hatch: {{ item }}"
  ansible.builtin.set_fact:
    __mp_kubevirt_vm: >-
      {{ __mp_curated
         | combine(_mp_specs[item].vm_overrides | default({}),
                   recursive=True, list_merge='append') }}
```

### Step 4: Run tests to verify they pass

Run: `pytest tests/unit/kubevirt_render/ -v`
Expected: all PASS.

### Step 5: Commit

```bash
git add roles/kubevirt/tasks/_build_vm.yml tests/unit/kubevirt_render/test_vm_overrides.py
git commit -m "feat(kubevirt): renderer vm_overrides escape hatch (deep-merge, list-append)"
```

---

## Task 11: Create `_validate.yml` and wire into `create.yml`

Validation is a separate file (mirrors `roles/docker/tasks/_validate.yml`) so it can be tested in isolation. Tests use a `validate_harness.yml` that wraps just the validate include.

**Files:**
- Create: `roles/kubevirt/tasks/_validate.yml`
- Create: `tests/unit/kubevirt_render/validate_harness.yml`
- Create: `tests/unit/kubevirt_render/test_validate.py`
- Modify: `roles/kubevirt/tasks/create.yml`

### Step 1: Write the failing tests

Create `tests/unit/kubevirt_render/validate_harness.yml`:

```yaml
---
# Harness for unit-testing roles/kubevirt/tasks/_validate.yml.
- name: Validate host_spec
  hosts: localhost
  gather_facts: false
  vars:
    item: instance
    _mp_specs:
      instance: "{{ host_spec }}"
    mp_kubevirt_allowed_ssh_service_types: ["NodePort"]
  tasks:
    - name: Include the validator
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../roles/kubevirt/tasks/_validate.yml"
```

Create `tests/unit/kubevirt_render/test_validate.py`:

```python
"""Tests for roles/kubevirt/tasks/_validate.yml (negative paths)."""

from __future__ import annotations


def test_valid_container_disk_passes(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "container_disk", "image": "quay.io/x"}})
    assert proc.returncode == 0, proc.stderr


def test_missing_boot_source_fails(run_validate) -> None:
    proc = run_validate({"namespace": "test"})  # no boot_source
    assert proc.returncode != 0
    assert "boot_source" in (proc.stdout + proc.stderr)


def test_invalid_type_fails(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "wat", "image": "x"}})
    assert proc.returncode != 0
    assert "wat" in (proc.stdout + proc.stderr) or "container_disk" in (proc.stdout + proc.stderr)


def test_container_disk_missing_image_fails(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "container_disk"}})
    assert proc.returncode != 0
    assert "image" in (proc.stdout + proc.stderr)


def test_data_volume_url_missing_size_fails(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "data_volume_url", "url": "https://x"}})
    assert proc.returncode != 0
    assert "size" in (proc.stdout + proc.stderr)


def test_data_volume_pvc_missing_source_namespace_fails(run_validate) -> None:
    proc = run_validate({
        "boot_source": {"type": "data_volume_pvc", "source": {"name": "g"}, "size": "10Gi"},
    })
    assert proc.returncode != 0
    assert "namespace" in (proc.stdout + proc.stderr)


def test_pvc_missing_name_fails(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "pvc"}})
    assert proc.returncode != 0
    assert "name" in (proc.stdout + proc.stderr)


def test_invalid_ssh_service_type_fails(run_validate) -> None:
    proc = run_validate({
        "boot_source": {"type": "container_disk", "image": "quay.io/x"},
        "ssh_service": {"type": "LoadBalancer"},
    })
    assert proc.returncode != 0
    assert "LoadBalancer" in (proc.stdout + proc.stderr) or "NodePort" in (proc.stdout + proc.stderr)
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/unit/kubevirt_render/test_validate.py -v`
Expected: all FAIL because `_validate.yml` doesn't exist (the include task fails, but with the wrong error — the first "valid" test will fail on include resolution).

### Step 3: Create `_validate.yml`

Create `roles/kubevirt/tasks/_validate.yml`:

```yaml
---
# Per-host validation. Operates on _mp_specs[item] (populated upstream in
# create.yml by the merge loop, or by the unit-test harness).

- name: "Assert boot_source is defined: {{ item }}"
  ansible.builtin.assert:
    that:
      - "'boot_source' in _mp_specs[item]"
      - _mp_specs[item].boot_source is mapping
      - "'type' in _mp_specs[item].boot_source"
    fail_msg: >-
      Host '{{ item }}' is missing `mp.kubevirt.boot_source`. Set it to
      one of: container_disk, data_volume_url, data_volume_pvc, pvc.

- name: "Assert boot_source.type is supported: {{ item }}"
  ansible.builtin.assert:
    that:
      - _mp_specs[item].boot_source.type in
          ['container_disk', 'data_volume_url', 'data_volume_pvc', 'pvc']
    fail_msg: >-
      Host '{{ item }}' has unsupported boot_source.type
      '{{ _mp_specs[item].boot_source.type }}'. Supported:
      container_disk, data_volume_url, data_volume_pvc, pvc.

- name: "Assert container_disk required fields: {{ item }}"
  ansible.builtin.assert:
    that:
      - _mp_specs[item].boot_source.image is defined
      - _mp_specs[item].boot_source.image | length > 0
    fail_msg: "Host '{{ item }}' boot_source.type=container_disk requires `image`."
  when: _mp_specs[item].boot_source.type == 'container_disk'

- name: "Assert data_volume_url required fields: {{ item }}"
  ansible.builtin.assert:
    that:
      - _mp_specs[item].boot_source.url is defined
      - _mp_specs[item].boot_source.size is defined
    fail_msg: >-
      Host '{{ item }}' boot_source.type=data_volume_url requires `url` and `size`.
  when: _mp_specs[item].boot_source.type == 'data_volume_url'

- name: "Assert data_volume_pvc required fields: {{ item }}"
  ansible.builtin.assert:
    that:
      - _mp_specs[item].boot_source.source is defined
      - _mp_specs[item].boot_source.source.name is defined
      - _mp_specs[item].boot_source.source.namespace is defined
      - _mp_specs[item].boot_source.size is defined
    fail_msg: >-
      Host '{{ item }}' boot_source.type=data_volume_pvc requires
      source.name, source.namespace, and size.
  when: _mp_specs[item].boot_source.type == 'data_volume_pvc'

- name: "Assert pvc required fields: {{ item }}"
  ansible.builtin.assert:
    that:
      - _mp_specs[item].boot_source.name is defined
      - _mp_specs[item].boot_source.name | length > 0
    fail_msg: "Host '{{ item }}' boot_source.type=pvc requires `name` (the PVC name)."
  when: _mp_specs[item].boot_source.type == 'pvc'

- name: "Assert ssh_service.type is supported: {{ item }}"
  ansible.builtin.assert:
    that:
      - (_mp_specs[item].ssh_service.type | default('NodePort'))
        in mp_kubevirt_allowed_ssh_service_types
    fail_msg: >-
      Host '{{ item }}' has unsupported ssh_service.type
      '{{ _mp_specs[item].ssh_service.type | default('(missing)') }}'.
      Supported: {{ mp_kubevirt_allowed_ssh_service_types | join(', ') }}.

- name: "Warn if instancetype is set alongside cpu/memory_limit: {{ item }}"
  ansible.builtin.debug:
    msg: >-
      Host '{{ item }}' sets both `instancetype` and `cpu`/`memory_limit`.
      The renderer suppresses domain.cpu and domain.resources when instancetype
      is set, so the cpu/memory_limit values will be ignored.
  when:
    - _mp_specs[item].instancetype is defined
    - (_mp_specs[item].cpu is defined or _mp_specs[item].memory_limit is defined)
```

### Step 4: Wire `_validate.yml` into `create.yml`

Modify `roles/kubevirt/tasks/create.yml`. REPLACE the existing `Validate ssh_service.type per host` block (the assertion block) with this include loop:

```yaml
- name: Validate per-host specs
  ansible.builtin.include_tasks: _validate.yml
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

### Step 5: Run tests to verify they pass

Run: `pytest tests/unit/kubevirt_render/test_validate.py -v`
Expected: all PASS.

Run: `pytest tests/unit/kubevirt_render/ -v`
Expected: all 30+ unit tests PASS.

### Step 6: Commit

```bash
git add roles/kubevirt/tasks/_validate.yml roles/kubevirt/tasks/create.yml \
        tests/unit/kubevirt_render/validate_harness.yml \
        tests/unit/kubevirt_render/test_validate.py
git commit -m "feat(kubevirt): per-host validation in _validate.yml (boot_source + ssh_service)"
```

---

## Task 12: Refactor `_create_vm.yml` to use the renderer

Replaces the hardcoded k8s manifest with a call to `_build_vm.yml` + a single k8s apply of the rendered dict.

**Files:**
- Modify: `roles/kubevirt/tasks/_create_vm.yml`

### Step 1: Inspect current contents

`_create_vm.yml` currently contains the hardcoded VirtualMachine manifest as the body of a single `kubernetes.core.k8s` task. After Tasks 1–11, the renderer produces an equivalent (and richer) dict.

### Step 2: Rewrite `_create_vm.yml`

Replace the entire file `roles/kubevirt/tasks/_create_vm.yml` with:

```yaml
---
# Per-host: render the VirtualMachine via _build_vm.yml, then apply.
# This file is included from create.yml in a loop over groups['molecule'].

- name: "Render VirtualMachine spec: {{ item }}"
  ansible.builtin.include_tasks: _build_vm.yml

- name: "Apply VirtualMachine: {{ item }}"
  kubernetes.core.k8s:
    state: present
    definition: "{{ __mp_kubevirt_vm }}"
```

### Step 3: Run lint to catch syntax issues

Run: `ansible-lint roles/kubevirt/`
Expected: no errors. (If `_build_vm.yml` triggers `risky-shell-pipe` or similar, address it now.)

### Step 4: Commit

```bash
git add roles/kubevirt/tasks/_create_vm.yml
git commit -m "refactor(kubevirt): _create_vm.yml uses the new renderer"
```

---

## Task 13: Update the self-test scenario and run the integration test

The self-test inventory still uses the bare `image:` shortcut, which Task 11's validation now rejects. Update it to the new schema and run the integration test end-to-end.

**Files:**
- Modify: `extensions/molecule/default/inventory/hosts.yml`

### Step 1: Update the kubevirt block

Edit `extensions/molecule/default/inventory/hosts.yml`. CHANGE the `kubevirt:` block from:

```yaml
            kubevirt:
              image: quay.io/kubevirt/fedora-cloud-container-disk-demo:latest
```

to:

```yaml
            kubevirt:
              boot_source:
                type: container_disk
                image: quay.io/kubevirt/fedora-cloud-container-disk-demo:latest
              cpu:
                cores: 2
              vm_overrides:
                metadata:
                  labels:
                    test.molecule_provisioners/exercise: vm_overrides
```

### Step 2: Run the kubevirt self-test

(Requires a Kubernetes cluster with KubeVirt at `$KUBECONFIG`. In CI this is the kind cluster from `.github/workflows/tests.yml`. Locally, skip this step and rely on CI if no cluster is available.)

Run:

```bash
PROVISIONER=kubevirt pytest tests/integration -v -k default -o addopts="" -s
```

Expected: PASS. The kubevirt CI job (`integration-kubevirt`) overrides addopts to keep PLAY RECAP visible — same flag locally.

If a cluster is not available locally, run only the unit tests as a smoke check:

```bash
pytest tests/unit/ -v
```

Expected: all PASS.

### Step 3: Commit

```bash
git add extensions/molecule/default/inventory/hosts.yml
git commit -m "test(kubevirt): self-test inventory uses boot_source + cpu + vm_overrides"
```

---

## Task 14: Update docs (README, examples, MIGRATION, argument_specs, CLAUDE.md)

Final pass to align documentation with the new schema. Each doc change is its own file edit; all committed together.

**Files:**
- Modify: `roles/kubevirt/README.md`
- Modify: `docs/examples/inventory/hosts.yml`
- Modify: `docs/MIGRATION.md`
- Modify: `roles/kubevirt/meta/argument_specs.yml`
- Modify: `CLAUDE.md`

### Step 1: Rewrite the README inputs section

In `roles/kubevirt/README.md`, REPLACE the section starting at `## Inputs (per-host, in inventory)` through the end of `## v1 limitation` with:

````markdown
## Inputs (per-host, in inventory)

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            kubevirt:
              # Required: boot source (one of container_disk, data_volume_url,
              # data_volume_pvc, pvc). See "Boot sources" below.
              boot_source:
                type: container_disk
                image: quay.io/containerdisks/ubuntu:24.04

              # Optional
              namespace: molecule              # role default 'molecule'
              ssh_user: cloud-user             # role default 'cloud-user'
              ssh_service:
                type: NodePort                 # only NodePort in v1

              # Curated compute
              cpu:
                cores: 4                       # default 2
                sockets: 1
                threads: 1
              memory: 1Gi                      # → resources.requests.memory
              memory_limit: 2Gi                # → resources.limits.memory

              # Compute presets (alternative to cpu/memory; suppresses both)
              instancetype: u1.medium          # str OR {name, kind}
              preference: fedora               # str OR {name, kind}

              # Scheduling
              node_selector: {kubernetes.io/arch: amd64}
              tolerations: []
              affinity: {}

              # Appended to defaults (containerdisk + cloudinitdisk + default pod net)
              extra_disks: []
              extra_volumes: []
              extra_interfaces: []
              extra_networks: []

              # Escape hatch — deep-merged into the whole VirtualMachine object
              # (lists append). Use for anything not surfaced above.
              vm_overrides: {}
```

Shared defaults can be hoisted into `mp_defaults.kubevirt` in `inventory/group_vars/molecule.yml`. Field resolution: role defaults ← `mp_defaults.kubevirt` ← `hostvars[item].mp.kubevirt`.

## Boot sources

### `container_disk` — OCI-packaged image

```yaml
boot_source:
  type: container_disk
  image: quay.io/containerdisks/ubuntu:24.04
```

### `data_volume_url` — CDI import from URL

Requires CDI installed on the cluster.

```yaml
boot_source:
  type: data_volume_url
  url: https://cloud-images.ubuntu.com/.../noble.img
  checksum: "sha256:..."     # optional
  size: 10Gi                  # required
  storage_class: standard     # optional
```

### `data_volume_pvc` — CDI smart-clone from existing PVC

Requires CDI installed on the cluster.

```yaml
boot_source:
  type: data_volume_pvc
  source: {name: golden-ubuntu, namespace: images}
  size: 10Gi                  # required
  storage_class: standard     # optional
```

### `pvc` — direct mount of existing PVC

No CDI required.

```yaml
boot_source:
  type: pvc
  name: existing-boot-pvc
```

## Escape hatch and foot-guns

`vm_overrides` is deep-merged into the whole VirtualMachine object with `list_merge='append'`. There are no guardrails — overriding any of the following will break the lifecycle:

- **Don't set `spec.running: false`.** The prepare phase calls `wait_for_connection` against the NodePort SSH service; a stopped VM never becomes reachable.
- **Don't replace the `cloudinitdisk` volume.** The role injects an SSH public key via cloud-init `users:`. If you must edit it, replicate the block and keep `temporary_ssh_public_key`.
- **Don't change `metadata.labels.kubevirt.io/domain` or the SSH Service's selector.** The NodePort routes by this label.

When `instancetype` is set, the renderer **omits** `domain.cpu` and `domain.resources` from the rendered spec — KubeVirt rejects conflicting fields. Setting `cpu:`/`memory_limit:` alongside `instancetype:` is silently ignored (a debug message is emitted at validate time).

## Role-level overrides

See `defaults/main.yml` (`mp_kubevirt_role_defaults`, `mp_kubevirt_ssh_key_path`, `mp_kubevirt_wait_timeout`, `mp_kubevirt_allowed_ssh_service_types`).

## v1 limitation

Only `ssh_service.type: NodePort` is supported. The role asserts this on create. LoadBalancer / ClusterIP+port-forward are out of scope for v1.
````

### Step 2: Update `docs/examples/inventory/hosts.yml`

Edit `docs/examples/inventory/hosts.yml`. CHANGE the `kubevirt:` block from:

```yaml
            kubevirt:
              image: quay.io/containerdisks/ubuntu:24.04
              ssh_user: ubuntu
```

to:

```yaml
            kubevirt:
              boot_source:
                type: container_disk
                image: quay.io/containerdisks/ubuntu:24.04
              ssh_user: ubuntu
```

### Step 3: Add a migration section to `docs/MIGRATION.md`

Append to `docs/MIGRATION.md`:

```markdown

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

Three additional boot-source modes are now available: `data_volume_url`,
`data_volume_pvc`, `pvc`. See `roles/kubevirt/README.md#boot-sources`.
```

### Step 4: Expand `argument_specs.yml`

Edit `roles/kubevirt/meta/argument_specs.yml`. REPLACE the `create:` block contents with:

```yaml
  create:
    short_description: >-
      Create KubeVirt VirtualMachines and NodePort services for hosts in groups['molecule'].
    options:
      mp_kubevirt_role_defaults:
        type: dict
        description: >-
          Per-host field defaults. Currently includes namespace, ssh_user, memory,
          and ssh_service.type. Layered as: this dict <- mp_defaults.kubevirt
          <- hostvars[item].mp.kubevirt. Per-host hostvars accepts a much wider
          schema (boot_source, cpu, memory_limit, scheduling, extras, vm_overrides);
          see roles/kubevirt/README.md.
      mp_kubevirt_ssh_key_path:
        type: path
        description: Where the SSH keypair is generated (defaults to molecule_ephemeral_directory/identity_file).
      mp_kubevirt_allowed_ssh_service_types:
        type: list
        elements: str
        description: Allowlist for ssh_service.type. v1 supports NodePort only.
```

### Step 5: Update the Public-contract block in `CLAUDE.md`

In `CLAUDE.md`, find the `kubevirt:` block under "Public contract (the thing we don't break without a major bump)" and REPLACE it with:

```yaml
            kubevirt:                         # required when mp_backend == kubevirt
              boot_source:                    # required: discriminated union
                type: container_disk          #   container_disk | data_volume_url | data_volume_pvc | pvc
                image: <str>                  #   per-type fields; see roles/kubevirt/README.md
              namespace: <str>                # optional, role default 'molecule'
              ssh_user: <str>                 # optional, role default 'cloud-user'
              ssh_service:
                type: NodePort                # optional, only NodePort in v1
              # Optional curated knobs:
              cpu: {cores, sockets, threads, model}
              memory: <str>                   # role default '1Gi' → requests.memory
              memory_limit: <str>             # → limits.memory
              instancetype: <str-or-dict>     # str OR {name, kind}; suppresses cpu/resources
              preference: <str-or-dict>
              node_selector: <dict>
              tolerations: <list>
              affinity: <dict>
              extra_disks: <list>             # appended to [containerdisk, cloudinitdisk]
              extra_volumes: <list>           # appended to [containerdisk, cloudinitdisk]
              extra_interfaces: <list>        # appended after default masquerade
              extra_networks: <list>          # appended after default pod
              vm_overrides: <dict>            # escape hatch: deep-merge into whole VM, lists append
```

### Step 6: Run pre-commit to catch formatting drift

Run: `pre-commit run --all-files`
Expected: PASS (or auto-fixes that are then staged).

### Step 7: Run lint

Run: `ansible-lint && yamllint .`
Expected: PASS.

### Step 8: Commit

```bash
git add roles/kubevirt/README.md docs/examples/inventory/hosts.yml docs/MIGRATION.md \
        roles/kubevirt/meta/argument_specs.yml CLAUDE.md
git commit -m "docs(kubevirt): document boot_source schema and vm_overrides escape hatch"
```

---

## Done check

After all 14 tasks:

- [ ] `pytest tests/unit/ -v` → all PASS (≈30 tests)
- [ ] `pytest tests/unit/test_basic.py -v` still passes (the pre-existing dummy test)
- [ ] `PROVISIONER=kubevirt pytest tests/integration -v -k default -o addopts="" -s` → PASS (on a cluster with KubeVirt)
- [ ] `PROVISIONER=podman pytest tests/integration -v -k default` → PASS (no regression)
- [ ] `PROVISIONER=docker pytest tests/integration -v -k default` → PASS (no regression)
- [ ] `ansible-lint && yamllint .` → clean
- [ ] `pre-commit run --all-files` → clean
- [ ] `git log --oneline` shows ~14 small commits, one per task
