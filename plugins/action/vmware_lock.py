# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
"""Reuse the controller-process lease with an independent VMware lock namespace."""

import os
from pathlib import Path
from ansible.plugins.action import ActionBase
from ansible_collections.david_igou.molecule_provisioners.plugins.action.aws_lock import lease


class ActionModule(ActionBase):
    """Hold one lease across the role's lifecycle tasks."""

    TRANSFERS_FILES = False

    def run(self, tmp=None, task_vars=None):
        """Check mode performs no local writes."""
        result = super().run(tmp, task_vars)
        result["changed"] = False
        if self._task.check_mode:
            return result
        try:
            args = self._task.args
            if args.get("state") not in ("held", "released"):
                raise ValueError("VMware lease state must be held or released")
            lease(str(Path(args["directory"]) / ".vmware-lease"), args["state"], os.getppid())
        except (ValueError, KeyError, OSError) as error:
            result.update(failed=True, msg=str(error).replace("AWS", "VMware"))
        return result
