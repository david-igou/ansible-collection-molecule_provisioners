#!/usr/bin/python
# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
# GNU General Public License v3.0+ (https://www.gnu.org/licenses/gpl-3.0.txt).
# Alternatively, use under the MIT license in LICENSE.
"""Document the controller-only AWS lifecycle lease action."""

DOCUMENTATION = r"""
---
module: aws_lock
short_description: Serialize AWS lifecycle operations within one ephemeral directory
description:
  - Internal controller action used by the AWS role.
  - A process lease protects saved ownership across individual role tasks.
  - Requires Linux process identity and filesystem locking support.
author:
  - David Igou (@david-igou)
version_added: 0.0.7
options:
  directory:
    description: Private Molecule ephemeral directory containing the run state.
    type: path
    required: true
  state:
    description: Acquire or release the current controller's lease.
    type: str
    required: true
    choices:
      - held
      - released
attributes:
  check_mode:
    description: Skips filesystem changes in check mode.
    support: full
  action:
    description: Runs on the Ansible controller.
    support: full
"""
EXAMPLES = r"""
- name: Serialize an AWS lifecycle operation
  david_igou.molecule_provisioners.aws_lock:
    directory: /tmp/molecule-aws-run
    state: held
"""
RETURN = r"""{}"""

from ansible.module_utils.basic import AnsibleModule


def main():
    """Require the action plugin to retain the long-lived controller identity."""
    module = AnsibleModule(
        argument_spec={
            "directory": {"type": "path", "required": True},
            "state": {"type": "str", "choices": ["held", "released"], "required": True},
        },
        supports_check_mode=True,
    )
    module.fail_json(msg="Use the controller action plugin through the AWS lifecycle role")


if __name__ == "__main__":
    main()
