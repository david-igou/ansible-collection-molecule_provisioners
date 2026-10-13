# AWS provisioner

Provision disposable EC2 guests through the collection's public `create`, `prepare`
and `destroy` playbooks. Select `mp_backend: aws`; put shared settings under
`mp_defaults.aws` and per-host overrides under `mp.aws`. Mappings merge recursively;
lists replace defaults. The native inventory overlay supports SSH, WinRM, PSRP and
`network_cli`.

## Quick start

Requires `david_igou.molecule_provisioners` 0.0.7-alpha or later. For a complete
consumer scenario, follow the [AWS walkthrough](../../docs/AWS.md).

The controller needs Python 3.10+, ansible-core 2.17+, `amazon.aws` 9–11,
`community.aws` 9–11, `community.crypto`, boto3, botocore and cryptography. Install
this collection's Galaxy and Python requirements in your execution environment.
Windows additionally needs `ansible.windows` and pywinrm or pypsrp. Appliances
need their platform collection and an Ansible network connection dependency.

Use AWS's normal credential chain or a named SDK profile. Credentials stay outside
inventory and source control. No default region is imposed: set `region` or configure
`AWS_REGION` / the SDK region. Examples and the live test harness use **us-east-2**.

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

Choose a real EBS-backed AMI in the target region. The Linux default creates a
`cloud-user` management user with a generated SSH key and passwordless sudo through
cloud-init, and installs Python 3 and sudo. The AMI must support cloud-init and its
package manager. Prepared images can disable generated bootstrap.

Use the standard one-line imports and scenario configuration in
[the collection README](../../README.md#using). No `platforms` or EC2 driver is
required. `molecule login --host instance` uses SSH from the generated inventory;
other transports use ordinary Ansible commands against that inventory.

## Networking

`network.mode: auto` creates a VPC, public subnet, internet gateway and dedicated
route table when no existing network is selected. One network is shared per run
and region. Existing IDs, filters, security groups or ENIs resolve `auto` to
`existing`. An invalid existing selection fails; it never falls back to creating a
replacement network. No default VPC is needed.

| Network field | Default / behavior |
| --- | --- |
| `mode` | `auto`; explicit `create` or `existing` also supported |
| `vpc_cidr` | `10.77.0.0/24` |
| `subnet_cidr` | VPC CIDR; must be contained in it |
| `availability_zone` | Stable enabled AZ supporting all managed guests and referenced disks |

Managed networks enable DNS support/hostnames, subnet public IPv4 assignment and
`0.0.0.0/0` routing. Internet routing adds no inbound access. Set explicit
`management_cidrs` or `security_group_rules` when creating a security group.
Application ingress also requires explicit sources. NAT gateways, Elastic IPs,
peering and automatic IPv6 networks are outside this backend.

To reuse networking:

```yaml
---
mp_defaults:
  aws:
    region: us-east-2
    image_id: ami-0123456789abcdef0
    network:
      mode: existing
    subnet_id: subnet-0123456789abcdef0
    management_cidrs:
      - 198.51.100.10/32
```

`vpc_id` is optional with a subnet. `subnet_filters` and `vpc_filters` use EC2 filter
names. Selection must identify exactly one eligible subnet and, when supplied,
one VPC. Supplying only a VPC does not request a subnet inside it. Existing
networks, security groups and their rules are never modified or deleted.

## Host settings

| Field | Default / behavior |
| --- | --- |
| `region`, `profile` | Explicit region/profile or SDK configuration |
| `expected_account_id` | Optional account assertion before mutation |
| `image_id` | Exact regional AMI ID; recommended for reproducibility |
| `image_name`, `image_owner`, `image_filters` | Alternative AMI discovery; newest creation date, then AMI ID; owner defaults to `self` |
| `instance_type` | `t3.medium`; AMI architecture and AZ offerings checked |
| `network` | Managed/existing selection above |
| `vpc_id`, `vpc_filters`, `subnet_id`, `subnet_filters` | Existing network selection |
| `assign_public_ip` | `true` for a generated primary interface |
| `connection_address` | `public` or `private`; default `public` |
| `connection_ip` | Explicit reachable management address |
| `security_groups` | Existing group IDs or unambiguous names in one VPC |
| `security_group_name`, `security_group_description` | Generated group prefix and description; names always include run identity |
| `security_group_rules`, `security_group_rules_egress` | Vendor module-shaped rules; existing groups remain unchanged |
| `management_cidrs` | Explicit IPv4/IPv6 management-port sources; no implicit public ingress |
| `key_inject_method` | Linux `cloud-init`, Windows `ec2`, appliance `none` |
| `private_key_path` | Caller key; otherwise generate a private RSA PEM key |
| `key_name` | Existing matching EC2 key pair; otherwise import an owned run key when needed |
| `cloud_init` | `enabled`, `mode: merge` or `replace`, and structured `user_data` |
| `user_data` | Opaque shell, MIME or PowerShell payload; requires `ec2` or `none` key injection |
| `root_disk` | `size`, `volume_type`, `encrypted`, `kms_key_id`, `iops`, `throughput`; encrypted gp3, no shrinking |
| `volumes` | Vendor-style block mappings (`device_name`, `ebs` including snapshot fields); generated EBS disks are disposable |
| `existing_volumes` | `volume_id` and `device_name`; same AZ, never deleted |
| `network_interfaces` | Generated module-shaped interfaces, device indexes, subnet/group/private IP settings |
| `network_interfaces_ids` | Existing `id` / `device_index` references; never delete on termination |
| `iam_instance_profile` | Existing profile name or ARN, applied to the instance |
| `tags` | Added to generated instance, disks and ENIs; ownership and `Name` keys reserved |
| `instance_options` | `placement`, `cpu_options`, `cpu_credit_specification`, `ebs_optimized`, `detailed_monitoring`, `metadata_options`, `instance_initiated_shutdown_behavior` |
| `launch_template` | One `id` or `name`, plus an exact numeric `version` |
| `connection` | `ssh`, `winrm`, `psrp`, `network_cli`; default `ssh` |
| `ssh_user`, `ssh_port` | `cloud-user` / 22; Windows port 5986 |
| `admin_user` | Windows `Administrator` |
| `connection_vars` | `ansible_*` authentication/interpreter/proxy settings; endpoint/transport identity uses the explicit fields |
| `connection_probe` | Required read-only command for authenticated `network_cli` readiness |
| `boot_wait_seconds` | Optional delay after authentication; default 0 |
| `application_ports` | Named entries with `port`, `protocol: TCP` or `UDP`, optional ingress `cidrs` |

Template mode reads the pinned version before launch. Template compute, bootstrap
and device values replace generated defaults; explicit inventory values override
them. Configure management access in the template or inventory. Retaining generated disks,
termination protection and deleting referenced ENIs are rejected. Templates remain
caller-owned. Changing launch configuration requires destroy/create.

With `key_inject_method: ec2`, set `ssh_user` to the AMI default account
(for example `ec2-user` for Amazon Linux), or provide a prepared management user.

Structured cloud-init merges management keys into users by name and preserves
caller packages and commands. `mode: replace` suppresses package additions; key
injection still follows `key_inject_method`. Opaque payloads are passed unchanged
and must provide the software/listeners needed by the chosen connection.

## Windows and appliances

For Windows, select `connection: winrm` or `psrp` and a Windows AMI. Provide an
HTTPS listener through image preparation or PowerShell `user_data`; opening a
security-group port does not configure Windows. Linux bootstrap is disabled.
The role decrypts EC2 Administrator password data with the matching RSA key, or
uses explicit `connection_vars.ansible_password`. Choose transport authentication
and certificate verification through `ansible_winrm_*` / `ansible_psrp_*` options.
Credentials and decrypted passwords are suppressed and the runtime inventory is
private. For RDP, connect using a client that prompts for the password; the
provisioner does not put it on an `xfreerdp` command line.

For appliances, supply `ansible_network_os` in `connection_vars` and a read-only
`connection_probe`. Readiness retries authenticated commands, rather than treating
an open TCP port as success. Use a prepared AMI and explicit bootstrap/key policy.

## Runtime inventory

The private native overlay exports `ansible_host`, transport, user, port and
required authentication fields, plus `mp_aws_instance_id`, `mp_aws_region`,
`mp_aws_vpc_id`, `mp_aws_subnet_id`, `mp_aws_network_mode`, `mp_aws_private_ip`,
`mp_aws_public_ip` and `mp_aws_endpoints`. Each named application endpoint contains
`host`, `port`, `guest_port` and `protocol`. Guest ports are exposed directly;
security-group rules do not start application listeners or forward ports.

With public IPv4, additional generated interfaces attach after the primary
launch; the AMI must configure them. Existing ENIs require private addressing or
an explicit reachable endpoint. Secondary IPv6 interfaces use private-address
launch mode.

Private addressing requires an existing route, VPN or caller SSH proxy settings.
The backend does not create a NAT gateway, bastion or tunnel.

## Lifecycle and recovery

Before creating infrastructure, the role validates every host and saves a private
`aws_run.yml` journal with a random run identity, account, region, selected AMI and
ownership. It journals resources incrementally. Launches use a stable EC2 client
token and an exact private replay request in `aws-requests/`. The vendor instance
module does not accept a caller-provided launch token, so a narrow SDK helper owns
that operation; networking, keys, storage and cleanup use vendor modules. Repeated create
keeps the same instance; changed inventory requires destroy/create. Missing saved
keys, ownership/account mismatch and ambiguous recovery stop with state retained.

Destroy uses the saved run, including hosts removed from the current inventory.
Keep the `molecule` group and backend selector available to dispatch cleanup.
Instances terminate before generated disks/ENIs, security groups, keys and network
foundations are removed. Caller keys, volumes, ENIs and networking survive.
Cleanup failure retains the journal for retry; a successful cleanup removes run
credentials, replay requests and runtime inventory. Check mode performs read-only
validation and creates no journal or AWS resources.

Keep the ephemeral directory until teardown succeeds. A local process lease
prevents concurrent controllers from operating on the same directory. A dead
controller in the same Linux PID namespace is recoverable. When moving a run
between containers/PID namespaces, first verify that the old controller stopped,
then remove only `.aws-lock` before retrying. Do not remove it while a controller
is active.

A pending network create with no discoverable owned candidate remains uncertain,
including during destroy. Inspect the recorded account/region and CloudTrail;
resolve the pending journal entry only after confirming the create outcome.
The role keeps that evidence rather than assuming an absent response means no resource.

If state is lost, normal destroy deliberately does nothing. Recover the manifest
from private backup, or inspect exact account/region resources with both
`molecule-provisioners:managed-by=david_igou.molecule_provisioners` and the original
`molecule-provisioners:run-id` tags. Independently verify IDs and ownership before
manual recovery. Names, CIDRs and scenario labels alone do not establish ownership.
Never publish private replay files, keys, runtime inventories or console output.

Role controls: `mp_aws_wait_timeout: 600`, `mp_aws_windows_wait_timeout: 900`,
`mp_aws_cleanup_timeout: 600`, `mp_aws_max_instances: 10`, `mp_aws_ssh_key_path`
(default ephemeral `identity_file`), `mp_aws_print_diagnostic_tails: false`, and
`mp_aws_role_defaults: {}`. Set them on the controller or molecule inventory group.

## IAM

Use a disposable account or narrowly scoped testing role. The caller needs STS
identity and EC2 describe permissions for AMIs, instance types/offerings, AZs,
instances, volumes, ENIs, keys, groups and selected networking. Lifecycle access
needs `RunInstances`, `TerminateInstances`, `CreateTags`, generated security-group
create/delete and ingress/egress authorizations, `ImportKeyPair` / `DeleteKeyPair`
when EC2 injection is used, volume attachment/attribute modification and generated
volume/ENI deletion. Windows password retrieval needs `GetPasswordData`.

Managed networking additionally needs `CreateVpc`, `ModifyVpcAttribute`,
`DeleteVpc`, `CreateSubnet`, `ModifySubnetAttribute`, `DeleteSubnet`,
`CreateInternetGateway`, `AttachInternetGateway`, `DetachInternetGateway`,
`DeleteInternetGateway`, `CreateRouteTable`, `AssociateRouteTable`,
`DisassociateRouteTable`, `CreateRoute`, and `DeleteRouteTable`. Multi-NIC use
needs ENI creation/attachment/attribute modification. IAM profiles need an
appropriate `iam:PassRole`; encrypted snapshots/KMS keys can require KMS grants.

Use tag conditions where supported, while accounting for create-time tagging and
AWS actions that do not support resource conditions. This is an action inventory,
not a ready-made least-privilege policy. Caller networking needs describe/use
access, not permission to modify its foundations.

See [AWS testing](../../docs/TESTING.md#aws) and
[EC2 driver migration](../../docs/MIGRATION.md#aws-ec2-driver).

Spot/Fleet/ASG orchestration, SSM connections and AMI building are outside this
backend. Pinned templates cannot bypass the on-demand lifecycle through market
options. Appliance probes and IAM profile passthrough require caller-prepared
fixtures; they are covered by validation tests rather than the live test suite.
