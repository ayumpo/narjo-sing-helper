# Narjo Sing helper

Sing lets Narjo turn the singer's voice down, or off, so you can sing along — like Apple
Music Sing, but for your own music library.

This helper does the heavy lifting. It runs on your own server, next to Navidrome, Plex, Jellyfin or Emby,
and separates each song into "vocals" and "music". Your music never leaves your home network.

## What you need

- A computer that is always on and can see your music folder, with **Docker**: a Synology NAS, a home
  server (Proxmox, Unraid, Ubuntu…) or a Mac or Windows PC with Docker Desktop.
- About **10 GB of free space** (the helper itself, its voice-separation models, and prepared songs).
- Narjo on your iPhone or iPad, on the same home network (or connected through a VPN such as Tailscale).

## How fast is it?

It depends entirely on the computer it runs on:

| Your server | A new song is ready in about… |
|---|---|
| Modern mini PC or desktop (for example Intel Core i5-12500T) | **2–3 minutes**. Narjo prepares the next songs in your queue while you listen. |
| Low-power NAS (for example Synology DS920+, Intel Celeron J4125) | **30 minutes to an hour**. Fine for preparing songs ahead of time, too slow to tap the mic and sing right away. |

Once a song is prepared, it starts in Sing within a second, every time.

## Install

It takes about 15 minutes of your time, plus a while for your server to set itself up the first time.

### 1. Download

At the top of this page, click the green **Code** button → **Download ZIP**, and unzip it. You get a folder
called `narjo-sing-helper-main`. Rename it to **`narjo-sing-helper`**.

### 2. Tell it where your music is

Open the file **`docker-compose.yml`** in that folder with TextEdit (Mac) or Notepad (Windows), and find this
line:

```yaml
      - /volume1/music:/music:ro
```

`/volume1/music` is where a Synology NAS keeps music by default. If your music is somewhere else, replace
**only** `/volume1/music` with your music folder, and save. Leave `:/music:ro` as it is.

> **Where is my music folder?** Use the folder your music server (Navidrome, Plex, Jellyfin or Emby) reads.
> On Synology: File Station → right-click your music folder → **Properties** → **Location**.
> On Unraid it is usually `/mnt/user/music`.

### 3. Start it

**Synology (Container Manager)**

1. Open **File Station**, go into the **`docker`** shared folder, and drag your `narjo-sing-helper` folder
   into it.
2. Open **Container Manager** → **Project** → **Create**.
   - **Project name:** `narjo-sing`
   - **Path:** `docker/narjo-sing-helper`
   - When it finds the `docker-compose.yml` file, choose to **use the existing** one.
3. Click **Next**, then **Done**. The first start builds the helper on your NAS: it downloads about 3 GB and
   can take 20–40 minutes. You can close the window; it keeps going.

**Unraid, Ubuntu, or any computer with Docker**

Copy the `narjo-sing-helper` folder to your server, open a terminal in it, and run:

```bash
docker compose up -d --build
```

(On older systems the command is `docker-compose up -d --build`.)

**Proxmox (Debian container)**

1. Select the container → **Options** → **Features** → tick **keyctl** and **Nesting** → restart it.
2. Give it your music folder, read-only. In the **Proxmox host's** Shell (replace `106` with your
   container's number, and `/mnt/music` with your music folder):
   ```bash
   pct set 106 -mp0 /mnt/music,mp=/music,ro=1
   ```
3. In the **container's** Console:
   ```bash
   apt update && apt install -y git docker.io docker-compose
   git clone https://github.com/ayumpo/narjo-sing-helper.git && cd narjo-sing-helper
   sed -i 's#/volume1/music:/music#/music:/music#' docker-compose.yml
   docker-compose up -d --build
   ```

## Connect Narjo

1. **Get the access key.** It is saved in the `stems` folder next to your compose file, in a file called
   `access-key.txt` (on Synology: File Station → `docker/narjo-sing/stems/access-key.txt`). It is also printed
   in the container's log on a line starting with `Access key`.
2. In Narjo, open **Settings → Integrations → Sing** and enter:
   - **Helper address:** `http://YOUR-SERVER-IP:8765` (for example `http://192.168.1.20:8765`)
   - **Access key:** the key from step 1
3. Tap **Test Connection**.
4. Play a song, open the **lyrics**, and tap the **microphone** above the "…" button.

## Fast, Best or both

When your Narjo app includes these settings (coming in an update), **Settings → Integrations → Sing → Quality**
chooses how songs are prepared on that phone:

- **Automatic** (the default): the helper decides from its own speed. A fast computer makes the better
  version straight away, a medium one makes the fast version first and the better one later, and a slow
  NAS makes only the fast version, so it isn't busy for hours.
- **Fast**: the fast version only. Ready soonest.
- **Best**: only the better version. The first time you sing a song you wait longer.
- **Both**: the fast version first, so you can sing right away; the better version replaces it when it's ready.

Narjo shows how long each choice takes on your helper. People who share one helper can each choose their own.

## Pause or cancel

- **Pause** stops the helper working without losing anything: the song in progress freezes where it is and
  carries on when you press **Resume**. Nothing new starts while it's paused, and it stays paused after a restart.
- **Cancel** stops one song for good. It isn't prepared again until someone asks for it.

You can do both on the helper's own page at `http://YOUR-SERVER-IP:8765`. Once your Narjo app includes these
controls (coming in an update), you can also do them in Narjo (**Settings → Integrations → Sing → Helper
Queue**, or the message above the microphone while a song is being prepared).

## Check that it's working

Open `http://YOUR-SERVER-IP:8765` in a web browser. It confirms the helper is running. Paste your access key
there to see what it is doing: the songs it is preparing right now, their progress, and the songs that are
ready.

## Troubleshooting

- **Test Connection fails.** Check that your phone is on the same network as the server, that the address
  starts with `http://` and ends with `:8765`, and that a firewall isn't blocking port 8765 (Synology:
  Control Panel → Security → Firewall).
- **"Song not found".** The helper can't see that song's file. Make sure the music line in the compose file
  points at the same music folder your music server uses.
- **The mic keeps spinning.** The helper is still preparing the song. Open the status page above to see its
  progress; on slower servers this takes a while.
- **Updating.** Download the new ZIP and replace the files in your `narjo-sing-helper` folder, but keep
  its `models` and `stems` folders (they hold your prepared songs and access key). Then build again:
  Container Manager → **Project** → `narjo-sing` → **Action** → **Build**; or `docker compose up -d --build`.

## Licenses and your music

**This helper** is open-source software under the [MIT License](LICENSE), provided "as is", without warranty.

**It doesn't include anyone else's software or AI models.** When you build and run it, *your* server downloads
each of the components below directly from its publisher, under that component's own license. Narjo does not
distribute them.

| Component | What it does | License |
|---|---|---|
| Python, FastAPI, Uvicorn, NumPy, SoundFile and their libraries | Run the helper | PSF, MIT, BSD, Apache 2.0 |
| PyTorch | Runs the AI models | BSD 3-Clause |
| audio-separator | Loads and runs the separation models | MIT |
| ONNX Runtime | Runs some models | MIT |
| FFmpeg (Debian package) | Reads and writes audio files | LGPL / GPL |
| soxr | Resamples audio | LGPL 2.1 or later |
| Mutagen | Reads song tags (title, album…) | GPL 2.0 or later |
| HTDemucs model, by Meta | Quick vocal separation | MIT (Meta's Demucs project; Meta published no separate license for the trained weights) |
| MelBand RoFormer vocal model, by Kimberley Jensen | Best-quality vocal separation | MIT |

audio-separator also lists a package called `diffq`, which is licensed for non-commercial use only. This helper
never uses it (it only matters for compressed models the helper doesn't load), so the helper installs a tiny
stand-in of its own instead (`third_party/diffq_stub`, MIT) and the real `diffq` is never downloaded.

### Which models it uses

**The helper only uses AI models with an open license (MIT):** HTDemucs for the quick version and Kimberley
Jensen's MelBand RoFormer for the best-quality version. It cannot download or run any other model.

On slow hardware you can use HTDemucs for everything, which is lighter (the voice is removed a little less
cleanly). Add one `environment` line to `docker-compose.yml`, so the helper part looks like this:

```yaml
  narjo-sing-helper:
    build: .
    image: narjo-sing-helper:latest
    container_name: narjo-sing-helper
    restart: unless-stopped
    environment:
      SING_BEST_MODEL: htdemucs
```

### Your music

- Use Sing only with music you own or are licensed to use, for your own personal, private enjoyment.
- The "vocals" and "music" versions are made and kept on your own server and your own devices. Nothing is sent
  to Narjo or to anyone else.
- Don't share, publish, sell or perform them publicly unless you have the rights to do so.

### Trademarks

Apple Music Sing is a trademark of Apple Inc. Narjo is not affiliated with, endorsed by or sponsored by Apple.
Navidrome, Plex, Jellyfin, Emby, Synology, Proxmox, Unraid, Docker and Portainer are trademarks of their
respective owners.

## For technical users

### How it works

On first start the helper indexes the mounted library, generates an access key, and benchmarks two models
on your hardware: a fast one (HTDemucs) that answers the mic right away, and a best one (MelBand RoFormer) that
re-separates songs in the background, at low priority, when it is fast enough to be worthwhile. Narjo asks
for the current song and the next two; finished stems are cached on both the server and the phone.

### Settings

All variables are read once, at startup.

| Variable | Default | Meaning |
|---|---|---|
| `SING_MUSIC_DIR` | `/music` | Path to the music library, mounted read-only. |
| `SING_MODELS_DIR` | `/models` | Where downloaded model weights are stored. |
| `SING_STEMS_DIR` | `/stems` | Where finished stems, the library index, the job queue and the access key are stored. |
| `SING_PORT` | `8765` | TCP port the helper listens on. |
| `SING_STEM_CACHE_GB` | `50` | Maximum total size of the stems cache, in GB. Least-recently-used stems are evicted first. |
| `SING_RESCAN_MINUTES` | `10` | How often the library index is refreshed. Only files whose size or modification time changed are re-read. |
| `SING_FAST_MODEL` | `htdemucs` | Model used for `now`/`next` jobs: `htdemucs` or `melband_kim`. |
| `SING_BEST_MODEL` | `melband_kim` | Model used for `batch` jobs and background upgrades. Same choices as above; set it equal to `SING_FAST_MODEL` to use one model for everything. Changing it moves already-queued jobs to the new model on the next start. |
| `SING_BEST_UPGRADE` | `auto` | `auto` decides the tier mode from the benchmark; `on` forces two-tier background upgrades even if the benchmark alone would not enable them; `off` disables them. |
| `SING_BEST_HOURS` | unset | Optional `HH:MM-HH:MM` window (e.g. `01:00-07:00`) restricting when best-tier background work is allowed to run. `H:MM` and an en dash separator are also accepted; the two endpoints must differ. Unset means any time. The window uses the **container's local time**, so set `TZ` (e.g. `TZ: America/New_York`) in `docker-compose.yml` if you rely on this. |
| `SING_MAX_MINUTES` | `20` | Songs longer than this (by the indexed duration) are rejected immediately, without decoding. Must be > 0. |
| `SING_REBENCHMARK` | unset | Set to `1` to re-measure hardware speed on the next start instead of using the cached result. The benchmark cache is keyed by helper version plus the two configured models, so re-run this after a hardware change. |

Mount your library at `/music` (as `docker-compose.yml` does) and you need none of these.
Songs are matched by the end of their file path, so the folder may sit at a different path here
than on your music server.

### Models and speed in detail

Measured speed, in seconds to separate 60 seconds of audio, using `audio-separator` defaults:

| Model | M1 Mac (GPU) | Proxmox i5-12500T (CPU, AVX2) | Synology DS920+ J4125 (CPU, no AVX2) |
|---|---|---|---|
| HTDemucs (2 passes) | ~16 | 54 | 933 |
| MelBand RoFormer (Kim) | not measured | ~280 | not measured |

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

### Docker on a Mac

Docker Desktop on macOS can't use the Mac's GPU, so the helper runs on the CPU there.

### API

The helper listens on port 8765. Every `/v1/...` route except `GET /v1/ping` requires the header
`X-Narjo-Sing-Key` with the access key described above. `GET /` (the status page above) also
needs no key, but shows nothing sensitive until a key is entered in the browser.

- `GET /v1/ping` — `{ok: true}`. No key required; used as the container health check.
- `GET /v1/health` — helper and benchmark status:
  `{version, device, state, error, mode, fastModel, bestModel, fastSecondsPerMinute,
  bestSecondsPerMinute, queueLength, backgroundPrepRecommended, libraryFiles, qualityChoices, paused,
  configuredBestModel, bestStatus}`.
  `state` is `benchmarking`, `ready` or `error`; `error` is set only in the `error` state.
  `mode` is `single`, `two-tier` or `fast-only` once benchmarking has finished. `qualityChoices` is `true`
  on helpers that accept `quality`. `bestStatus` is `pending`, `measured`, `tooSlow` (slower than 30× real
  time) or `unavailable` (the better model's speed test failed, for example because it couldn't be downloaded).
- `GET /v1/queue` — snapshot for the status page:
  `{paused, jobs: [{id, relPath, model, priority, state, progress, etaSeconds, created, updated,
  requestedQuality, quality}], recent: [{relPath, quality, model, finished}]}`. `jobs` lists queued and
  running jobs in the order the worker will run them (the running job, if any, first; then by priority and
  creation time). `quality` is the label the job's copy will get. `recent` lists the last 50 distinct songs
  with stems, most recently finished first.
- `POST /v1/queue/pause` and `POST /v1/queue/resume` — `{paused}`. While paused nothing new starts and the
  running separation is frozen; the switch survives a restart.
- `POST /v1/jobs` — submit a song:
  request `{clientSongId, title, durationSeconds, path?, albumArtist?, album?, disc?, track?, priority, quality?}` (`priority` is `now`, `next` or `batch`); response
  `{clientSongId, jobId?, state, etaSeconds?, matchedBy?, searched?}`.
  `quality` is `auto` (the default), `fast`, `best` or `both`.
  `state` is one of `queued|running|done|failed|cancelled|notFound|expired`. On `notFound`, `searched`
  lists the matching strategies that were tried instead of a `jobId`.
- `POST /v1/jobs/batch` — submit several songs at once: request `{songs: [...], priority: "batch", quality?}`, with each entry shaped like a `POST /v1/jobs` request; response is a list of
  `{clientSongId, jobId?, state, etaSeconds?, matchedBy?, searched?}`.
- `POST /v1/stems/lookup` — check readiness for up to 200 songs without creating any jobs:
  request `{songs: [...], quality?}`, each entry shaped like a `POST /v1/jobs` request minus `priority`
  (1–200 songs, else 422); response is a list in request order of
  `{clientSongId, ready, quality}`. `ready` is true only when the song matches a library file
  and stems that the requested `quality` accepts already exist for it: with `quality: "best"`,
  only `best` stems count. The response's `quality` (`fast`, `best`, or `null`) is the tier of
  the stems the helper has, `null` when it has none.
- `GET /v1/jobs/{jobId}` — `{state, progress, etaSeconds?, error?, quality, upgradePending, helperPaused}`.
  `progress` is 0…1. `quality` (`fast` or `best`) is the tier of the stems currently available.
  `upgradePending` is true while a best-tier upgrade for this song is queued or running.
- `POST /v1/jobs/{jobId}/cancel` — cancels every queued or running job for that song, its upgrade included:
  `{cancelled: <count>}`. Unknown id → 404. A cancel that arrives once the separation has its result keeps it.
- `GET /v1/jobs/{jobId}/vocals.m4a` and `GET /v1/jobs/{jobId}/instrumental.m4a` — the two stems,
  AAC 256 kbps 44.1 kHz stereo, with `ETag` and HTTP Range support. The `ETag` changes when a
  best-tier upgrade replaces the stems.
