output "frontend_url" {
  description = "The web app: call console and call log."
  value       = google_cloud_run_v2_service.frontend.uri
}

output "backend_url" {
  description = "The API and WebSocket endpoint."
  value       = google_cloud_run_v2_service.backend.uri
}

output "image_repository" {
  description = "Push images here as backend:<tag> and frontend:<tag>."
  value       = local.registry
}

output "twilio_voice_webhook" {
  description = "Set this as your Twilio number's voice webhook (HTTP POST)."
  value       = "${local.backend_url}/twilio/voice"
}
