# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT
"""QEMU role configuration filters."""

from ansible_collections.david_igou.molecule_provisioners.plugins.module_utils import (
    qemu_spec,
)


class FilterModule:
    """Expose the role's validated configuration transformations."""

    def filters(self) -> dict:
        return {
            "qemu_specs": qemu_spec.normalize,
            "qemu_valid": qemu_spec.valid,
            "qemu_size": qemu_spec.size_bytes,
            "qemu_requests": qemu_spec.requests,
            "qemu_user_data": qemu_spec.user_data,
            "qemu_network_data": qemu_spec.network_data,
            "qemu_argv": qemu_spec.launch_argv,
        }
