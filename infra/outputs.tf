output "dashboard_url" {
  value = "https://${local.host}/"
}

output "ingest_url" {
  description = "Put this in the firmware's secrets.h as SERVER_URL"
  value       = "https://${local.host}/ingest"
}

output "api_token" {
  description = "RTD_API_TOKEN on the server = DEVICE_TOKEN in the firmware = the dashboard sign-in. Show it with: terraform output -raw api_token"
  value       = random_password.api_token.result
  sensitive   = true
}

output "elastic_ip" {
  value = aws_eip.this.public_ip
}

output "instance_id" {
  value = aws_instance.server.id
}

output "shell_command" {
  description = "Open a shell on the server (no SSH needed). Needs the AWS CLI and the Session Manager plugin."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.server.id}"
}
