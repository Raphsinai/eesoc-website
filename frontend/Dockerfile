# syntax=docker/dockerfile:1

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

# Build the static site -> /app/dist
COPY . .
RUN npm run build

# ---- Serve stage: static files behind nginx ----
FROM nginx:1.27-alpine AS serve

# SPA-aware config: history-mode fallback, gzip, long-cache for hashed assets
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy the compiled static output
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# BusyBox wget ships with the alpine image. Use 127.0.0.1, not localhost:
# localhost resolves to ::1 first and nginx's `listen 80` binds IPv4 only,
# so the check would fail with "connection refused" while the site is fine.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
