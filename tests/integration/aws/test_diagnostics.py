"""Readiness failure reports safe IDs and retains bounded private console evidence."""

import json

from .conftest import GuestRun, write_yaml


def test_readiness_failure_retains_private_console_evidence(aws_environment):
    run = GuestRun(aws_environment, {"instance": {}})
    try:
        run.action("create")
        run.action("prepare")
        runtime_path = run.ephemeral / "inventory/molecule_runtime.yml"
        runtime = {"all": {"hosts": run.runtime()}}
        runtime["all"]["hosts"]["instance"]["ansible_user"] = "nonexistent-molecule-probe"
        write_yaml(runtime_path, runtime)
        options = json.loads(run.extra.read_text())
        options["mp_aws_wait_timeout"] = 5
        run.extra.write_text(json.dumps(options))
        code, output = run.action("prepare", check=False)
        assert code != 0
        assert (run.ephemeral / "aws_run.yml").exists()
        console = run.ephemeral / "instance-console.log"
        assert console.exists(), "Readiness rescue did not save console evidence"
        assert console.stat().st_mode & 0o777 == 0o600
        assert console.stat().st_size <= 262144
        assert "kernel:" not in output
        assert "#cloud-config" not in output
    finally:
        run.destroy()
