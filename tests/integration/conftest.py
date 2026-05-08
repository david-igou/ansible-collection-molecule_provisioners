"""Integration test gating.

Skips the kubevirt molecule scenario unless MOLECULE_KUBEVIRT_ENABLED
is truthy. The pytest_ansible molecule_scenario fixture parameterizes
each scenario directory under extensions/molecule/, so we add a skip
marker to any test item whose nodeid mentions kubevirt.
"""

from __future__ import annotations

import os

import pytest


_TRUTHY = {"1", "true", "yes", "on"}


def _kubevirt_enabled() -> bool:
    return os.environ.get("MOLECULE_KUBEVIRT_ENABLED", "").lower() in _TRUTHY


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    if _kubevirt_enabled():
        return
    skip = pytest.mark.skip(
        reason="kubevirt scenario gated by MOLECULE_KUBEVIRT_ENABLED",
    )
    for item in items:
        if "kubevirt" in item.nodeid:
            item.add_marker(skip)
