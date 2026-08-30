#!/usr/bin/env sh
#
# GHSA-55f2-h36g-96c3 (CVSS 9.8) — account takeover via password-reset token
# integer type juggling. Vulnerable: flarum/core <= 1.8.18. Fixed: 1.8.19.
#
# Why this script exists: flarum/core v1.8.19 was tagged on the monorepo
# (github.com/flarum/core) but never reached the split repo Packagist resolves
# against (github.com/flarum/flarum-core), whose 1.8.x tags stop at v1.8.18.
# No Flarum 1.8 install can `composer require` the patched core, so we backport
# it into the vendor tree at build time.
#
# The fix upstream is three identical additions: EmailToken, PasswordToken and
# RegistrationToken all use a random string as their primary key, but did not
# declare $keyType. Eloquent therefore casts the key to an integer before the
# lookup, so `WHERE token = 0` matches any token MySQL coerces to 0 — i.e. any
# token that does not begin with a digit.
#
# Contract:
#   - Idempotent. If all three models already declare $keyType (which is what
#     happens the moment FLARUM_VERSION reaches 1.8.19+), it logs and skips —
#     the patch self-retires with no further action.
#   - Fails loudly. A missing file, a missing anchor, or a post-apply assertion
#     miss aborts the build. Silently shipping an unpatched image is the
#     dangerous failure mode; a broken build is not.
#
# Usage: ghsa-55f2-h36g-96c3.sh [<path to vendor/flarum/core>]

set -eu

CORE_DIR="${1:-/opt/flarum/vendor/flarum/core}"
SRC_DIR="${CORE_DIR}/src/User"
MODELS="EmailToken PasswordToken RegistrationToken"

ANCHOR="protected \$primaryKey = 'token';"
PROPERTY="protected \$keyType = 'string';"

log() { echo "[ghsa-55f2-h36g-96c3] $*"; }
die() { echo "[ghsa-55f2-h36g-96c3] FATAL: $*" >&2; exit 1; }

# --- Preflight: every target must exist ------------------------------------
for model in ${MODELS}; do
  [ -f "${SRC_DIR}/${model}.php" ] \
    || die "${SRC_DIR}/${model}.php not found — upstream moved these models. \
Re-verify the advisory against the new layout before building again."
done

# --- Decide: already patched, or apply -------------------------------------
needs_patch=0
for model in ${MODELS}; do
  grep -qF "${PROPERTY}" "${SRC_DIR}/${model}.php" || needs_patch=1
done

if [ "${needs_patch}" -eq 0 ]; then
  log "all three token models already declare \$keyType — patch not needed"
else
  for model in ${MODELS}; do
    file="${SRC_DIR}/${model}.php"

    if grep -qF "${PROPERTY}" "${file}"; then
      log "${model}: already patched, skipping"
      continue
    fi

    grep -qF "${ANCHOR}" "${file}" \
      || die "${model}: anchor \"${ANCHOR}\" not found — cannot place the fix"

    # Insert the property block immediately after the $primaryKey declaration,
    # mirroring upstream's placement. awk rather than sed: busybox sed's `a\`
    # cannot append a multi-line block portably.
    awk -v anchor="${ANCHOR}" -v property="${PROPERTY}" '
      { print }
      index($0, anchor) && !done {
        print ""
        print "    /**"
        print "     * The primary key is a random string, not an auto-incrementing"
        print "     * integer. Without this, Eloquent casts the key to an integer"
        print "     * before lookup, so a request supplying `0` matches any token"
        print "     * MySQL coerces to `0` (i.e. any token not starting with a digit)."
        print "     *"
        print "     * Backported from flarum/core v1.8.19 (GHSA-55f2-h36g-96c3)."
        print "     *"
        print "     * @var string"
        print "     */"
        print "    " property
        done = 1
      }
    ' "${file}" > "${file}.patched"

    mv "${file}.patched" "${file}"
    log "${model}: patched"
  done
fi

# --- Assert: unconditional, runs on both branches --------------------------
# A no-op apply must not be able to produce an unpatched image.
for model in ${MODELS}; do
  grep -qF "${PROPERTY}" "${SRC_DIR}/${model}.php" \
    || die "${model}: assertion failed — \"${PROPERTY}\" absent after patching"
done

log "verified: all three token models declare \$keyType = 'string'"
