# Installed AWS consumer fixture

For a consumer setup with published dependencies, use the
[AWS walkthrough](../../../docs/AWS.md). This fixture tests a local artifact.

The AWS integration harness builds version 0.0.7-alpha, installs the artifact in an
isolated collection path, and runs this separate consumer with Molecule. The shared
dependency pin describes that version; dependency resolution is disabled because
the harness supplies the local artifact before its release to Galaxy.

All real guest configuration comes from inventory; lifecycle playbooks are public
FQCN imports. Tests supply the AMI, account and ingress CIDR through environment
variables, with us-east-2 fixed as the test region. The harness checks interactive
SSH login, list, prepare, converge, idempotence, verify and scoped destroy.
