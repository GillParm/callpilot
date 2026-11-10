# Deploying CallPilot to Google Cloud Run

Two Cloud Run services, `callpilot-api` (FastAPI) and `callpilot-web` (Next.js),
images in Artifact Registry, API keys in Secret Manager. Terraform creates
everything except the images and the secret values.

## 1. Prerequisites

- A Google Cloud project with billing enabled
- `gcloud` (logged in with `gcloud auth login` and `gcloud auth application-default login`)
- Terraform 1.9+
- Docker with `buildx`

```sh
export PROJECT_ID=your-gcp-project REGION=us-central1
gcloud config set project "$PROJECT_ID"
cp terraform.tfvars.example terraform.tfvars   # then edit
terraform init
```

## 2. Create the registry and secret containers

```sh
terraform apply \
  -target=google_artifact_registry_repository.images \
  -target=google_secret_manager_secret.keys
```

Add a value for each secret listed in `secrets` in `terraform.tfvars`. Reading
from stdin keeps keys out of your shell history and out of Terraform state:

```sh
printf '%s' "$GEMINI_API_KEY"    | gcloud secrets versions add GEMINI_API_KEY --data-file=-
printf '%s' "$ANTHROPIC_API_KEY" | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-
```

## 3. Build and push the images

Cloud Run runs `linux/amd64`, so build for that even on Apple silicon:

```sh
REPO=$(terraform output -raw image_repository)
gcloud auth configure-docker "$REGION-docker.pkg.dev"
docker buildx build --platform linux/amd64 -t "$REPO/backend:latest" --push ../backend
docker buildx build --platform linux/amd64 -t "$REPO/frontend:latest" --push ../frontend
```

## 4. Deploy

```sh
terraform apply
terraform output frontend_url
```

To ship a new version, push images with a new tag and run
`terraform apply -var image_tag=<tag>`.

## 5. Optional: a real phone number

1. Add `TWILIO_AUTH_TOKEN` to `secrets` and give it a value (step 2).
2. Run `terraform apply`.
3. In the Twilio console, set your number's voice webhook to
   `terraform output -raw twilio_voice_webhook` (HTTP POST).

## Notes

- **One backend instance.** The call log and schedule live in SQLite on the
  instance's disk, so `max_instance_count` is 1 and data resets when the
  instance is replaced. That is fine for a demo; see the README's trade-offs
  for moving to Cloud SQL.
- **WebSockets.** A call is a single WebSocket request, so the timeout is 60
  minutes (the Cloud Run maximum) and session affinity is on.
- **CPU after the call.** `cpu_idle = false` keeps CPU allocated so the
  after-call summary, which runs after the socket closes, can finish.
