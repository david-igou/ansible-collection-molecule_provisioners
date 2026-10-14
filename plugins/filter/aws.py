# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
"""AWS inventory validation and launch rendering used by the provisioner role."""

from ansible.errors import AnsibleFilterError
from ansible_collections.david_igou.molecule_provisioners.plugins.module_utils import aws_spec


def checked(function):
    """Convert safe validation errors to actionable Ansible filter failures."""

    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError) as error:
            raise AnsibleFilterError(str(error)) from error

    return wrapped


class FilterModule:
    """Expose AWS specification operations to role tasks."""

    def filters(self):
        """Return role-private filters."""
        return {
            "aws_" + name: checked(getattr(aws_spec, name))
            for name in (
                "normalize",
                "intent",
                "token",
                "tags",
                "cloud_data",
                "request",
                "ingress",
                "runtime",
                "fingerprint",
                "template_spec",
                "image_info",
                "network_plan",
                "owned",
                "reconcile_network",
                "valid_request",
            )
        }
