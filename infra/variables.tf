variable "region" {
  description = "AWS region. Default is Asia Pacific (Mumbai)."
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = "Graviton (arm64) type. t4g.micro is enough for a handful of loggers posting every 10 s."
  type        = string
  default     = "t4g.micro"
}

variable "data_volume_gb" {
  description = "Size of the separate EBS volume that holds the SQLite database and TLS certificates."
  type        = number
  default     = 10
}

variable "domain_name" {
  description = "Optional own domain. Create an A record pointing at the elastic_ip output. Empty = use <ip>.sslip.io."
  type        = string
  default     = ""
}
