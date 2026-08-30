# syntax=docker/dockerfile:1
#
# Flarum image — PikaPods fork of crazy-max/docker-flarum (MIT).
# The runtime (rootfs/, s6-overlay v2 init, nginx/php templates, the extension
# helper) is kept verbatim from upstream. This file adds the pieces we own:
# a digest-pinnable base, an Alpine refresh, the GHSA-55f2-h36g-96c3 backport,
# and OCI labels. See README.md for design notes.
#
# Build:
#   docker build \
#     --build-arg FLARUM_VERSION=1.8.18 \
#     -t ghcr.io/pikapods/docker-flarum:1.8.18 .
#
# CI additionally passes BASE_IMAGE (digest-pinned), BASE_DIGEST,
# IMAGE_REVISION, GIT_SHA and BUILD_DATE. Local builds work without them.

ARG ALPINE_VERSION=3.23

# NOTE: stay on the -2.2.0.3 suffix. That is s6-overlay **v2**
# (/etc/cont-init.d, /etc/services.d), which every script under rootfs/ is
# written against. crazymax/alpine-s6:3.23-3.2.3.0 is s6-overlay v3
# (/etc/s6-overlay/s6-rc.d) and would require rewriting the whole init tree.
#
# BASE_IMAGE lets CI pin to a digest (…@sha256:…) so the build is reproducible
# and the exact base can be recorded in a label. Local builds fall back to the
# floating tag.
ARG BASE_IMAGE=crazymax/alpine-s6:${ALPINE_VERSION}-2.2.0.3

FROM tianon/gosu:latest AS gosu

FROM ${BASE_IMAGE}

# Re-declare post-FROM so they are visible to LABEL and the RUN steps below.
ARG ALPINE_VERSION
# Plain semver, no leading "v" — the composer constraint prefixes it. This
# keeps the arg, the image tag and the image.version label in one form.
ARG FLARUM_VERSION=1.8.18

# Build identity. IMAGE_REVISION is bumped by build.yml when the same
# FLARUM_VERSION is rebuilt against a fresher base or Alpine package set;
# BASE_DIGEST records the sha256 the FROM line resolved to.
ARG IMAGE_REVISION=r1
ARG BASE_DIGEST=
ARG GIT_SHA=
ARG BUILD_DATE=

LABEL org.opencontainers.image.title="Flarum" \
      org.opencontainers.image.description="Self-maintained Flarum container, with GHSA-55f2-h36g-96c3 backported" \
      org.opencontainers.image.source="https://github.com/pikapods/docker-flarum" \
      org.opencontainers.image.url="https://github.com/pikapods/docker-flarum" \
      org.opencontainers.image.vendor="PikaPods" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${FLARUM_VERSION}-${IMAGE_REVISION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.base.name="crazymax/alpine-s6:${ALPINE_VERSION}-2.2.0.3" \
      org.opencontainers.image.base.digest="${BASE_DIGEST}" \
      cc.pikapods.patches="GHSA-55f2-h36g-96c3"

COPY --from=gosu /gosu /usr/local/bin/

# `apk upgrade` before the install: the base tag is rebuilt on the upstream
# maintainer's cadence, not ours. Refreshing here decouples our Alpine CVE
# posture from that entirely, so any rebuild picks up current 3.x packages
# even when the base digest has not moved.
RUN apk --no-cache upgrade \
  && apk --update --no-cache add \
    bash \
    curl \
    jq \
    libgd \
    mysql-client \
    mariadb-connector-c \
    nginx \
    php84 \
    php84-cli \
    php84-ctype \
    php84-curl \
    php84-dom \
    php84-exif \
    php84-fileinfo \
    php84-fpm \
    php84-gd \
    php84-gmp \
    php84-iconv \
    php84-intl \
    php84-json \
    php84-mbstring \
    php84-opcache \
    php84-openssl \
    php84-pdo \
    php84-pdo_mysql \
    php84-pecl-uuid \
    php84-phar \
    php84-session \
    php84-simplexml \
    php84-sodium \
    php84-tokenizer \
    php84-xml \
    php84-xmlwriter \
    php84-zip \
    php84-zlib \
    shadow \
    tar \
    tzdata \
  && rm -rf /tmp/* /var/www/*

ENV S6_BEHAVIOUR_IF_STAGE2_FAILS="2"\
  TZ="UTC" \
  PUID="1000" \
  PGID="1000"

# Install Flarum, then backport GHSA-55f2-h36g-96c3 into the vendor tree.
#
# flarum/core v1.8.19 carries the fix but was never published to the split repo
# Packagist resolves against, so `composer require` cannot reach it — see the
# script header and README "Patched vulnerabilities".
#
# The script is verify-then-apply and idempotent: once FLARUM_VERSION reaches a
# release that already declares $keyType it logs "patch not needed" and skips,
# and it asserts the property is present on every exit path. An upstream
# refactor that moves these models breaks the build rather than shipping an
# unpatched image.
#
# Folded into this RUN rather than given its own layer so the patched files
# land before `chown -R`, which would otherwise have to duplicate the whole
# vendor tree into a second layer. The bind mount keeps patches/ out of the
# image entirely — nothing to clean up, and no whiteout in an earlier layer.
RUN --mount=type=bind,source=patches,target=/patches \
  mkdir -p /opt/flarum \
  && curl -sSL https://getcomposer.org/installer | php -- --install-dir=/usr/bin --filename=composer \
  && COMPOSER_CACHE_DIR="/tmp" composer create-project flarum/flarum /opt/flarum --no-install \
  && COMPOSER_CACHE_DIR="/tmp" COMPOSER_NO_BLOCKING=1 composer require --working-dir /opt/flarum flarum/core:v${FLARUM_VERSION} \
  && composer clear-cache \
  && sh /patches/ghsa-55f2-h36g-96c3.sh /opt/flarum/vendor/flarum/core \
  && addgroup -g ${PGID} flarum \
  && adduser -D -h /opt/flarum -u ${PUID} -G flarum -s /bin/sh -D flarum \
  && chown -R flarum:flarum /opt/flarum \
  && rm -rf /root/.composer /tmp/*

COPY rootfs /

EXPOSE 8000
WORKDIR /opt/flarum
VOLUME [ "/data" ]

ENTRYPOINT [ "/init" ]
