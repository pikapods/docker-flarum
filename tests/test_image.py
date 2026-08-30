import json
import os
import re
import subprocess

import pytest

IMAGE = os.environ["IMAGE"]

# Set by CI so the label can be checked against what was actually requested.
# Absent locally — the shape assertions still run.
EXPECTED_VERSION = os.environ.get("FLARUM_VERSION")
EXPECTED_REVISION = os.environ.get("IMAGE_REVISION")


def _inspect():
    out = subprocess.run(
        ["docker", "inspect", IMAGE],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)[0]


@pytest.fixture(scope="session")
def inspect():
    return _inspect()


def _run(*args, check=False):
    return subprocess.run(
        ["docker", "run", "--rm", "--entrypoint=", IMAGE, *args],
        capture_output=True, text=True, check=check,
    )


class TestImageMetadata:
    def test_required_oci_labels(self, inspect):
        labels = inspect["Config"].get("Labels") or {}
        for key in (
            "org.opencontainers.image.source",
            "org.opencontainers.image.version",
            "org.opencontainers.image.licenses",
            "org.opencontainers.image.title",
        ):
            assert labels.get(key), f"missing OCI label: {key}"

    def test_base_image_labels_not_inherited_from_upstream(self, inspect):
        # crazymax/alpine-s6 sets image.url/vendor/title of its own. If our
        # LABEL block stops overriding them the published image advertises the
        # wrong project.
        labels = inspect["Config"].get("Labels") or {}
        assert labels.get("org.opencontainers.image.vendor") == "PikaPods"
        assert "pikapods" in labels.get("org.opencontainers.image.url", "")
        assert labels.get("org.opencontainers.image.title") == "Flarum"

    def test_version_label_shape(self, inspect):
        labels = inspect["Config"].get("Labels") or {}
        version = labels.get("org.opencontainers.image.version", "")
        assert re.fullmatch(r"\d+\.\d+\.\d+-r\d+", version), (
            f"image.version {version!r} is not <flarum-version>-rN — build.yml "
            "parses the trailing rN to compute the next revision"
        )

    @pytest.mark.skipif(not EXPECTED_VERSION, reason="FLARUM_VERSION not set")
    def test_version_label_matches_requested_build(self, inspect):
        labels = inspect["Config"].get("Labels") or {}
        expected = EXPECTED_VERSION
        if EXPECTED_REVISION:
            expected = f"{EXPECTED_VERSION}-{EXPECTED_REVISION}"
            assert labels.get("org.opencontainers.image.version") == expected
        else:
            assert labels.get("org.opencontainers.image.version", "").startswith(
                f"{EXPECTED_VERSION}-"
            )

    def test_base_stays_on_s6_overlay_v2(self, inspect):
        # Every script under rootfs/ targets /etc/cont-init.d (s6-overlay v2).
        # The 3.x line of crazymax/alpine-s6 is s6-overlay v3 and uses
        # /etc/s6-overlay/s6-rc.d instead — a base bump would boot an image
        # that silently runs no init at all.
        labels = inspect["Config"].get("Labels") or {}
        base = labels.get("org.opencontainers.image.base.name", "")
        assert base.startswith("crazymax/alpine-s6:"), f"unexpected base: {base!r}"
        assert base.endswith("-2.2.0.3"), (
            f"base {base!r} is not the s6-overlay v2 line; rootfs/ would not run"
        )

    def test_exposes_8000(self, inspect):
        ports = inspect["Config"].get("ExposedPorts") or {}
        assert "8000/tcp" in ports, f"8000/tcp not exposed; got {list(ports)}"

    def test_entrypoint_is_s6_init(self, inspect):
        assert inspect["Config"].get("Entrypoint") == ["/init"]

    def test_workdir_and_volume(self, inspect):
        assert inspect["Config"].get("WorkingDir") == "/opt/flarum"
        assert "/data" in (inspect["Config"].get("Volumes") or {})

    def test_default_env_present(self, inspect):
        env = dict(e.split("=", 1) for e in inspect["Config"].get("Env") or [])
        assert env.get("PUID") == "1000"
        assert env.get("PGID") == "1000"
        assert env.get("TZ") == "UTC"
        # Without this, a failing cont-init.d script leaves a half-configured
        # container running instead of aborting the boot.
        assert env.get("S6_BEHAVIOUR_IF_STAGE2_FAILS") == "2"

    def test_image_size_under_limit(self, inspect):
        size_mb = inspect["Size"] / (1024 * 1024)
        assert size_mb < 500, f"image size {size_mb:.0f} MB exceeds 500 MB guardrail"


class TestImageFilesystem:
    def test_flarum_user_exists_at_puid(self):
        # s6-overlay v2 needs PID 1 as root, so Config.User is empty by design;
        # nginx/php-fpm drop to this user via s6-setuidgid at boot. The account
        # must exist at the PUID/PGID the ENV advertises.
        r = _run("id", "flarum")
        assert r.returncode == 0, f"flarum user missing: {r.stderr}"
        assert "uid=1000(flarum)" in r.stdout, r.stdout
        assert "gid=1000(flarum)" in r.stdout, r.stdout

    def test_app_dir_owned_by_flarum(self):
        r = _run("stat", "-c", "%U:%G", "/opt/flarum")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "flarum:flarum"

    def test_vendor_tree_owned_by_flarum(self):
        # composer installs as root; the chown in the same layer is what makes
        # the tree usable by the runtime user. If it is ever dropped, the
        # boot-time fixperms has to rewrite the whole vendor tree on every
        # start — slow, and masked by the fact that the app still works.
        r = _run("sh", "-c", "find /opt/flarum/vendor ! -user flarum | head -3")
        assert r.returncode == 0, r.stderr
        assert not r.stdout.strip(), f"root-owned files in vendor: {r.stdout}"

    def test_core_version_matches_build_arg(self):
        r = _run(
            "jq", "-r",
            '.packages[] | select(.name == "flarum/core") | .version',
            "/opt/flarum/composer.lock",
        )
        assert r.returncode == 0, r.stderr
        locked = r.stdout.strip()
        # Upstream is inconsistent about the leading "v": 1.8.16-1.8.18 are
        # tagged v-prefixed, 1.8.19 is not. Never assume it — a v-prefixed
        # grep against Packagist is exactly what hid 1.8.19's availability
        # and made this fork look like it needed a source patch.
        assert re.fullmatch(r"v?\d+\.\d+\.\d+", locked), f"odd core version: {locked!r}"
        if EXPECTED_VERSION:
            assert locked.lstrip("v") == EXPECTED_VERSION.lstrip("v"), (
                f"composer.lock has flarum/core {locked}, build requested "
                f"{EXPECTED_VERSION}"
            )

    def test_tracks_the_1_8_series(self):
        # Flarum 2.0 is a different runtime (and a different rootfs). If a
        # version bump ever crosses the major boundary this fails loudly
        # rather than publishing an untested image under the same tags.
        r = _run(
            "jq", "-r",
            '.packages[] | select(.name == "flarum/core") | .version',
            "/opt/flarum/composer.lock",
        )
        # .lstrip("v") for the same reason as above: the prefix is not reliable.
        assert r.stdout.strip().lstrip("v").startswith("1.8."), (
            f"flarum/core is {r.stdout.strip()}; this image only tracks the 1.8 series"
        )

    @pytest.mark.parametrize("script", [
        "00-fix-logs.sh",
        "01-fix-uidgid.sh",
        "02-fix-perms.sh",
        "03-config.sh",
        "04-svc-main.sh",
        "05-svc-cron.sh",
    ])
    def test_s6_v2_cont_init_scripts(self, script):
        # s6-overlay v2 layout. If the base image is ever bumped to the 3.x
        # line these paths stop being executed and the container boots with
        # no config, no install and no services.
        #
        # Mode is deliberately not asserted: s6-overlay v2 stages
        # /etc/cont-init.d into /var/run/s6/etc and sets the exec bit there,
        # so upstream ships these 0644. test_flarum_installs_on_first_boot in
        # test_runtime.py is what proves they actually run.
        r = _run("test", "-f", f"/etc/cont-init.d/{script}")
        assert r.returncode == 0, f"/etc/cont-init.d/{script} missing"

    def test_s6_v3_layout_absent(self):
        # Negative control for the above: if this ever starts existing, the
        # base moved to s6-overlay v3 and rootfs/ needs rewriting.
        r = _run("test", "-d", "/etc/s6-overlay/s6-rc.d")
        assert r.returncode != 0, (
            "/etc/s6-overlay/s6-rc.d exists — base image appears to be "
            "s6-overlay v3, but rootfs/ is written for v2"
        )

    @pytest.mark.parametrize("helper", [
        "/usr/local/bin/extension",
        "/usr/local/bin/flarum_scheduler",
        "/usr/local/bin/gosu",
    ])
    def test_helpers_executable(self, helper):
        r = _run("test", "-x", helper)
        assert r.returncode == 0, f"{helper} missing or not executable"

    def test_extension_helper_usage(self):
        # The extension helper is the documented UX for PikaPods users; a
        # broken shebang or a syntax error only surfaces when invoked.
        r = _run("sh", "/usr/local/bin/extension")
        assert "extension require" in (r.stdout + r.stderr), (
            f"extension helper did not print usage (rc={r.returncode}, "
            f"stdout={r.stdout!r}, stderr={r.stderr!r})"
        )

    @pytest.mark.parametrize("binary", [
        "php", "nginx", "php-fpm84", "composer", "gosu", "mariadb", "jq", "curl", "bash",
    ])
    def test_runtime_binaries_present(self, binary):
        r = _run("which", binary)
        assert r.returncode == 0, f"{binary} not found on PATH"
        assert r.stdout.strip(), f"which {binary} returned empty"

    @pytest.mark.parametrize("ext", [
        "ctype", "curl", "dom", "exif", "fileinfo", "gd", "gmp", "iconv",
        "intl", "json", "mbstring", "openssl", "pdo_mysql", "session",
        "SimpleXML", "sodium", "tokenizer", "uuid", "xml", "zip",
        "Zend OPcache",
    ])
    def test_php_extensions_loaded(self, ext):
        r = _run("php", "-m")
        assert r.returncode == 0, r.stderr
        modules = {line.strip() for line in r.stdout.splitlines() if line.strip()}
        assert ext in modules, f"PHP module {ext!r} not loaded; got {sorted(modules)}"
