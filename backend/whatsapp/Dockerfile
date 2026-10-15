# The WhatsApp link (src/link.js says what it is and why it is careful).
#
# The tests run in the build: CI's only step for this service is building the
# image, so a failing test has to fail the build to stop anything.

FROM node:22-alpine AS test
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# As `node`, the user the service runs as: root ignores file permissions, and
# the test that stands in for a volume's mount point relies on them.
RUN chown -R node:node /app
USER node
RUN npm test

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
# Only so the build cannot skip the test stage: BuildKit builds just the
# stages the final one depends on.
COPY --from=test /app/package.json /tmp/tested

# The session lives on a volume here, owned by `node`. Unprivileged: nothing
# in this process needs root.
RUN mkdir -p /data/session && chown -R node:node /data
USER node
EXPOSE 8080
CMD ["node", "src/main.js"]
