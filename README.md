# docker-flarum

[Flarum](https://flarum.org/) container image — a PikaPods fork of
[`crazy-max/docker-flarum`](https://github.com/crazy-max/docker-flarum) (MIT),
rebuilt on a predictable schedule against a freshly patched Alpine.

This image powers Flarum on [PikaPods](https://www.pikapods.com) and is
maintained by the PikaPods team. It's published here for our users' reference
and the benefit of the wider community. To run your own Flarum pod from
$1.9/month, see
[pikapods.com/pods?run=flarum](https://www.pikapods.com/pods?run=flarum).

Published to both `ghcr.io/pikapods/docker-flarum` and
`pikapods/docker-flarum` (Docker Hub) — pick whichever registry you prefer.
Three tag patterns are pushed per build:

| Tag         | Mutability | Use for                                                       |
|-------------|------------|---------------------------------------------------------------|
| `latest`    | mutable    | Most recent build of the most recent in-series stable          |
| `1.8.19`    | mutable    | Pin to a Flarum version; auto-receive Alpine/base patches      |
| `1.8.19-r1` | immutable  | Byte-for-byte reproducibility; never reused                    |

Source: https://github.com/pikapods/docker-flarum

## Why this fork

Upstream `crazymax/flarum` is a well-built image, and its runtime is kept here
**verbatim** — the s6-overlay v2 init scripts, the `extension` helper, the
scheduler, and the nginx/PHP templates are all upstream's, unmodified. What
this fork changes is the build and the release cadence:

- **Predictable rebuilds.** Upstream publishes in bursts (1.8.10 → 1.8.17 was
  a 13-month gap), and a versioned tag only appears when the maintainer cuts a
  release — `crazymax/flarum:1.8.17` sat unrebuilt while Alpine and PHP CVEs
  accumulated underneath it. PikaPods needs those picked up on a clock, so
  every build runs `apk upgrade` and bumps the `-rN` revision that the mutable
  version tag follows.
- **Reproducible builds.** CI pins the base image by digest and records it in
  `org.opencontainers.image.base.digest`, so a rebuild can be attributed to a
  specific base rather than a floating tag.
- **A test suite.** 80-odd assertions across an image lane and a runtime lane
  that boots against MariaDB, covering the install, the `/data` layout, the
  scheduler sidecar and the extension persistence cycle.

This image tracks the **Flarum 1.8 series only**. Flarum 2.0 is still at
release-candidate stage with no GA date.

## Quick start

The bundled `compose.yaml` brings up Flarum, MariaDB and the scheduler sidecar
with zero external dependencies — the fastest way to try the image:

```bash
git clone https://github.com/pikapods/docker-flarum.git
cd docker-flarum
docker compose up -d
# wait ~60s for the first-boot install
curl -I http://localhost:8000/     # → HTTP/1.1 200 OK
```

Flarum installs itself with the default credentials **`flarum` / `flarum`** —
log in at http://localhost:8000/ and change them immediately.

Against an existing database:

```bash
docker run -d --name flarum \
  -v flarum-data:/data \
  -e FLARUM_BASE_URL=https://forum.example.com \
  -e DB_HOST=db.internal \
  -e DB_NAME=flarum \
  -e DB_USER=flarum \
  -e DB_PASSWORD=... \
  -p 8000:8000 \
  ghcr.io/pikapods/docker-flarum:latest
```

`FLARUM_BASE_URL` is mandatory — the container refuses to boot without it, and
Flarum bakes it into generated asset and redirect URLs at install time.

## Environment variables

Upstream's full reference lives in
[crazy-max/docker-flarum](https://github.com/crazy-max/docker-flarum#environment-variables);
this fork changes none of it. The commonly-used subset:

### Database

| Var           | Required | Default    | Purpose                                          |
|---------------|----------|------------|--------------------------------------------------|
| `DB_HOST`     | yes      | —          | MySQL/MariaDB hostname.                          |
| `DB_PORT`     | no       | `3306`     | DB port.                                         |
| `DB_NAME`     | no       | `flarum`   | Database name.                                   |
| `DB_USER`     | no       | `flarum`   | DB user. `DB_USER_FILE` also supported.          |
| `DB_PASSWORD` | yes      | —          | DB password. `DB_PASSWORD_FILE` also supported.  |
| `DB_PREFIX`   | no       | `flarum_`  | Table prefix.                                    |
| `DB_NOPREFIX` | no       | `false`    | Set `true` to force an empty prefix.             |
| `DB_TIMEOUT`  | no       | `60`       | Seconds to wait for the database on boot.        |

### Flarum

| Var                             | Required | Default             | Purpose                                             |
|---------------------------------|----------|---------------------|-----------------------------------------------------|
| `FLARUM_BASE_URL`               | **yes**  | —                   | Public URL, including scheme. Boot fails if unset.  |
| `FLARUM_FORUM_TITLE`            | no       | `Flarum Dockerized` | Forum title (first install only).                   |
| `FLARUM_DEBUG`                  | no       | `false`             | Debug mode. Leave off in production.                |
| `FLARUM_API_PATH`               | no       | `api`               | API path segment.                                   |
| `FLARUM_ADMIN_PATH`             | no       | `admin`             | Admin path segment.                                 |
| `FLARUM_COOKIE_SAMESITE`        | no       | `lax`               | Session cookie SameSite policy.                     |
| `FLARUM_REFERRER_POLICY`        | no       | `same-origin`       | `Referrer-Policy` header.                           |
| `FLARUM_POWEREDBY_HEADER`       | no       | `true`              | Emit `X-Powered-By`.                                |
| `FLARUM_ANNOUNCEMENTS_DISABLED` | no       | `false`             | Suppress in-admin announcements.                    |

### Container

| Var                | Default       | Purpose                                                          |
|--------------------|---------------|------------------------------------------------------------------|
| `PUID` / `PGID`    | `1000`        | UID/GID the `flarum` user is remapped to at boot.                |
| `TZ`               | `UTC`         | Timezone.                                                        |
| `MEMORY_LIMIT`     | `256M`        | PHP `memory_limit`.                                              |
| `UPLOAD_MAX_SIZE`  | `16M`         | PHP + nginx upload ceiling.                                      |
| `OPCACHE_MEM_SIZE` | `128`         | OPcache size in MB.                                              |
| `LISTEN_IPV6`      | `true`        | Set `false` to drop the IPv6 nginx listener.                     |
| `REAL_IP_FROM`     | `0.0.0.0/32`  | Trusted proxy CIDR for `X-Forwarded-For`.                        |
| `REAL_IP_HEADER`   | `X-Forwarded-For` | Header carrying the client IP.                               |
| `SIDECAR_CRON`     | `0`           | `1` turns the container into a scheduler-only sidecar.           |
| `CRON_SCHEDULE`    | `* * * * *`   | Scheduler cadence (sidecar mode only).                           |

## Scheduler

Flarum's scheduled tasks (mail digests, extension jobs) are not driven by web
requests. Run a second container from the same image with `SIDECAR_CRON=1`
against the same `/data` volume and database — `compose.yaml` shows the shape.
Without it the forum works, but anything scheduled never fires.

## Extensions

Extensions are installed with Composer and mirrored into
`/data/extensions/list`, which is replayed on every boot — so they survive
container recreation and image upgrades:

```bash
docker compose exec flarum extension require fof/upload
docker compose exec flarum extension list
docker compose exec flarum extension remove fof/upload
```

Editing `/data/extensions/list` by hand still works, but is no longer
necessary: the Composer post-install hook keeps the file in sync
automatically.

Note that the vendor tree itself lives inside the image, not on the volume.
Recreating the container reinstalls everything in the list from scratch.

## Mounts

| Path          | Purpose                                                                        |
|---------------|--------------------------------------------------------------------------------|
| `/data`       | Persistent volume: `assets/`, `extensions/`, `storage/`, `extensions/list`.     |
| `/opt/flarum` | Flarum source + vendor tree. Baked at build time — do **not** bind-mount.       |

## Ports

| Port | Purpose                                              |
|------|------------------------------------------------------|
| 8000 | HTTP. Terminate TLS at a reverse proxy in front.     |

## User & permissions

s6-overlay v2 requires PID 1 to run as root; nginx and php-fpm are dropped to
the `flarum` account (UID/GID **1000** by default) via `s6-setuidgid` during
boot. Set `PUID` / `PGID` to remap that account — the init scripts rewrite
`/etc/passwd` and re-own `/data` before any service starts, so bind mounts
work without host-side `chown`.

## Building locally

```bash
docker build --build-arg FLARUM_VERSION=1.8.19 -t flarum:test .
```

`FLARUM_VERSION` is plain semver with no leading `v`. It is handed to Composer
as-is and flows into `org.opencontainers.image.version` as
`<version>-<revision>`.

> **Do not add a `v` prefix anywhere in the version handling.** Upstream is
> inconsistent about it — 1.8.16 through 1.8.18 are tagged `v1.8.x`, but
> 1.8.19 is tagged bare. Composer normalises the constraint either way, so
> passing the plain version always works; a v-prefixed *filter*, on the other
> hand, will silently hide releases. That is exactly how 1.8.19 was initially
> mistaken for unavailable when this fork was set up.

CI additionally passes `BASE_IMAGE` (digest-pinned), `BASE_DIGEST`,
`IMAGE_REVISION`, `GIT_SHA` and `BUILD_DATE`.

> **Do not bump the base image family.** `crazymax/alpine-s6:3.23-2.2.0.3` is
> s6-overlay **v2** (`/etc/cont-init.d`, `/etc/services.d`). The `3.23-3.2.3.0`
> tag is s6-overlay **v3** (`/etc/s6-overlay/s6-rc.d`) and would require
> rewriting every init script under `rootfs/`. `tests/test_image.py` asserts
> both the label and the on-disk layout to catch an accidental bump.

## Testing

```bash
pip install -r tests/requirements.txt

IMAGE=flarum:test pytest -v tests/ -m 'not runtime'   # fast image lane
IMAGE=flarum:test pytest -v tests/                    # + runtime lane vs MariaDB
```

The runtime lane boots MariaDB plus the image, installs Flarum, and exercises
the extension install/persistence cycle against a real volume.

## Credits & license

The runtime in `rootfs/` and the overall image design are the work of
[CrazyMax](https://github.com/crazy-max) in
[crazy-max/docker-flarum](https://github.com/crazy-max/docker-flarum),
used under the MIT licence and retained here with full git history.

This fork adds the CI pipeline and the test suite. Both are MIT — see
[LICENSE](LICENSE). Flarum itself is MIT.
