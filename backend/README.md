# EESoc website — backend

Placeholder for the API service. Not implemented yet.

The frontend ([`../frontend`](../frontend)) currently renders placeholder data from
`frontend/src/data/site.js`. To serve live data, implement the endpoints documented in
[`../API.md`](../API.md).

## Contract

- Serve the routes in [`../API.md`](../API.md) under `/api` (JSON).
- Listen on port **8080** (internal). The frontend's nginx proxies `/api` → this
  service, so they share an origin and avoid CORS — see
  [`../frontend/nginx.conf`](../frontend/nginx.conf) and
  [`../docker-compose.yml`](../docker-compose.yml).
- To wire it up: add a `Dockerfile` here, then uncomment the `backend` service in
  `../docker-compose.yml` and the `/api` `location` block in `../frontend/nginx.conf`.

Stack is your choice (Node/Express, FastAPI, Go, …).
