variable "project_id" {
  description = "Google Cloud project to deploy into."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run and Artifact Registry."
  type        = string
  default     = "us-central1"
}

variable "image_tag" {
  description = "Tag of the backend and frontend images in Artifact Registry."
  type        = string
  default     = "latest"
}

variable "secrets" {
  description = <<-EOT
    API keys to load from Secret Manager into the backend. Terraform creates the
    empty secrets; add values with `gcloud secrets versions add` so they never
    end up in Terraform state. Only list secrets that have a version.
  EOT
  type        = set(string)
  default     = ["GEMINI_API_KEY", "ANTHROPIC_API_KEY"]

  validation {
    condition = alltrue([
      for name in var.secrets :
      contains(["OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "TWILIO_AUTH_TOKEN"], name)
    ])
    error_message = "Supported secrets: OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY, TWILIO_AUTH_TOKEN."
  }
}

variable "clinic_timezone" {
  description = "IANA time zone the clinic's schedule is in."
  type        = string
  default     = "America/Toronto"
}

variable "min_instances" {
  description = "Keep an instance warm to avoid cold starts on the first call (costs money while idle)."
  type        = number
  default     = 0
}

variable "public" {
  description = "Allow unauthenticated access to both services (a public demo)."
  type        = bool
  default     = true
}
