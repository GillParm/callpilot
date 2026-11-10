data "google_project" "this" {}

locals {
  # Cloud Run service URLs are deterministic, so each service can be told the
  # other's URL without a dependency cycle.
  backend_name  = "callpilot-api"
  frontend_name = "callpilot-web"
  backend_url   = "https://${local.backend_name}-${data.google_project.this.number}.${var.region}.run.app"
  frontend_url  = "https://${local.frontend_name}-${data.google_project.this.number}.${var.region}.run.app"
  registry      = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
  all_secrets   = toset(["OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "TWILIO_AUTH_TOKEN"])
}

resource "google_project_service" "apis" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# Images ---------------------------------------------------------------------

resource "google_artifact_registry_repository" "images" {
  repository_id = "callpilot"
  format        = "DOCKER"
  location      = var.region
  description   = "CallPilot container images"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  depends_on = [google_project_service.apis]
}

# Secrets --------------------------------------------------------------------

# Empty containers only: values are added out of band with
#   printf '%s' "$KEY" | gcloud secrets versions add GEMINI_API_KEY --data-file=-
# so keys never appear in Terraform state or in this repository.
resource "google_secret_manager_secret" "keys" {
  for_each  = local.all_secrets
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

# Identities -----------------------------------------------------------------

resource "google_service_account" "backend" {
  account_id   = "callpilot-api"
  display_name = "CallPilot API (Cloud Run)"
}

resource "google_service_account" "frontend" {
  account_id   = "callpilot-web"
  display_name = "CallPilot web app (Cloud Run)"
}

# The API may read only the secrets it is configured to use.
resource "google_secret_manager_secret_iam_member" "backend_reads_keys" {
  for_each  = var.secrets
  secret_id = google_secret_manager_secret.keys[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

# Services -------------------------------------------------------------------

resource "google_cloud_run_v2_service" "backend" {
  name                = local.backend_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.backend.email
    # A call is one long-lived WebSocket; Cloud Run allows up to 60 minutes.
    timeout          = "3600s"
    session_affinity = true

    scaling {
      min_instance_count = var.min_instances
      # SQLite lives on the instance's disk, so the demo runs as one instance.
      # Move to Cloud SQL before scaling out (see the README's trade-offs).
      max_instance_count = 1
    }

    containers {
      image = "${local.registry}/backend:${var.image_tag}"

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        # Keep CPU after a call ends so the after-call summary can finish.
        cpu_idle          = false
        startup_cpu_boost = true
      }

      env {
        name  = "CALLPILOT_ENV"
        value = "production"
      }
      env {
        name  = "CLINIC_TIMEZONE"
        value = var.clinic_timezone
      }
      env {
        name  = "PUBLIC_BASE_URL"
        value = local.backend_url
      }
      env {
        name  = "CORS_ORIGINS"
        value = jsonencode([local.frontend_url])
      }

      dynamic "env" {
        for_each = var.secrets
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.keys[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
        }
        period_seconds    = 2
        failure_threshold = 15
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.backend_reads_keys]
}

resource "google_cloud_run_v2_service" "frontend" {
  name                = local.frontend_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.frontend.email

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      image = "${local.registry}/frontend:${var.image_tag}"

      ports {
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        startup_cpu_boost = true
      }

      env {
        name  = "BACKEND_PUBLIC_URL"
        value = local.backend_url
      }
      env {
        name  = "BACKEND_INTERNAL_URL"
        value = local.backend_url
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = var.public ? {
    backend  = google_cloud_run_v2_service.backend.name
    frontend = google_cloud_run_v2_service.frontend.name
  } : {}
  location = var.region
  name     = each.value
  role     = "roles/run.invoker"
  member   = "allUsers"
}
