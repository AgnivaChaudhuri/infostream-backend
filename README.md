# Infostream v1 Monorepo

Infostream is a personal intelligence engine with:

- Python daemon backend (`FastAPI`)
- Tauri desktop frontend (Vanilla TypeScript + Vite)
- SQLite persistence
- Source-extension ingestion pipeline
- Deterministic summarizer provider stub with schema validation and fallback digest generation

## Repository Structure

- `backend/`: Python daemon and tests
- `desktop/`: Tauri + frontend app and tests
- `examples/sources/demo_source/`: deterministic source extension for local smoke testing
- `services/`: daemon/service scaffolding
- `docs/ambiguity-defaults.md`: explicit ambiguity defaults

## Prerequisites

- Python 3.12+
- `uv`
- Node.js 20+
- Rust toolchain
- `make`

Ubuntu 24.04 desktop build dependencies (Tauri v2):

```bash
sudo apt install -y \
  pkg-config build-essential \
  libgtk-3-dev \
  libwebkit2gtk-4.1-dev \
  libjavascriptcoregtk-4.1-dev \
  libsoup-3.0-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev
```

## Quickstart

```bash
make setup
make backend-test
make backend-dev
```

In a second terminal:

```bash
cd desktop
npm run dev
```

For full Tauri dev mode:

```bash
make desktop-dev
```

Install the Linux user service unit:

```bash
make install-systemd-user
```

## Build

```bash
make backend-build
make desktop-build
```

`make backend-build` produces a PyInstaller binary at:

- `desktop/src-tauri/bin/infostream-backend`

## API Endpoints

Implemented endpoints:

- `GET /health`
- `GET /sources/installed`
- `GET /sources/enabled`
- `POST /sources/{id}/enable`
- `POST /sources/{id}/disable`
- `POST /sources/install`
- `DELETE /sources/{id}`
- `GET /interests`
- `PUT /interests`
- `GET /llm/current`
- `PUT /llm/current`
- `POST /digest/run`
- `PUT /digest/schedule`
- `GET /digest/current`
- `GET /digest/history`
- `GET /runs/latest`
- `GET /runs/{run_id}`
- `GET /items?refs=<comma-separated-item_refs>`

All responses use:

```json
{
  "ok": true,
  "message": "...",
  "error_code": null,
  "data": {}
}
```

## Demo Source

The bundled source at `examples/sources/demo_source` is installable via:

- UI source install form, or
- `POST /sources/install` with body `{ "path": "/abs/path/to/examples/sources/demo_source" }`

## Service Scaffolding

- Linux systemd user unit: `services/linux/infostream.service`
- macOS launchd placeholder: `services/macos/com.infostream.daemon.plist`
- Windows service placeholder script: `services/windows/install-service.ps1`

## Notes

- Linux daemon behavior is implemented for this pass.
- macOS/Windows runtime service behavior is scaffolded only.
- Real model providers are intentionally out of scope; provider abstraction with deterministic stub is active.
