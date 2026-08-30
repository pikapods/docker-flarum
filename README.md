# docker-flarum

[Flarum](https://flarum.org/) container image — a PikaPods fork of
[`crazy-max/docker-flarum`](https://github.com/crazy-max/docker-flarum) (MIT),
carrying a backport of the **GHSA-55f2-h36g-96c3** account-takeover fix that
cannot currently be installed through Composer.

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
| `1.8.18`    | mutable    | Pin to a Flarum version; auto-receive Alpine/base patches      |
| `1.8.18-r1` | immutable  | Byte-for-byte reproducibility; never reused                    |

Source: https://github.com/pikapods/docker-flarum

## Why this fork

Upstream `crazymax/flarum` is a well-built image, and its runtime is kept here
**verbatim** — the s6-overlay v2 init scripts, the `extension` helper, the
scheduler, and the nginx/PHP templates are all upstream's, unmodified. Two
things are ours:

1. **The GHSA-55f2-h36g-96c3 backport** (below). Nobody can ship this through
   Composer right now, so the image has to carry the patch itself.
2. **Release cadence.** Upstream publishes on a bursty schedule (1.8.10 →
   1.8.17 was a 13-month gap), and a versioned tag only appears when the
   maintainer cuts a release. PikaPods needs Alpine and PHP CVEs picked up on
   a predictable clock, so this fork rebuilds against a freshly `apk upgrade`d
   Alpine and bumps the `-rN` revision, which the mutable `1.8.18` tag follows.

This image tracks the **Flarum 1.8 series only**. Flarum 2.0 is still at
release-candidate stage with no GA date.

## Patched vulnerabilities

### GHSA-55f2-h36g-96c3 — account takeover via password-reset token type juggling

| | |
|---|---|
| Severity | **CVSS 9.8 (critical)** |
| Affected | `flarum/core <= 1.8.18` |
| Fixed upstream in | `flarum/core` 1.8.19 |
| Status in this image | **Patched** — backported at build time |

`EmailToken`, `PasswordToken` and `RegistrationToken` use a random string as
their Eloquent primary key, but did not declare `$keyType`. Eloquent therefore
cast the lookup key to an integer, so a request for token `0` produced
`WHERE token = 0` — and MySQL coerces every token that does not begin with a
digit to `0`. Requesting `/reset/0` matched an arbitrary live password-reset
token and handed over the reset form for somebody else's account.

The upstream fix is one property on each of the three models:

```php
protected $keyType = 'string';
```

**Why it has to be backported.** Packagist resolves `flarum/core` against the
split repository
[`flarum/flarum-core`](https://github.com/flarum/flarum-core), whose 1.8.x tags
stop at **v1.8.18**. The `v1.8.19` tag exists only on the monorepo
[`flarum/core`](https://github.com/flarum/core). The `flarum/flarum` skeleton
*is* published at 1.8.19 but requires `flarum/core: ^1.8.19`, so it is
unsatisfiable too. As a result **no Flarum 1.8 install can `composer require`
the patched core**, which is why no downstream image has shipped it either.

[`patches/ghsa-55f2-h36g-96c3.sh`](patches/ghsa-55f2-h36g-96c3.sh) applies the
three-property fix to the vendor tree during the build. It is:

- **Idempotent and self-retiring.** If all three models already declare
  `$keyType` — which is what happens the moment `FLARUM_VERSION` reaches a
  release that includes the fix — it logs `patch not needed` and skips. No
  manual step is needed to retire it.
- **Fail-loud.** A missing file, a missing anchor, or a failed post-apply
  assertion aborts the build. Silently shipping an unpatched image is the
  dangerous failure mode; a broken build is not.

Three independent layers of verification back this up, so a version bump or an
upstream refactor cannot quietly regress the image:

| Check | Where |
|---|---|
| Build-time assertion on all three models | `patches/ghsa-55f2-h36g-96c3.sh` |
| `$keyType` present in source, and effective via `Eloquent::getKeyType()` | `tests/test_image.py::TestSecurityPatch` |
| `/reset/0` returns 404 against a live token MySQL coerces to `0`, with a positive control | `tests/test_runtime.py` |
| Patch survives boot **and** a runtime `composer require` | `tests/test_runtime.py` |

Verify it yourself on any published image:

```bash
docker run --rm --entrypoint= ghcr.io/pikapods/docker-flarum:latest \
  grep -l "keyType = 'string'" \
  /opt/flarum/vendor/flarum/core/src/User/{Email,Password,Registration}Token.php

docker inspect ghcr.io/pikapods/docker-flarum:latest \
  --format '{{index .Config.Labels "cc.pikapods.patches"}}'
# → GHSA-55f2-h36g-96c3
```

### Known scanner false positive

`composer.lock` and `Application::VERSION` still read **1.8.18**, because
that is the version the image is built from — the fix is applied on top. SCA
scanners that match on package version (Trivy, Grype, Snyk, Docker Scout) will
therefore report this image as vulnerable to GHSA-55f2-h36g-96c3 even though
it is not.

The version constant is deliberately **not** faked. Rewriting it would make
the image indistinguishable from a genuine 1.8.19 and destroy the ability to
tell later whether the real fix has landed. The `cc.pikapods.patches` label
is the authoritative signal; treat a scanner hit on this advisory as
suppressed-with-justification.

Once 1.8.19 reaches Packagist, bumping `FLARUM_VERSION` resolves everything at
once: Composer installs the real fix, the patch script detects `$keyType` is
already present and skips, the scanner finding goes away, and the image
publishes as `1.8.19-r1`. No other change is required.

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
Recreating the container reinstalls everything in the list from scratch, which
is also what re-applies this image's security patch on top of a clean tree.

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
docker build --build-arg FLARUM_VERSION=1.8.18 -t flarum:test .
```

`FLARUM_VERSION` is plain semver with no leading `v`; the Dockerfile prefixes
it for the Composer constraint, and it flows into the
`org.opencontainers.image.version` label as `<version>-<revision>`.

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

The runtime lane boots MariaDB plus the image, installs Flarum, exercises the
extension install/persistence cycle and checks the GHSA-55f2-h36g-96c3
regression behaviourally.

To confirm the patch guard is real rather than vacuous, comment out the
`ghsa-55f2-h36g-96c3.sh` invocation in the `Dockerfile`, rebuild, and check
that `TestSecurityPatch` fails. Restore afterwards.

## Credits & license

The runtime in `rootfs/` and the overall image design are the work of
[CrazyMax](https://github.com/crazy-max) in
[crazy-max/docker-flarum](https://github.com/crazy-max/docker-flarum),
used under the MIT licence and retained here with full git history.

This fork adds the security backport, the CI pipeline, and the test suite.
Both are MIT — see [LICENSE](LICENSE). Flarum itself is MIT.
