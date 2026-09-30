# EESoc website

The website for **EESoc** — the Electrical & Electronic (EEE) and Electronic & Information (EIE) Engineering society at Imperial College London.

Built with **Vue 3 + Vite** from the EESoc design system, converted from the `EESoc Website.dc.html` design mockup.

## Stack

- [Vue 3](https://vuejs.org/) (`<script setup>` SFCs)
- [Vue Router 4](https://router.vuejs.org/) — one route per screen
- [Vite](https://vite.dev/) — dev server & build
- [lucide-vue-next](https://lucide.dev/) — icons (bundled; the original loaded them from a CDN)

## Getting started

```bash
npm install
npm run dev      # start the dev server (http://localhost:5173)
npm run build    # production build → dist/
npm run preview  # preview the production build
```

## Docker

This folder is a self-contained frontend image: a multi-stage build (Node → nginx)
serving the static SPA with history-mode routing. See [`Dockerfile`](Dockerfile) and
[`nginx.conf`](nginx.conf).

```bash
# from this folder
docker build -t eesoc-frontend .
docker run --rm -p 8080:80 eesoc-frontend        # → http://localhost:8080

# or the whole stack (frontend + backend) from the parent folder
docker compose up --build
```

The API base is baked at build time (`--build-arg VITE_API_BASE=…`, default `/api`,
which nginx proxies to the backend). Endpoints the app expects: [`../API.md`](../API.md).

## Structure

```
src/
├── main.js                    # app entry
├── App.vue                    # shell: header + <router-view> + footer + login dialog
├── router/index.js            # routes: / /resources /sponsors /lockers /portal
├── data/site.js               # all site content + the search index (placeholder copy)
├── styles/
│   ├── app.css                # globals + responsive rules
│   └── design-system/         # the EESoc design system, ported verbatim
│       ├── styles.css         # entry (@imports the tokens + components)
│       ├── tokens/            # colours, type, spacing, radii, effects, motion, fonts
│       └── components/        # components.css (class-based component styles)
├── components/
│   ├── ds/                    # design-system components as Vue SFCs
│   │   ├── Button, IconButton, Icon, Card, Badge, Tag, Input, Field, Dialog
│   │   └── ChipFrame, EventCard, Logo, PinStrip, TraceRule, StatBlock, ImageSlot
│   ├── SiteHeader.vue         # sticky nav + live search
│   ├── SiteFooter.vue         # dark footer
│   └── LoginDialog.vue        # Imperial SSO modal
└── views/
    ├── HomeView.vue           # hero, upcoming events, committee, sponsor carousel
    ├── ResourcesView.vue      # category sidebar + past-paper table / resource cards
    ├── SponsorsView.vue       # expandable sponsor list + sponsorship CTA
    ├── LockersView.vue        # interactive locker booking map
    └── PortalView.vue         # sponsor portal: dashboard + panels
```

## Notes on the conversion

- **Routing.** The mockup switched screens with internal state; this build uses real routes, so every page has a URL, back/forward works, and the header search deep-links (e.g. `/resources?cat=papers`).
- **Icons.** Bundled via `lucide-vue-next` instead of the CDN mask used in the mockup — the site now works offline and only ships the icons it uses.
- **Fonts.** Space Mono / IBM Plex Mono / IBM Plex Sans are still loaded from Google Fonts (as the design system shipped). Swap `src/styles/design-system/tokens/fonts.css` for `@font-face` rules to self-host.
- **Content** in `src/data/site.js` is the design system's placeholder copy. Wire it to real data when the society's backend exists.
- **Images.** Logos/photos weren't supplied, so `ImageSlot` renders labelled placeholders where imagery goes (committee photos, sponsor logos). Replace with `<img>` once assets exist.
- **Design tokens & component CSS** are copied verbatim from the design system, so the look stays in lockstep with the source of truth.
