#!/usr/bin/python
# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
# GNU General Public License v3.0+ (https://www.gnu.org/licenses/gpl-3.0.txt).
# Alternatively, use under the MIT license in LICENSE.
"""QEMU process ownership and run reservations."""

DOCUMENTATION = r"""
---
module: qemu_process
short_description: Manage Linux QEMU run ownership and port reservations
description:
  - Internal lifecycle helper for the QEMU role. VM configuration remains in the role.
  - Requires Linux pidfd support, Python 3.9 or newer, and a working QMP socket.
author:
  - David Igou (@david-igou)
version_added: 0.0.7
options:
  operation:
    description: Lifecycle operation.
    type: str
    required: true
    choices:
      - reserve
      - launch
      - destroy
      - status
  directory:
    description: Run ephemeral directory containing private ownership state.
    type: path
    required: true
  cache:
    description: Controller-wide lock and reservation directory shared by all runs.
    type: path
    required: true
  hosts:
    description: Non-secret allocation requests produced by the role.
    type: dict
    default: {}
  host:
    description: Logical guest name for launch or diagnostics.
    type: str
  argv:
    description: Role-generated launch arguments. Never returned or saved in the manifest.
    type: list
    elements: str
    default: []
  isolation:
    description: Allocate ports automatically instead of using the legacy base.
    type: bool
    default: true
  port_base:
    description: Legacy SSH port base used when isolation is disabled.
    type: int
    default: 2222
  cleanup_timeout:
    description: Maximum seconds to wait after SIGTERM before SIGKILL.
    type: int
    default: 30
  print_tail:
    description: Include private console and launch log tails in diagnostic results.
    type: bool
    default: false
"""
EXAMPLES = r"""
- name: Observe an owned guest without exposing console contents
  david_igou.molecule_provisioners.qemu_process:
    operation: status
    directory: /tmp/molecule-example
    cache: /tmp/molecule-qemu-locks
    host: example
"""
RETURN = r"""
run:
  description: Persisted non-secret run allocations; empty for launch to avoid argv redaction of endpoint values.
  type: dict
  returned: always
diagnostics:
  description: Process status and private log paths, with tails only when requested.
  type: dict
  returned: when operation is status
"""

import os
import signal

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.david_igou.molecule_provisioners.plugins.module_utils.qemu_runtime import (
    Runtime,
)


def main() -> None:
    """Dispatch lifecycle operations without persisting guest credentials."""
    module = AnsibleModule(
        argument_spec={
            "operation": {
                "type": "str",
                "required": True,
                "choices": ["reserve", "launch", "destroy", "status"],
            },
            "directory": {"type": "path", "required": True},
            "cache": {"type": "path", "required": True},
            "hosts": {"type": "dict", "default": {}},
            "host": {"type": "str"},
            "argv": {"type": "list", "elements": "str", "default": [], "no_log": True},
            "isolation": {"type": "bool", "default": True},
            "port_base": {"type": "int", "default": 2222},
            "cleanup_timeout": {"type": "int", "default": 30},
            "print_tail": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    params = module.params
    if not 0 <= params["cleanup_timeout"] <= 3600:
        module.fail_json(msg="cleanup_timeout must be in 0..3600 seconds.")
    try:
        if not module.check_mode and params["operation"] in ("reserve", "launch"):
            if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
                module.fail_json(
                    msg="QEMU ownership requires Linux pidfd support and Python 3.9 or newer."
                )
            descriptor = os.pidfd_open(os.getpid())
            try:
                signal.pidfd_send_signal(descriptor, 0)
            finally:
                os.close(descriptor)
        runtime = Runtime(params["directory"], params["cache"])
        if module.check_mode:
            operation = params["operation"]
            changed = (
                (operation == "reserve" and not runtime.state)
                or (operation == "destroy" and bool(runtime.state))
                or (
                    operation == "launch"
                    and runtime.state.get("hosts", {}).get(params["host"], {}).get("status")
                    != "running"
                )
            )
            module.exit_json(changed=changed, run={} if operation == "launch" else runtime.state)
        with runtime.locked():
            changed = False
            result = {}
            if params["operation"] == "reserve":
                changed = runtime.reserve(params["hosts"], params["isolation"], params["port_base"])
            elif params["operation"] == "launch":
                if not params["argv"] or params["host"] not in runtime.state.get("hosts", {}):
                    module.fail_json(
                        msg="Launch requires role-generated argv and an allocated host."
                    )
                changed = runtime.launch(params["host"], params["argv"])
            elif params["operation"] == "destroy":
                changed = runtime.destroy(params["cleanup_timeout"])
            else:
                result["diagnostics"] = runtime.diagnostics(params["host"], params["print_tail"])
        module.exit_json(
            changed=changed,
            run={} if params["operation"] == "launch" else runtime.state,
            **result,
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        module.fail_json(msg=str(error))


if __name__ == "__main__":
    main()
