# syntax=docker/dockerfile:1

# The previous frontend image, whose hashed assets we merge into this build
# so tabs opened before the deploy keep working (SPA asset continuity — see
# .plan/03-spa-asset-continuity.md). CI passes the floating :latest tag here.
# The default must be an image that HAS /usr/share/nginx/html so the COPY
# below always succeeds even on a cold first build.
ARG PREVIOUS_FRONTEND_IMAGE=nginx:1.27-alpine
FROM ${PREVIOUS_FRONTEND_IMAGE} AS previous

# ---- Build stage: compile the Vite app to static files ----
FROM node:22-alpine AS build
WORKDIR /app

# API base baked into the build. Default to same-origin "/api" so nginx can
# proxy it to the backend (see nginx.conf). Override at build time:
#   docker build --build-arg VITE_API_BASE=https://api.eesoc.org .
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE

# Install dependencies against the lockfile for reproducible builds
COPY package.json package-lock.json ./
RUN npm ci

# Build-variant tag baked into the bundle (see vite.config.js). EMPTY by
# default, and only ever set by the test suite: CI passes no such build-arg,
# so an unset variant must reach vite as the empty string — vite.config.js
# treats empty as "no variant" and leaves Vite's own default asset filenames
# untouched, which is what production builds get. Declared and exported
# AFTER the npm ci layer on purpose: changing the variant only invalidates
# the vite build layer, so docker layer caching still skips the dependency
# install when the variant changes.
ARG EESOC_BUILD_VARIANT=
ENV EESOC_BUILD_VARIANT=$EESOC_BUILD_VARIANT

# Build the static site -> /app/dist
COPY . .
RUN npm run build

# ---- Serve stage: static files behind nginx ----
FROM nginx:1.27-alpine AS serve

# SPA-aware config: history-mode fallback, gzip, long-cache for hashed assets
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy the compiled static output
COPY --from=build /app/dist /usr/share/nginx/html

# Merge the previous build's hashed assets under /assets/ into this image.
# Only assets/ is merged, never the previous index.html: the app shell must
# always be the current one so a reload picks up the new deploy.
#
# `cp -an` (archive + no-clobber): BusyBox cp in this alpine image supports
# both -a (recursive, preserves mtime) and -n, so the current build wins any
# name collision — which in practice cannot happen anyway, since filenames
# are content-hashed and vite.config.js gives variant builds fully disjoint
# names too.
#
# `2>/dev/null || true` is deliberate, not a swallowed error. The default
# previous image (a stock nginx) has no /assets/ directory at all, so a cold
# first build fails this cp by design with nothing to union; and a genuinely
# broken union cannot pass silently, because the asset-continuity e2e suite
# asserts that the previous build's asset URLs stay fetchable from the new
# image — a cp that dropped files fails the build in CI rather than shipping.
#
# Prune files older than 14 days by mtime: Docker COPY and `cp -a` preserve
# build-time mtimes, so each asset ages out ~two weeks after the build that
# produced it. Why 14 days: deploys land several times a day on staging, and
# a week or two covers any realistically still-open tab; the pruning also
# bounds image growth, since every build inherits the union of everything
# its predecessors kept.
# Considered and rejected: serving old chunks from a Garage S3 bucket
# instead. That needs cluster-side setup — a bucket, access keys, and an
# nginx route from this image to it (deploy/garage-init/, deploy/k8s/ are
# cluster-side and owned by other sections) — so it is a blocked human step
# recorded here, not silently dropped.
COPY --from=previous /usr/share/nginx/html /tmp/previous-html
RUN cp -an /tmp/previous-html/assets /usr/share/nginx/html/ 2>/dev/null || true \
    && find /usr/share/nginx/html/assets -type f -mtime +14 -delete

EXPOSE 80

# BusyBox wget ships with the alpine image. Use 127.0.0.1, not localhost:
# localhost resolves to ::1 first and nginx's `listen 80` binds IPv4 only,
# so the check would fail with "connection refused" while the site is fine.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
