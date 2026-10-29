# Quick start (Docker)

The fastest way to run GT7 Datalogger is with Docker Compose. One container serves the
dashboard, REST API, and WebSocket on port 8000 and listens for PlayStation telemetry on
UDP 33740.

## Prerequisites

- Docker with the Compose plugin
- A PlayStation 4/5 running Gran Turismo 7 on the **same network** as the machine running
  the datalogger
- Motion-data / telemetry is streamed automatically by GT7 — no in-game setting is needed,
  but the console and server must be able to exchange UDP packets

## Run it

From a clone of the repository:

```bash
GT7_PS_IP=<your playstation ip> docker compose up --build
```

Open <http://localhost:8000> and start driving. Laps are recorded automatically whenever
you are on track.

The page opens in your browser's language when it is Spanish, Portuguese or Japanese,
and in English otherwise. The 🌐 menu at the right of the status bar changes it for
this browser only ([Language](../guide/race-engineer.md#language)).

!!! tip "No PlayStation handy?"
    Run the built-in simulated telemetry source instead — it drives laps around a
    synthetic circuit at 60 Hz, complete with lockups, wheelspin, kerb strikes, and
    driver-aid activity:

    ```bash
    GT7_SOURCE=sim docker compose up --build
    ```

## Use the prebuilt image

Images are published to GitHub Container Registry, so you can skip the build entirely:

```bash
docker pull ghcr.io/jbhoorasingh/gt7-datalogger:latest

docker run -d --name gt7-datalogger \
  -p 8000:8000 -p 33740:33740/udp \
  -e GT7_PS_IP=<your playstation ip> \
  -v gt7-data:/data \
  ghcr.io/jbhoorasingh/gt7-datalogger:latest
```

| Tag | Built from | Architectures |
| --- | --- | --- |
| `latest`, `main` | tip of `main`, every push | `amd64` |
| `sha-<short sha>` | a specific commit | `amd64` |
| `X.Y.Z`, `X.Y`, `X` | `vX.Y.Z` release tags | `amd64` + `arm64` |

!!! warning "arm64 (Raspberry Pi 4/5, Zero 2 W)"
    Pull a **release tag** (for example `0.1.0`), not `latest` — only tagged releases
    build the `arm64` image.

With Compose, `docker compose pull && docker compose up -d` runs the published image;
`docker compose up --build` still builds from source.

## Ports

| Port | Protocol | Purpose |
| --- | --- | --- |
| 8000 | HTTP | Dashboard, REST API, WebSocket |
| 33740 | UDP | Telemetry from the PlayStation |
| 33739 | UDP (outbound) | Heartbeat to the PlayStation |

!!! note "Auto-discovery"
    Broadcast auto-discovery of the console requires the container to share the LAN's
    broadcast domain. With Docker's default bridge network, set `GT7_PS_IP` explicitly
    (recommended), or run with `network_mode: host` on Linux.

## Next steps

- Point OBS at the [overlay](../guide/overlay.md) for streaming
- Learn what every panel in the [Live view](../guide/live-view.md) shows
- See all [configuration options](configuration.md)
