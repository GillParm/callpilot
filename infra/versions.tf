terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }

  # Keep state somewhere shared once more than one person deploys, e.g.:
  # backend "gcs" {
  #   bucket = "my-terraform-state"
  #   prefix = "callpilot"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
