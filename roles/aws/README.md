# AWS Linux guests

This backend creates disposable EC2 instances in a public VPC/subnet supplied by
the caller. It creates a separate RSA 4096 PEM key pair and security group for each
Molecule invocation. It never creates or removes the caller's network or IAM keys.

Install `amazon.aws`, `community.crypto`, Boto3 and Botocore on the controller.
Credentials use the normal AWS SDK chain, including `AWS_SHARED_CREDENTIALS_FILE`.
Use credentials restricted to the selected deployment, region and guest purpose.

Set `mp_backend: aws` on the `molecule` group. `mp_defaults.aws` supplies common
settings; each host's `mp.aws` overrides them. Required fields are `deployment_id`,
`vpc_id`, `subnet_id` and an exact `image_id`. Defaults are region `us-east-2`,
instance type `t3.medium`, SSH user `ec2-user`, a 30 GiB encrypted gp3 root disk,
and SSH/HTTP access from `0.0.0.0/0`. Set `ssh_cidr` and `http_cidr` to change those
source ranges. The root disk is enlarged when the image requires more space.

```yaml
mp_backend: aws
mp_defaults:
  aws:
    deployment_id: example-deployment
    vpc_id: vpc-0123456789abcdef0
    subnet_id: subnet-0123456789abcdef0
    region: us-east-2
```

Set each host's `mp.aws.image_id` to a selected AMI. Invoke the collection's
`david_igou.molecule_provisioners.create`, `.prepare` and `.destroy` playbooks.
`mp_aws_wait_timeout` defaults to 600 seconds for EC2 and SSH readiness.
`mp_aws_ssh_key_path` defaults to `identity_file` in the Molecule ephemeral directory.
`mp_aws_role_defaults` contains the base host defaults.

Create resumes the saved invocation without starting duplicate instances. Destroy
matches deployment, purpose and run tags, including after a partially failed
create. It waits for instance termination and verifies disk deletion before
removing the run's security group and key pair. Keep `aws_run.yml` after a cleanup
failure and retry destroy from the same ephemeral directory. The saved identity
is removed only after cleanup succeeds. Changing inventory during a saved run
requires destroying that run first. Check mode cannot simulate EC2/SSH readiness;
use the normal lifecycle against a disposable guest account.

License: MIT. Maintainer: David Igou.
