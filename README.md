# narjo-sing-helper

Self-hosted vocal separation for Narjo's Sing feature.

## What it does

`narjo-sing-helper` runs next to your music server, indexes the same music library, and
separates songs into a vocal stem and an instrumental stem on request. It exposes a small
HTTP API that the Narjo app calls to submit songs, poll job status and download the finished
stems. No audio or metadata leaves your server: separation happens on your own hardware, using
model weights your server downloads itself on first use. It is a Python/FastAPI service,
packaged as a Docker image, aimed at the same kind of self-hosters who already run Navidrome,
Jellyfin, Emby or Plex.

## Install

There is no published registry image yet: you build the image locally from a clone of this
repository with `docker compose up -d --build`.

```bash
git clone https://github.com/ayumpo/narjo-sing-helper.git
cd narjo-sing-helper
cp docker-compose.example.yml docker-compose.yml
# edit docker-compose.yml: set SING_MUSIC_DIR and the matching volume mount
docker compose up -d --build
```

Mount your music library read-only, at the same path your music server uses, so paths reported
by Jellyfin, Emby, Plex or Navidrome resolve directly without translation. For example, if your
music server sees the library at `/volume1/data/Media/music`, mount it at that same path in this
container too.

### Synology (Container Manager)

The image is built from this repository's files, so the project folder must contain them.

1. On GitHub, **Code → Download ZIP**, and unzip it into a folder on the NAS, e.g.
   `docker/narjo-sing-helper` (File Station), so that folder holds `Dockerfile` and
   `docker-compose.example.yml`.
2. Copy `docker-compose.example.yml` to `docker-compose.yml` in that folder and set both the
   volume mount and `SING_MUSIC_DIR` to your library, e.g. `/volume1/data/Media/music`.
3. Container Manager → **Project** → **Create** → set **Path** to that folder → it picks up
   `docker-compose.yml` → **Build** → start the project. The first build downloads about 2–3 GB.
4. Open the container's **Log** for the `Access key` line (also saved in `stems/access-key.txt`).

### Proxmox (LXC with Docker)

Run the helper inside an unprivileged LXC container with Docker installed:

1. Create the LXC with nesting enabled, e.g. in its config:
   ```
   features: nesting=1,keyctl=1
   ```
2. Bind-mount the host's music folder into the container, read-only, at the same path your
   music server uses.
3. Install Docker inside the container, clone this repo, and run
   `docker compose up -d --build` as above.

A full VM with Docker works the same way and needs no special LXC features.

### Unraid

There is no Community Applications template yet. Either:

- install the **Docker Compose Manager** plugin and point it at a clone of this repo and your
  edited `docker-compose.yml`, or
- `docker compose up -d --build` from the Unraid CLI, in a clone of this repo.

### Apple silicon Macs

Docker Desktop on macOS cannot access the GPU, so the Docker image falls back to CPU, which is
far slower than native (see the speed table below). A native macOS install using the GPU is
planned but not part of this release.

## Configuration

All variables are read once, at startup.

| Variable | Default | Meaning |
|---|---|---|
| `SING_MUSIC_DIR` | `/music` | Path to the music library, mounted read-only. |
| `SING_MODELS_DIR` | `/models` | Where downloaded model weights are stored. |
| `SING_STEMS_DIR` | `/stems` | Where finished stems, the library index, the job queue and the access key are stored. |
| `SING_PORT` | `8765` | TCP port the helper listens on. |
| `SING_STEM_CACHE_GB` | `50` | Maximum total size of the stems cache, in GB. Least-recently-used stems are evicted first. |
| `SING_RESCAN_MINUTES` | `10` | How often the library index is refreshed. Only files whose size or modification time changed are re-read. |
| `SING_FAST_MODEL` | `htdemucs` | Model used for `now`/`next` jobs. One of `htdemucs`, `kim_vocal_2`, `bs_roformer`. |
| `SING_BEST_MODEL` | `bs_roformer` | Model used for `batch` jobs and background upgrades. Same choices as above. |
| `SING_BEST_UPGRADE` | `auto` | `auto` decides the tier mode from the benchmark; `on` forces two-tier background upgrades even if the benchmark alone would not enable them; `off` disables them. |
| `SING_BEST_HOURS` | unset | Optional `HH:MM-HH:MM` window (e.g. `01:00-07:00`) restricting when best-tier background work is allowed to run. `H:MM` and an en dash separator are also accepted; the two endpoints must differ. Unset means any time. The window uses the **container's local time**, so set `TZ` (e.g. `TZ: America/New_York`) in `docker-compose.yml` if you rely on this. |
| `SING_MAX_MINUTES` | `20` | Songs longer than this (by the indexed duration) are rejected immediately, without decoding. Must be > 0. |
| `SING_REBENCHMARK` | unset | Set to `1` to re-measure hardware speed on the next start instead of using the cached result. The benchmark cache is keyed by helper version plus the two configured models, so re-run this after a hardware change. |

The container's own `SING_MUSIC_DIR`, `SING_MODELS_DIR` and `SING_STEMS_DIR` defaults
(`/music`, `/models`, `/stems`) match the volumes declared in the Dockerfile; you normally only
need to change `SING_MUSIC_DIR` to match your mount, plus optionally `SING_FAST_MODEL`,
`SING_BEST_MODEL`, `SING_BEST_UPGRADE` and `SING_BEST_HOURS`.

## Connecting Narjo

On first start, the helper generates an access key and prints it to the container log on a line
like:

```
Access key (also saved in /stems/access-key.txt): <key>
```

The same key is saved to `/stems/access-key.txt` inside the `/stems` volume. Copy it, then in
Narjo go to **Settings → Integrations → Sing**, enter the helper's URL and the access key, and
use "Test connection" to confirm it works. Sing requires Narjo Pro.

## Status page

Open `http://<host>:8765/` in a browser to check on the helper directly. Without the access key
it just confirms the helper is running and shows how to connect. Paste the key in to see live
status: benchmark state, device, mode, model throughput, the current queue (running and queued
jobs, with progress and ETA), and the last 50 songs that have stems prepared. The page polls
every 5 seconds while visible; tick "Remember on this browser" to keep the key in that browser's
local storage.

## Hardware and speed

Measured speed, in seconds to separate 60 seconds of audio, using `audio-separator` defaults:

| Model | M1 Mac (GPU) | Proxmox i5-12500T (CPU, AVX2) | Synology DS920+ J4125 (CPU, no AVX2) |
|---|---|---|---|
| HTDemucs (2 passes) | ~16 | 54 | 933 |
| Kim Vocal 2 | ~30 | 53 | 396 |
| BS-RoFormer | ~100 (MLX) to ~405 (MPS) | ~1050 | hours |

The helper itself runs HTDemucs with a single pass, roughly half the 2-pass time shown above,
as the fast tier.

On first start (and whenever `SING_REBENCHMARK=1` is set), the helper benchmarks the fast and
best models on your hardware and picks one of three modes:

- **`single`**: the best model is fast enough (≤ 2× real time) to use for every job; there is no
  separate fast tier.
- **`two-tier`**: the best model is usable in the background (≤ 30× real time, or
  `SING_BEST_UPGRADE=on`). `now`/`next` jobs use the fast model immediately; the helper then
  re-separates those songs with the best model at low OS priority and replaces the stems once
  done, and all `batch` jobs use the best model directly.
- **`fast-only`**: the best model is too slow to be worth running at all by default. Only the
  fast model is used, unless `SING_BEST_UPGRADE=on` forces two-tier mode anyway.

`GET /v1/health` reports the active mode plus measured throughput, so you can see which tier
your hardware landed in.

## Model licenses

Model weights are not shipped with this project or baked into the Docker image. Your own server
downloads them on first use from the public UVR model repository.

- **HTDemucs** is MIT-licensed.
- The **BS-RoFormer (viperx)** weights carry no published license. They are used here only on
  your own hardware, for your own library, and are never redistributed by this project.

## API

The helper listens on port 8765. Every `/v1/...` route except `GET /v1/ping` requires the header
`X-Narjo-Sing-Key` with the access key described above. `GET /` (the status page above) also
needs no key, but shows nothing sensitive until a key is entered in the browser.

- `GET /v1/ping` — `{ok: true}`. No key required; used as the container health check.
- `GET /v1/health` — helper and benchmark status:
  `{version, device, state, error, mode, fastModel, bestModel, fastSecondsPerMinute,
  bestSecondsPerMinute, queueLength, backgroundPrepRecommended, libraryFiles}`.
  `state` is `benchmarking`, `ready` or `error`; `error` is set only in the `error` state.
  `mode` is `single`, `two-tier` or `fast-only` once benchmarking has finished.
- `GET /v1/queue` — read-only snapshot for the status page:
  `{jobs: [{id, relPath, model, priority, state, progress, etaSeconds, created, updated}],
  recent: [{relPath, quality, model, finished}]}`. `jobs` lists queued and running jobs in the
  order the worker will run them (the running job, if any, first; then by priority and creation
  time). `recent` lists the last 50 distinct songs with stems, most recently finished first.
- `POST /v1/jobs` — submit a song:
  request `{clientSongId, title, durationSeconds, path?, albumArtist?, album?, disc?, track?,
  priority}` (`priority` is `now`, `next` or `batch`); response
  `{clientSongId, jobId?, state, etaSeconds?, matchedBy?, searched?}`.
  `state` is one of `queued|running|done|failed|notFound|expired`. On `notFound`, `searched`
  lists the matching strategies that were tried instead of a `jobId`.
- `POST /v1/jobs/batch` — submit several songs at once: request `{songs: [...], priority:
  "batch"}`, with each entry shaped like a `POST /v1/jobs` request; response is a list of
  `{clientSongId, jobId?, state, etaSeconds?, matchedBy?, searched?}`.
- `POST /v1/stems/lookup` — check readiness for up to 200 songs without creating any jobs:
  request `{songs: [...]}`, each entry shaped like a `POST /v1/jobs` request minus `priority`
  (1–200 songs, else 422); response is a list in request order of
  `{clientSongId, ready, quality}`. `ready` is true only when the song matches a library file
  and stems already exist for it; `quality` (`fast`, `best`, or `null`) is the tier of those
  stems, `null` when `ready` is false.
- `GET /v1/jobs/{jobId}` — `{state, progress, etaSeconds?, error?, quality, upgradePending}`.
  `progress` is 0…1. `quality` (`fast` or `best`) is the tier of the stems currently available.
  `upgradePending` is true while a best-tier upgrade for this song is queued or running.
- `GET /v1/jobs/{jobId}/vocals.m4a` and `GET /v1/jobs/{jobId}/instrumental.m4a` — the two stems,
  AAC 256 kbps 44.1 kHz stereo, with `ETag` and HTTP Range support. The `ETag` changes when a
  best-tier upgrade replaces the stems.
