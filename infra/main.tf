locals {
  name = "rtd-logger"
}

# ---- Network (default VPC, no SSH: use SSM Session Manager) ----------------------------------
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_subnet" "selected" {
  id = sort(data.aws_subnets.default.ids)[0]
}

resource "aws_security_group" "web" {
  name_prefix = "${local.name}-"
  description = "HTTP and HTTPS in, everything out"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP (Lets Encrypt challenge and redirect to https)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_eip" "this" {
  domain = "vpc"
}

locals {
  host = var.domain_name != "" ? var.domain_name : "${replace(aws_eip.this.public_ip, ".", "-")}.sslip.io"
}

# ---- Secrets and app bundle -------------------------------------------------------------------
resource "random_password" "api_token" {
  length  = 40
  special = false
}

resource "aws_ssm_parameter" "api_token" {
  name  = "/${local.name}/api-token"
  type  = "SecureString"
  value = random_password.api_token.result
}

resource "random_id" "bucket" {
  byte_length = 4
}

resource "aws_s3_bucket" "bundle" {
  bucket        = "${local.name}-bundle-${random_id.bucket.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "bundle" {
  bucket                  = aws_s3_bucket.bundle.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "archive_file" "server" {
  type        = "tar.gz"
  source_dir  = "${path.module}/../server"
  output_path = "${path.module}/.build/server.tar.gz"
  excludes = [
    "tests/**", "**/__pycache__/**", ".pytest_cache/**", "**/*.db", "**/*.db-wal", "**/*.db-shm",
    "requirements-dev.txt",
  ]
}

resource "aws_s3_object" "server" {
  bucket = aws_s3_bucket.bundle.id
  key    = "server-${data.archive_file.server.output_md5}.tar.gz"
  source = data.archive_file.server.output_path
  etag   = data.archive_file.server.output_md5
}

# ---- Instance role ----------------------------------------------------------------------------
data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name_prefix        = "${local.name}-"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.bundle.arn}/*"]
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.api_token.arn]
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "read-bundle-and-token"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name_prefix = "${local.name}-"
  role        = aws_iam_role.instance.name
}

# ---- Data volume (survives instance replacement) + daily snapshots ----------------------------
resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.selected.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${local.name}-data", Snapshot = local.name }
}

data "aws_iam_policy_document" "assume_dlm" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  name_prefix        = "${local.name}-dlm-"
  assume_role_policy = data.aws_iam_policy_document.assume_dlm.json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "snapshots" {
  description        = "Daily snapshots of the RTD logger data volume - keep 7"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Snapshot = local.name }
    schedule {
      name = "daily"
      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["21:00"] # UTC, about 02:30 IST
      }
      retain_rule {
        count = 7
      }
      copy_tags = true
    }
  }
}

# ---- The server -------------------------------------------------------------------------------
data "aws_ssm_parameter" "ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "server" {
  ami                    = data.aws_ssm_parameter.ami.value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.selected.id
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  # A new app bundle or config changes user_data, which replaces the instance. The data volume and
  # the elastic IP are separate resources, so the database, certificates and address are kept.
  user_data_replace_on_change = true
  user_data = templatefile("${path.module}/user_data.sh.tpl", {
    region    = var.region
    bucket    = aws_s3_bucket.bundle.id
    key       = aws_s3_object.server.key
    param     = aws_ssm_parameter.api_token.name
    host      = local.host
    volume_id = replace(aws_ebs_volume.data.id, "-", "")
  })

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  tags = { Name = local.name }

  depends_on = [aws_iam_role_policy.instance]
}

resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.server.id
  stop_instance_before_detaching = true
}

resource "aws_eip_association" "this" {
  instance_id   = aws_instance.server.id
  allocation_id = aws_eip.this.id
}
