variable "environment" {
  description = "Environment name; prefixes every resource (dev or prod)."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "snowpipe_queue_arn" {
  description = "notification_channel of the Snowpipe that auto-ingests data/ (SHOW PIPES); null until the pipe exists."
  type        = string
  default     = null
}
