# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
"""Internal VMware role filters."""

from ansible.errors import AnsibleFilterError
from ansible_collections.david_igou.molecule_provisioners.plugins.module_utils import vmware_spec


def checked(function):
    """Convert input errors into safe Ansible failures."""

    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError) as error:
            raise AnsibleFilterError(str(error)) from error

    return wrapped


class FilterModule:
    """Expose the VMware lifecycle contracts."""

    def filters(self):
        """Return role-private filters."""
        return {
            "vmware_" + name: checked(getattr(vmware_spec, name))
            for name in (
                "normalize",
                "discovery_specs",
                "fingerprint",
                "new_run",
                "validate_run",
                "update_host",
                "request",
                "runtime",
                "linux_command",
                "remove_host",
            )
        }
