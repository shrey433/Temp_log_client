# AWS deployment (EC2, Mumbai)

Terraform for one small EC2 server running the RTD logger server and dashboard behind Caddy, which
gets a Let's Encrypt certificate automatically. Default region `ap-south-1` (Asia Pacific, Mumbai).

## What it creates

- `t4g.micro` (Graviton) Amazon Linux 2023 instance, IMDSv2 only, encrypted root disk, in the default VPC
- Elastic IP, so the address survives replacement
- Security group: 80 and 443 open to the internet, **no SSH** (use SSM Session Manager)
- Separate encrypted 10 GB EBS volume for the SQLite database and certificates, with daily snapshots
  (7 kept) via Data Lifecycle Manager
- Private S3 bucket holding the app bundle, SSM SecureString parameter holding the API token, and an
  instance role that can read only those two
- A random 40-character API token (`RTD_API_TOKEN`)

HTTPS hostname is `<ip-with-dashes>.sslip.io` unless you set `domain_name`.
Rough cost: a few USD per month (instance, volumes, Elastic IP).

## Deploy

Needs Terraform 1.5+ and AWS credentials with rights to create the resources above
(`aws configure`, or `AWS_PROFILE`).

```bash
cd infra
terraform init
terraform plan
terraform apply
```

Wait about 5 minutes after apply for the instance to build and start (log: `/var/log/rtd-bootstrap.log`).
Then:

```bash
terraform output dashboard_url          # open this, sign in with the token
terraform output -raw api_token         # the token
terraform output ingest_url             # SERVER_URL for the firmware
```

Firmware `firmware/include/secrets.h`:

```c
#define SERVER_URL   "<ingest_url>"
#define DEVICE_TOKEN "<api_token>"
```

For https verification also set `SERVER_CA_CERT` to the ISRG Root X1 PEM (Let's Encrypt root).

## Updating

Edit anything under `server/`, then `terraform apply`. A changed bundle replaces the instance; the data
volume, certificates and Elastic IP are kept, so there is a few minutes of downtime and no data loss
(loggers queue rows during that time).

## Operating

- Shell: `terraform output shell_command` (needs the AWS CLI and Session Manager plugin)
- Logs: `docker logs rtd-server`, `docker logs caddy`
- Own domain: set `domain_name`, create an A record to `elastic_ip`, `terraform apply`

## Caveats

- Terraform state (local by default, git-ignored) contains the API token. Keep it private or use an
  encrypted remote backend.
- Anyone with the token can read all data. One shared token for every device and the dashboard.
- Instance replacement drops the single data volume's attachment briefly; a failed apply midway can leave
  the volume detached. Re-run `terraform apply`.
- `terraform destroy` deletes the data volume and snapshots policy. Take a snapshot first if you need the data.
- The `sslip.io` hostname relies on a third-party DNS service; use `domain_name` for anything long-lived.

## Limited IAM policy for the deploy user

`iam-policy.json` grants only what this Terraform needs, instead of `AdministratorAccess`:
EC2/EBS/Elastic IP and security groups in `ap-south-1` only, SSM parameters under `/rtd-logger/`,
S3 buckets named `rtd-logger-bundle-*`, IAM roles and instance profiles named `rtd-logger-*` (and only the
two managed policies the stack attaches), and the snapshot policy. Everything except Describe calls and
DLM is limited by resource name, region or tag.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)   # as an admin, once
sed "s/ACCOUNT_ID/$ACCOUNT_ID/g" iam-policy.json > /tmp/rtd-policy.json
aws iam create-policy --policy-name rtd-logger-deploy --policy-document file:///tmp/rtd-policy.json
aws iam attach-user-policy --user-name rtd-deployer --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/rtd-logger-deploy
```

Or paste the edited JSON in the console (IAM, Policies, Create policy, JSON). It must be a managed policy:
it is too long for an inline user policy.

This policy has not been tested against a real account. If `terraform apply` stops with an
`AccessDenied` error, the message names the missing action; add it and re-run. Note that this limits
what the keys can do, but anyone holding them can still create and delete resources named `rtd-logger-*`.
