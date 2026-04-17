# OpenGist Gist Mirror

Self-hostable FastAPI application that mirrors all gists from one authenticated GitHub account into a single self-hosted OpenGist instance.

## Features

- Web UI for configuring GitHub/OpenGist credentials
- Manual sync trigger
- Scheduled sync with configurable interval
- Sync history and mirrored gist status table
- OpenGist sync through **Git over HTTP** (stable path)
- Docker-first deployment

## Requirements

- Docker + Docker Compose (recommended), or Python 3.12+
- A GitHub token with gist access
- Your OpenGist username
- An OpenGist access token

## Run with Docker

1. Clone this repository.
2. Build and start the service:

```bash
docker compose up --build -d
```

3. Open the web UI:

```
http://localhost:8000
```

4. Configure:
    - OpenGist URL (for example `https://opengist.example.com`)
    - OpenGist username
    - GitHub token
    - OpenGist password or token (legacy `username:token` also supported)
    - Sync interval
    - If this app runs in Docker, do not use `localhost` for OpenGist unless OpenGist is in the same container. Use `host.docker.internal:<port>` (OpenGist on host) or the OpenGist service name on a shared Docker network.

## Docker networking for OpenGist

- `localhost` inside the mirror container points back to the mirror container, not your host machine.
- This project’s `docker-compose.yml` includes `extra_hosts: ["host.docker.internal:host-gateway"]` so host networking works on Linux Docker as well.
- Recommended OpenGist URL values when mirror runs in Docker:
  - OpenGist running on host: `http://host.docker.internal:6157`
  - OpenGist running in another container on same network: `http://<opengist-service-name>:6157`

## OpenGist write mode

- The mirror writes gists using **Git push**, not the OpenGist REST API.
- This follows OpenGist docs where git-based creation/update is the stable workflow.
- `OpenGist password/token` supports:
  - account password or token together with `OpenGist username`, or
  - legacy `username:token` format for backward compatibility.

## Run locally (without Docker)

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
uvicorn app.main:app --reload
```

## Notes

- The app is intentionally single-tenant in v1.
- SQLite data is stored under `./data` when running with Docker Compose.
- Tokens are stored in the local database for UI-driven operation.
- OpenGist URLs without a scheme are normalized to `http://...`.

