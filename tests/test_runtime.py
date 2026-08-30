import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.runtime

IMAGE = os.environ["IMAGE"]
READY_DEADLINE_S = 300      # first boot runs `php flarum install` + migrations
RESTART_DEADLINE_S = 240

DB_NAME = "flarum"
DB_USER = "flarum"
DB_PASS = "flarumtest"


def _sh(*args, check=True):
    return subprocess.run(list(args), capture_output=True, text=True, check=check)


def _exec(container, *args, check=False):
    return subprocess.run(
        ["docker", "exec", container, *args],
        capture_output=True, text=True, check=check,
    )


def _http(url, timeout=10):
    """Return (status, body). urllib raises on 4xx/5xx; we want the code."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def _wait_http_200(url, deadline_s, container=None):
    end = time.time() + deadline_s
    last = None
    while time.time() < end:
        try:
            status, _ = _http(url, timeout=5)
            if status == 200:
                return
            last = f"status={status}"
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last = repr(e)
        time.sleep(2)
    if container:
        logs = _sh("docker", "logs", container, check=False)
        print(f"--- {container} logs ---\n{logs.stdout}\n{logs.stderr}")
    raise RuntimeError(f"{url} did not return 200 within {deadline_s}s (last={last})")


def _wait_db_ready(container, deadline_s=90):
    end = time.time() + deadline_s
    while time.time() < end:
        r = _exec(container, "healthcheck.sh", "--connect", "--innodb_initialized")
        if r.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"mariadb {container} not ready within {deadline_s}s")


@pytest.fixture(scope="session")
def stack():
    suffix = secrets.token_hex(4)
    net = f"flarum-net-{suffix}"
    db = f"fl-db-{suffix}"
    app = f"fl-app-{suffix}"
    cron = f"fl-cron-{suffix}"
    vol = f"flarum-data-{suffix}"

    # podman rejects `-p 0:8000`; pick a concrete high port so both runtimes
    # work. The port also goes into FLARUM_BASE_URL, which Flarum bakes into
    # generated asset and redirect URLs at install time — it must be fixed
    # before the container boots, so it cannot be discovered afterwards.
    port = secrets.randbelow(20000) + 30000
    base_url = f"http://127.0.0.1:{port}"

    _sh("docker", "network", "create", net)
    _sh("docker", "volume", "create", vol)
    try:
        _sh(
            "docker", "run", "-d", "--name", db, "--network", net,
            "--network-alias", "db",
            "-e", "MARIADB_RANDOM_ROOT_PASSWORD=yes",
            "-e", f"MARIADB_DATABASE={DB_NAME}",
            "-e", f"MARIADB_USER={DB_USER}",
            "-e", f"MARIADB_PASSWORD={DB_PASS}",
            "mariadb:11",
            "mariadbd",
            "--character-set-server=utf8mb4",
            "--collation-server=utf8mb4_unicode_ci",
        )
        _wait_db_ready(db)

        app_env = [
            f"FLARUM_BASE_URL={base_url}",
            "FLARUM_FORUM_TITLE=Smoke Test Forum",
            "DB_HOST=db",
            f"DB_NAME={DB_NAME}",
            f"DB_USER={DB_USER}",
            f"DB_PASSWORD={DB_PASS}",
        ]
        cmd = ["docker", "run", "-d", "--name", app, "--network", net,
               "-v", f"{vol}:/data", "-p", f"{port}:8000"]
        for e in app_env:
            cmd += ["-e", e]
        cmd.append(IMAGE)
        _sh(*cmd)

        _wait_http_200(f"{base_url}/", READY_DEADLINE_S, container=app)

        # Scheduler sidecar against the same volume + database.
        cmd = ["docker", "run", "-d", "--name", cron, "--network", net,
               "-v", f"{vol}:/data"]
        for e in app_env + ["SIDECAR_CRON=1", "CRON_SCHEDULE=* * * * *"]:
            cmd += ["-e", e]
        cmd.append(IMAGE)
        _sh(*cmd)

        yield {"app": app, "db": db, "cron": cron, "net": net,
               "vol": vol, "port": port, "base_url": base_url}
    finally:
        for n in (cron, app, db):
            subprocess.run(["docker", "rm", "-f", n], capture_output=True)
        subprocess.run(["docker", "network", "rm", net], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "-f", vol], capture_output=True)


# ---------------------------------------------------------------------------
# Boot & install
# ---------------------------------------------------------------------------

def test_flarum_installs_on_first_boot(stack):
    status, body = _http(f"{stack['base_url']}/")
    assert status == 200, f"forum returned {status}"
    assert "Smoke Test Forum" in body, "seeded forum title not rendered"


def test_data_layout(stack):
    for path in ("/data/assets", "/data/extensions", "/data/storage",
                 "/data/extensions/list"):
        r = _exec(stack["app"], "test", "-e", path)
        assert r.returncode == 0, f"{path} missing from the data volume"


@pytest.mark.parametrize("link,target", [
    ("/opt/flarum/public/assets", "/data/assets"),
    ("/opt/flarum/extensions", "/data/extensions"),
    ("/opt/flarum/storage", "/data/storage"),
])
def test_data_symlinks(stack, link, target):
    r = _exec(stack["app"], "readlink", link)
    assert r.returncode == 0, f"readlink {link} failed: {r.stderr}"
    assert r.stdout.strip() == target


def test_services_run_as_flarum(stack):
    # s6-overlay v2 keeps PID 1 as root and drops nginx/php-fpm to the
    # PUID/PGID account via s6-setuidgid. Verify the drop actually happened.
    # 04-svc-main.sh wraps both services in s6-setuidgid, so even the masters
    # should be unprivileged — nothing in this list may be root.
    r = _exec(stack["app"], "sh", "-c",
              "ps -o user,comm | grep -E 'php-fpm|nginx' | sort -u")
    assert r.returncode == 0, r.stderr
    lines = [l.split(None, 1) for l in r.stdout.splitlines() if l.strip()]
    assert any("nginx" in c for _, c in lines), f"nginx not running:\n{r.stdout}"
    assert any("php-fpm" in c for _, c in lines), f"php-fpm not running:\n{r.stdout}"
    privileged = [f"{u} {c}" for u, c in lines if u != "flarum"]
    assert not privileged, (
        f"web services not dropped to the flarum user: {privileged}"
    )


def test_logs_clean(stack):
    logs = _sh("docker", "logs", stack["app"], check=False)
    combined = logs.stdout + logs.stderr
    bad = re.findall(r"PHP Fatal|RuntimeException|s6-overlay-suexec: fatal", combined)
    assert not bad, f"bad patterns in container logs: {bad[:5]}"


def test_scheduler_sidecar_running(stack):
    logs = _sh("docker", "logs", stack["cron"], check=False)
    combined = logs.stdout + logs.stderr
    assert "Sidecar cron container detected" in combined, (
        f"cron container did not enter sidecar mode:\n{combined[-2000:]}"
    )
    # Sidecar mode must not start a web stack — two nginx instances on the
    # same volume is not the intent.
    r = _exec(stack["cron"], "sh", "-c", "ps -o comm | grep -c nginx || true")
    assert r.stdout.strip() in ("0", ""), "sidecar container is also running nginx"


# ---------------------------------------------------------------------------
# Extension persistence — the PikaPods UX this image exists to serve.
#
# Extensions are installed by Composer into /opt/flarum (inside the image, not
# the volume). The `extension` helper mirrors the resulting composer.json into
# /data/extensions/list, and 03-config.sh replays that list on every boot. If
# the mirror or the replay breaks, users silently lose every extension the
# next time their pod is recreated.
# ---------------------------------------------------------------------------

_TEST_EXTENSION = "flarum/extension-manager"


def test_extension_install_and_persistence(stack):
    app = stack["app"]

    r = _exec(app, "sh", "/usr/local/bin/extension", "require", _TEST_EXTENSION)
    assert r.returncode == 0, (
        f"extension require failed (rc={r.returncode})\n"
        f"stdout={r.stdout[-3000:]}\nstderr={r.stderr[-3000:]}"
    )

    listing = _exec(app, "sh", "/usr/local/bin/extension", "list")
    assert listing.returncode == 0, listing.stderr
    assert _TEST_EXTENSION in listing.stdout, (
        f"{_TEST_EXTENSION} not mirrored into /data/extensions/list; "
        f"got {listing.stdout!r}"
    )

    installed = _exec(app, "test", "-d",
                      f"/opt/flarum/vendor/{_TEST_EXTENSION}")
    assert installed.returncode == 0, "extension not present in the vendor tree"

    # Recreate the container against the same volume: a fresh image layer,
    # so the extension only comes back if the list replay works.
    _sh("docker", "rm", "-f", app)
    cmd = ["docker", "run", "-d", "--name", app, "--network", stack["net"],
           "-v", f"{stack['vol']}:/data", "-p", f"{stack['port']}:8000"]
    for e in (f"FLARUM_BASE_URL={stack['base_url']}",
              "FLARUM_FORUM_TITLE=Smoke Test Forum",
              "DB_HOST=db", f"DB_NAME={DB_NAME}",
              f"DB_USER={DB_USER}", f"DB_PASSWORD={DB_PASS}"):
        cmd += ["-e", e]
    cmd.append(IMAGE)
    _sh(*cmd)
    _wait_http_200(f"{stack['base_url']}/", RESTART_DEADLINE_S, container=app)

    reinstalled = _exec(app, "test", "-d",
                        f"/opt/flarum/vendor/{_TEST_EXTENSION}")
    assert reinstalled.returncode == 0, (
        f"{_TEST_EXTENSION} was not reinstalled from /data/extensions/list on "
        "container recreation — extension persistence is broken"
    )

    still_listed = _exec(app, "sh", "/usr/local/bin/extension", "list")
    assert _TEST_EXTENSION in still_listed.stdout, (
        f"/data/extensions/list lost {_TEST_EXTENSION} across recreation; "
        f"got {still_listed.stdout!r}"
    )


# ---------------------------------------------------------------------------
# php-fpm worker environment.
#
# clear_env=yes clears a worker's environment, so the pool's env[PATH] is the
# only thing standing between Flarum's Extension Manager and `sh: not found`
# on every Composer script it dispatches. The tests above drive the helper
# from a normal CLI environment and cannot see this.
#
# These come after the extension tests deliberately: the persistence test
# recreates stack["app"] mid-session, so ordering in this module matters.
# ---------------------------------------------------------------------------

def test_php_fpm_pool_exports_path(stack):
    # Assert the *rendered* pool, not the template -- catches a template edit
    # that the sed in 03-config.sh drops on the floor.
    r = _exec(stack["app"], "grep", "-E", r"^env\[PATH\]",
              "/etc/php84/php-fpm.d/www.conf")
    assert r.returncode == 0, (
        "rendered php-fpm pool has no env[PATH]; workers will run with an "
        "empty environment"
    )
    assert "/usr/bin" in r.stdout, r.stdout


def test_composer_hook_under_stripped_env(stack):
    # Same shape as the image-lane regression test, but against a real boot:
    # the baseline file and /data/extensions already exist, and /data is the
    # real volume rather than an anonymous one.
    r = _exec(stack["app"], "jq", "-r",
              '.scripts["post-update-cmd"][]', "/opt/flarum/composer.json")
    assert r.returncode == 0, r.stderr
    hooks = [l for l in r.stdout.splitlines() if "/usr/local/bin/extension" in l]
    assert len(hooks) == 1, f"expected exactly one extension hook, got {hooks}"

    r = _exec(stack["app"], "gosu", "flarum:flarum",
              "/usr/bin/env", "-i", "PATH=/opt/flarum/vendor/bin:",
              "/bin/sh", "-c", hooks[0])
    assert r.returncode == 0, (
        f"post-update hook failed under a stripped environment "
        f"(rc={r.returncode})\nstdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "not found" not in (r.stdout + r.stderr), r.stdout + r.stderr


def test_php_fpm_workers_see_a_usable_path(stack):
    # The only test that proves the fix rather than its configuration: it goes
    # through nginx -> FastCGI -> a real worker.
    #
    # Do NOT substitute a read of /proc/<pid>/environ here. Workers are
    # fork()ed, not exec()ed, so clearenv()/setenv() never rewrite the kernel's
    # env region -- /proc would show the master's pre-clear environment and the
    # test would pass with or without the fix.
    probe = "/opt/flarum/public/_pathprobe.php"
    try:
        w = _exec(stack["app"], "sh", "-c",
                  f"""printf '%s' '<?php echo getenv("PATH") ?: "UNSET";' > {probe}""")
        assert w.returncode == 0, w.stderr
        status, body = _http(f"{stack['base_url']}/_pathprobe.php")
        assert status == 200, f"probe returned {status}: {body[:200]}"
        assert "/usr/bin" in body, (
            f"php-fpm worker PATH is {body!r} -- Composer scripts dispatched "
            "by the Extension Manager will fail with 'not found'"
        )
    finally:
        _exec(stack["app"], "rm", "-f", probe)


def test_healthcheck_status_if_defined(stack):
    r = _sh("docker", "inspect", "--format", "{{json .State.Health}}", stack["app"])
    health = json.loads(r.stdout) if r.stdout.strip() else None
    if not health:
        pytest.skip("image defines no HEALTHCHECK (inherited from upstream)")
    assert health.get("Status") != "unhealthy", health.get("Log", [])[-1:]
