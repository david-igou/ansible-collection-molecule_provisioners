"""Guard against silent drift between renderer defaults and role defaults.

The renderer at roles/kubevirt/tasks/_build_vm.yml uses `| default(...)` filters
for namespace, ssh_user, and memory so the test harness doesn't need to mirror
mp_kubevirt_role_defaults. In production, the merge chain in create.yml supplies
these values from mp_kubevirt_role_defaults before the renderer runs.

If the role-default ever changes, the renderer's default MUST change too — or
the test harness will silently render the old value while production renders
the new one. This test asserts the two stay in sync.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROLE_DEFAULTS_PATH = (
    Path(__file__).parent / ".." / ".." / ".." / "roles" / "kubevirt" / "defaults" / "main.yml"
).resolve()
RENDERER_PATH = (
    Path(__file__).parent / ".." / ".." / ".." / "roles" / "kubevirt" / "tasks" / "_build_vm.yml"
).resolve()


def _role_defaults() -> dict:
    return yaml.safe_load(ROLE_DEFAULTS_PATH.read_text())["mp_kubevirt_role_defaults"]


def _renderer_text() -> str:
    return RENDERER_PATH.read_text()


def test_namespace_default_matches() -> None:
    """The renderer's namespace default must equal mp_kubevirt_role_defaults.namespace."""
    expected = _role_defaults()["namespace"]
    assert (
        f"default('{expected}')" in _renderer_text()
    ), f"renderer is missing | default('{expected}') for namespace"


def test_ssh_user_default_matches() -> None:
    """The renderer's ssh_user default must equal mp_kubevirt_role_defaults.ssh_user."""
    expected = _role_defaults()["ssh_user"]
    assert (
        f"default('{expected}')" in _renderer_text()
    ), f"renderer is missing | default('{expected}') for ssh_user"


def test_memory_default_matches() -> None:
    """The renderer's memory default must equal mp_kubevirt_role_defaults.memory."""
    expected = _role_defaults()["memory"]
    assert (
        f"default('{expected}')" in _renderer_text()
    ), f"renderer is missing | default('{expected}') for memory"
