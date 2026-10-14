# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
"""Hold a controller-process lease across the AWS role's individual tasks."""

import fcntl
import json
import os
from pathlib import Path

from ansible.plugins.action import ActionBase


def identity(pid):
    """Include Linux process start time so a recycled PID cannot retain a lease."""
    try:
        return Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def lease(directory, state, pid):
    """Serialize lease changes and preserve a live controller's lifecycle lease."""
    path = Path(directory)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    guard = path / ".aws-lock-guard"
    with os.fdopen(os.open(guard, os.O_RDWR | os.O_CREAT, 0o600), "a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        lock = path / ".aws-lock"
        current = json.loads(lock.read_text()) if lock.exists() else {}
        own = {"pid": pid, "start": identity(pid), "namespace": os.readlink("/proc/self/ns/pid")}
        if current and current.get("namespace") != own["namespace"]:
            raise ValueError(
                "AWS lease belongs to another PID namespace; confirm its controller stopped before removing .aws-lock"
            )
        if current and current != own and identity(current["pid"]) == current["start"]:
            raise ValueError("Another active Ansible controller owns this AWS ephemeral directory")
        if state == "held":
            with os.fdopen(
                os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
            ) as output:
                json.dump(own, output)
        elif current == own or not current or identity(current["pid"]) != current["start"]:
            lock.unlink(missing_ok=True)
        else:
            raise ValueError("Cannot release another controller's AWS lease")


class ActionModule(ActionBase):
    """Use the long-lived controller parent rather than the per-task worker PID."""

    TRANSFERS_FILES = False

    def run(self, tmp=None, task_vars=None):
        """Acquire/release a private local lease; check mode never writes files."""
        result = super().run(tmp, task_vars)
        result["changed"] = False
        if self._task.check_mode:
            return result
        try:
            args = self._task.args
            if args.get("state") not in ("held", "released"):
                raise ValueError("AWS lease state must be held or released")
            lease(args["directory"], args["state"], os.getppid())
        except (ValueError, KeyError, OSError) as error:
            result.update(failed=True, msg=str(error))
        return result
