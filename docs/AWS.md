# AWS EC2 with Molecule

Use `mp_backend: aws` to test against disposable EC2 guests. With no subnet or
other existing network selected, the provisioner creates a VPC, public subnet,
internet gateway and route table. Guests in the same run and region share that
network. Destroy removes the generated resources.

This walkthrough uses **us-east-2**. See the [role reference](../roles/aws/README.md)
for all fields, [IAM requirements](../roles/aws/README.md#iam), and
[EC2 driver migration](MIGRATION.md#aws-ec2-driver) for option mappings.

## Install

The AWS backend requires collection version **0.0.7-alpha or later**. Pin an exact
version in your consumer's `extensions/molecule/requirements-test.yml`:

```yaml
---
collections:
  - name: david_igou.molecule_provisioners
    version: 0.0.7-alpha
```

In the controller's virtual environment or execution environment, install
Molecule, ansible-core 2.17+, boto3, botocore and cryptography. Installing the
collection also installs its Galaxy dependencies, including `amazon.aws`,
`community.aws` and `community.crypto`. SSH guests need an SSH client.

```bash
python -m pip install molecule 'ansible-core>=2.17' \
  'boto3>=1.35.0' 'botocore>=1.35.0' 'cryptography>=42.0.0'
ansible-galaxy collection install -r extensions/molecule/requirements-test.yml
```

To use a source checkout before its version is published, run `make build` there
and install the resulting `david_igou-molecule_provisioners-0.0.7-alpha.tar.gz`
with `ansible-galaxy collection install /path/to/artifact.tar.gz --force`.
Set `dependency.enabled: false` in the shared Molecule config below so its
dependency step uses the installed artifact. Restore `true` when using a released
Galaxy dependency.

Activate AWS credentials in the controller environment through the normal SDK
chain. A named profile can be selected with `mp_defaults.aws.profile`; a container
controller also needs access to that profile's configuration and credentials.
Keep credentials out of inventory. Set `expected_account_id` to catch accidental
use of another account before provisioning.

## Create a scenario

Use the collection's [consumer layout](../README.md#using). Copy
[`molecule.yml`](examples/molecule.yml), [`create.yml`](examples/create.yml),
[`prepare.yml`](examples/prepare.yml) and [`destroy.yml`](examples/destroy.yml)
into `extensions/molecule/default/`. Copy [`ansible.cfg`](examples/ansible.cfg)
to the consumer root, merging it with any existing configuration.

The lifecycle files import the collection playbooks. For example, `create.yml` is:

```yaml
---
- name: Create instances
  ansible.builtin.import_playbook: david_igou.molecule_provisioners.create
```

Put this in `extensions/molecule/config.yml` so every scenario shares the pin:

```yaml
---
dependency:
  name: galaxy
  enabled: true
  options:
    requirements-file: extensions/molecule/requirements-test.yml
```

Create `extensions/molecule/default/inventory/hosts.yml`:

```yaml
---
all:
  children:
    molecule:
      vars:
        mp_backend: aws
        mp_defaults:
          aws:
            region: us-east-2
            expected_account_id: '123456789012'
            image_id: ami-0123456789abcdef0
            instance_type: t3.micro
            management_cidrs:
              - 198.51.100.10/32
      hosts:
        instance:
          mp:
            aws: {}
```

Replace the account ID, AMI and management CIDR with your values. The AMI must
exist in `us-east-2` and support cloud-init. Use an EBS-backed Linux image with a
working package manager. The default bootstrap creates `cloud-user`, injects a
generated SSH key, grants passwordless sudo, and installs Python 3 and sudo.
Set the management CIDR to the controller's actual egress address or trusted
network. There is no default inbound access from the internet.

Add your workload to `converge.yml` and assertions to `verify.yml`; both target
`hosts: molecule`. For a small working example, see the tested consumer's
[`converge.yml`](../tests/fixtures/aws-consumer/extensions/molecule/default/converge.yml)
and [`verify.yml`](../tests/fixtures/aws-consumer/extensions/molecule/default/verify.yml).

Run from the consumer root so Molecule discovers the shared config:

```bash
export MOLECULE_GLOB='extensions/molecule/*/molecule.yml'
molecule test -s default
```

For an interactive session, create and prepare the guests, then log in:

```bash
molecule create -s default
molecule prepare -s default
molecule login -s default --host instance
molecule destroy -s default
```

Create allocates infrastructure; prepare waits for authenticated access. Repeated
create reuses the saved instance. Destroy and create again to apply changed launch
settings. No `platforms` section or Molecule EC2 driver is needed.

## Choose networking

The default `network.mode: auto` creates networking only when there are no existing
network references. Set `mode: create` to require a new network, or `mode: existing`
to require an existing one. Invalid or ambiguous selectors fail rather than
creating a replacement.

To change the generated CIDRs, add this under `mp_defaults.aws`:

```yaml
network:
  mode: create
  vpc_cidr: 10.77.0.0/16
  subnet_cidr: 10.77.1.0/24
```

To reuse a subnet, add this instead:

```yaml
network:
  mode: existing
subnet_id: subnet-0123456789abcdef0
```

A subnet identifies its VPC, so `vpc_id` is optional. Supplying only a VPC does
not select a subnet within it. Existing networks and security groups are never
modified or deleted. To reuse security groups, set `security_groups` to their IDs
and ensure their rules already allow management traffic.

For a controller that can reach a private subnet, also set:

```yaml
assign_public_ip: false
connection_address: private
```

The controller must already have a route, VPN or configured SSH proxy to the
guest. The provisioner does not create a NAT gateway, bastion or tunnel.

## Add storage and application ingress

Add host-specific settings under `hosts.instance.mp.aws`, or share them under
`mp_defaults.aws`. Mappings merge recursively; lists replace defaults.

```yaml
root_disk:
  size: 16
  volume_type: gp3
  encrypted: true
volumes:
  - device_name: /dev/sdf
    ebs:
      volume_size: 8
      volume_type: gp3
      encrypted: true
application_ports:
  - name: web
    port: 8080
    protocol: TCP
    cidrs:
      - 198.51.100.10/32
```

Generated disks are disposable and removed during teardown. The guest still needs
to discover, format and mount a data disk; its device name can differ from the EC2
mapping. Root volumes cannot be smaller than the AMI's snapshot. Use
`existing_volumes` for caller-owned volumes that must survive teardown.

Application rules allow traffic but do not start a listener. After create,
`mp_aws_endpoints.web` contains `host`, `port`, `guest_port` and `protocol` for
use in verify tasks. Named endpoints support TCP and UDP. Rules on caller-owned
security groups must be configured separately.

## Use prepared images or Windows

For a Linux image that already has Python and sudo, skip generated cloud-init
and use the AMI's EC2 key injection. Add this under `mp.aws` or `mp_defaults.aws`:

```yaml
ssh_user: ec2-user
key_inject_method: ec2
cloud_init:
  enabled: false
```

Use the AMI's actual login user. For custom structured cloud-init, set
`cloud_init.user_data`; the default merge preserves caller packages and commands.
Opaque shell, MIME or PowerShell payloads belong in `user_data` and require
`key_inject_method: ec2` or `none`.

For Windows, use a Windows AMI and a prepared HTTPS WinRM listener, or provide
PowerShell `user_data` that configures it. Install pywinrm for `connection: winrm`
or pypsrp for `connection: psrp` in the controller. The collection supplies
`ansible.windows`. For a disposable guest with a self-signed listener certificate,
the host override can be:

```yaml
image_id: ami-0123456789abcdef0
instance_type: t3.medium
connection: winrm
key_inject_method: ec2
connection_vars:
  ansible_winrm_transport: ntlm
  ansible_winrm_server_cert_validation: ignore
```

Use a Windows AMI ID in place of the example. For a trusted listener certificate,
set `ansible_winrm_server_cert_validation: validate`. For PSRP, set
`connection: psrp`, replace the WinRM options with `ansible_psrp_auth: ntlm`
and the equivalent `ansible_psrp_cert_validation` policy, and install pypsrp.
The role decrypts the EC2 Administrator password using the matching RSA key
unless `connection_vars.ansible_password` is supplied. See
[Windows and appliances](../roles/aws/README.md#windows-and-appliances) for the
transport contract and appliance readiness requirements.

## Keep state until cleanup finishes

The Molecule ephemeral directory holds `aws_run.yml`, generated keys, private
launch replay files and runtime inventory. Keep it until destroy succeeds;
cleanup uses saved ownership even for hosts removed from inventory. Retain the
`molecule` group and `mp_backend: aws` so the dispatcher can run cleanup.

On failure, inspect private diagnostics and retry destroy with the same state,
account and region. Never publish private replay files, keys, runtime inventories
or console output. See [lifecycle and recovery](../roles/aws/README.md#lifecycle-and-recovery)
for interrupted requests, controller locks and lost-state recovery.

To test changes to the provisioner itself, use the opt-in
[local AWS suite](TESTING.md#aws). The tests run in an execution environment,
exercise the public lifecycle and check cleanup independently through the AWS SDK.
