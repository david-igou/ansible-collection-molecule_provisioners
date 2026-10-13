"""Test-only process death after an API response, before the next journal task."""

import os

from ansible.plugins.callback import CallbackBase


class CallbackModule(CallbackBase):
    """Opt-in fault injection, without adding failure controls to production roles."""

    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "interrupt"
    CALLBACK_NEEDS_ENABLED = True

    def v2_runner_on_ok(self, result):
        """Exit the controller immediately; no rescue or always tasks can run."""
        if result._task.name == os.environ.get("MP_TEST_AWS_INTERRUPT_TASK"):
            os._exit(97)
