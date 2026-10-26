# EESoc event photo print studio (Sony A7 III + Canon SELPHY CP1500)

The event-day shortcut is `/photo-studio` (also **Committee admin → Photo prints**).
Works on iPhone: no laptop, printer server, paid editing software, external
QR provider or third-party gallery required.

## Event-day procedure

1. Shoot on Sony A7 III, then transfer JPEGs to iPhone using Imaging Edge
   Mobile / the Sony smartphone transfer function. Select **Original**
   quality where possible, rather than the smaller 2M version.
2. Open the photo studio in your logged-in committee account. The **event most
   recently sent to print on any committee device is automatically selected**.
   If nothing has been printed yet, the event closest to today is suggested.
   You can switch to another event before each set.
3. The print heading comes from the official EESoc event title. To use a
   shorter label on printed cards (e.g., **Halloween** instead of **EESoc
   Halloween Social 2026**), edit the selected event's **Photo short name**,
   either in the studio or from the event's edit panel under Committee admin.
   Photo short names are optional and independently permission-protected.
   The digital gallery still shows the full event title. Historical prints
   keep their own event-name snapshots if the event is later renamed.
4. Choose 1–4 photos, rearrange them with the arrows and select portrait or
   landscape 6×4. Create a draft. Every photo uploads individually, so four
   Sony JPEGs do not exceed the nginx 12 MB *per-request* limit. Digital
   photos retain high resolution and are stripped of EXIF/GPS metadata.
5. Preview the **premium navy/blue-and-gold design**, using the website's
   official EESoc pins logo and its exact brand blue and gold. The print uses
   1200 × 1800 / 1800 × 1200 pixels at 300 DPI. The code keeps its light,
   high-contrast quiet zone for reliable scanning.
6. When participants agree their photos can be shared, **Publish & enable
   QR**. The random, unlisted gallery URL becomes active. Guests can
   download all original-quality JPEGs, individually or as a ZIP.
7. On iPhone tap **Share / AirPrint** and choose **Print**. Select Canon
   SELPHY CP1500 on the same Wi-Fi network (or its direct Wi-Fi), with
   4×6-inch photo media. **Open full-size print** is a fallback if AirPrint
   is not offered; print it via Photos/Files or Canon SELPHY Photo Layout.
   Test-scan the printed QR card before giving it to a guest.

The website records the event when the operator completes the Share/Print
action. This records **print intent**, not a hardware receipt from the Canon.
That event becomes the default for the next visitor to the photo studio,
including on a different committee device.

## Appearance

- Brand colours match the EESoc website: navy `#071D47`,
  blue `#1049B1` and gold `#E7B570`.
- Official pins wordmark: `frontend/public/assets/logo-wordmark-pins.svg`.
  A lighter-on-navy transparent raster is pre-rendered from this source
  and committed at `backend/api/assets/eesoc-pins-light.png`.
- Gold silkscreen border, restrained circuitry accents, and thin gold
  photo frames. No plain white page background; only the QR panel is pale.
- Main print heading: the event's short name when configured, otherwise
  the full event title. No generic “EESoc memories” or “EESoc photo print.”
- Original full event title stays on the guest gallery.
- QR is generated from the environment-specific `SITE_ORIGIN`:
  **staging prints link to staging**, not production.

## Committee permissions and privacy

Permission keys are independently selectable in Committee admin:

- `photos.view` — see sets and event picker
- `photos.upload` — upload photographs
- `photos.create` — create photo-set drafts, tied to actual events
- `photos.publish` / `photos.unpublish` — activate and revoke QR pages
- `photos.print` — record a shared print for the last-event default
- `photos.delete` — remove photos and QR set
- `events.photo_short_name.edit` — change the event-specific short label,
  subject to the normal event scope permissions

Event photo selectors show public events and, where the caller has an event
read grant and scope, unpublished drafts. Creating a photo set always verifies
event visibility and snapshots the authoritative event name in the database.
Setting the short name is a **dedicated PATCH** operation, not a side effect
of the ordinary event editing form.

Photo galleries are **unlisted, not private**. Any holder of a valid QR can
read/download. An unpublished set returns 404; deleting it removes associated
media. Downloaded copies cannot be retracted.

## Code and tests

- API: `backend/api/routes/photos.py` and
  `backend/api/routes/admin.py` (event photo short name)
- Database: `backend/database/routes/photos.py`,
  `backend/database/routes/events.py`, migrations 090/091
- Print renderer: `backend/api/services/photo_print.py`
- Official logo rasteriser: `scripts/render-photo-pins.py`
  (optional developer dependency `resvg-py`, not required in production)
- Studio: `frontend/src/components/PhotoStudioPanel.vue`
- Guest page: `frontend/src/views/PhotoGalleryView.vue`

```sh
.venv/bin/python -m pytest -q tests/photo-print tests/photo-studio tests/permissions
cd frontend && npm run build
```

Only deploy to `eesoc.com` after explicit production promotion approval.
