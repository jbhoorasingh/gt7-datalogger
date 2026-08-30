# Running on a Raspberry Pi

The capture workload is light — decrypting and decoding a ~300-byte UDP packet at 60 Hz —
so even a Pi Zero W handles it comfortably. There are two routes:

- **Docker on a 64-bit Pi (recommended)** — Pi Zero 2 W / 4 / 5 running 64-bit
  Raspberry Pi OS can pull a release-tagged `arm64` image and skip everything below.
- **Native install** — required on ARMv6 (original Pi Zero W), or for anyone who would
  rather not run Docker.

## Docker route (Pi Zero 2 W / 4 / 5, 64-bit OS)

Tagged releases publish an `arm64` image. Pull a **release tag**, not `latest`:

```bash
docker run -d --name gt7-datalogger \
  -p 8000:8000 -p 33740:33740/udp \
  -e GT7_PS_IP=<your playstation ip> \
  -v gt7-data:/data \
  ghcr.io/jbhoorasingh/gt7-datalogger:0.1.0
```

That's it. The rest of this page covers the native route.

## Native route

!!! tip "Which Pi?"
    A **Pi Zero 2 W** (quad-core, 64-bit capable) is strongly recommended: on arm64
    every dependency has a prebuilt wheel and the steps below "just work". A **Pi Zero W**
    (single-core, ARMv6) also works but depends on piwheels shipping ARMv6 wheels for the
    Rust-based packages (`pydantic-core`, `watchfiles`) — see the ARMv6 note at the end.

### 1. Build the frontend on your dev machine

Never run `npm run build` on the Pi (slow, and likely to run out of memory). Build on
your laptop and copy the output across:

```bash
# on your dev machine, from the repo root
cd frontend
npm ci
npm run build          # produces frontend/dist

# copy the whole repo (or at least backend/ + frontend/dist) to the Pi
rsync -av --exclude node_modules --exclude .venv ../  pi@raspberrypi.local:~/gt7-datalogger/
```

The backend serves `frontend/dist` automatically when that folder is present — no web
server or reverse proxy needed.

### 2. Prepare the Pi

Use a current **Raspberry Pi OS (Trixie-based)** image, which ships Python 3.12+ (the
project requires ≥ 3.12). On an older Bookworm image you would have to build Python 3.12
yourself.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev build-essential
python3 --version      # must be 3.12 or newer
```

### 3. Install the backend

Raspberry Pi OS points pip at **piwheels**, which provides prebuilt ARM wheels for
`pydantic-core`, `pycryptodome`, and friends — this makes the install fast instead of an
hours-long compile.

```bash
cd ~/gt7-datalogger/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 4. Configure

Set the console IP (or leave it unset for broadcast auto-discovery) in an `.env` file in
the directory you launch from, or as environment variables:

```bash
# ~/gt7-datalogger/backend/.env
GT7_SOURCE=udp
GT7_PS_IP=192.168.1.50        # your PlayStation's IP
GT7_DB_PATH=/home/pi/gt7-data/gt7.db
```

### 5. Run it

```bash
cd ~/gt7-datalogger/backend
source .venv/bin/activate
python -m app.main            # listens on 0.0.0.0:8000
```

Open `http://<pi-ip>:8000` from any device on the LAN. Car names work immediately — the
inventory ships with the app — and refresh themselves in the background after a GT7
content update.

### 6. Start automatically with systemd

```ini
# /etc/systemd/system/gt7-datalogger.service
[Unit]
Description=GT7 Datalogger
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/gt7-datalogger/backend
EnvironmentFile=/home/pi/gt7-datalogger/backend/.env
ExecStart=/home/pi/gt7-datalogger/backend/.venv/bin/python -m app.main
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gt7-datalogger
journalctl -u gt7-datalogger -f      # follow the logs
```

Make sure the Pi and PlayStation share the same 2.4 GHz network and that UDP port 33740
is not firewalled.

!!! warning "ARMv6 (Pi Zero W) note"
    If `pip install` tries to compile `pydantic-core` or `watchfiles` from source (i.e.
    piwheels has no wheel for the exact version), the build can take a very long time or
    exhaust the 512 MB of RAM. Options: pin to a package version piwheels does provide a
    wheel for, add temporary swap for the one-time build, or — the easy path — use a
    **Pi Zero 2 W** on 64-bit Raspberry Pi OS, where prebuilt wheels are always available.
