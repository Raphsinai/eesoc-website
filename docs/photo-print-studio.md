# EESoc photo print studio (Sony A7 III + Canon SELPHY CP1500)

The event-day shortcut is `/photo-studio` (also **Committee admin → Photo prints**).
It works from a phone browser; no printer server, laptop, paid editing software,
external QR service or third-party gallery is required.

## Event-day procedure

1. On the Sony A7 III, shoot photos as usual. Transfer the JPEGs to your
   iPhone with Sony Imaging Edge Mobile / the camera's smartphone transfer
   function. For best downloads, prefer **Original** rather than 2M. If
   shooting RAW+JPEG, transfer the JPEG.
2. Sign into the EESoc website on your phone and open **Photo print studio**.
   Choose 1–4 photos, rearrange them with the arrows, enter a title and select
   portrait or landscape 6×4.
3. Create the draft. Every photo uploads individually, so four 8 MB JPEGs
   don't exceed the site's 12 MB *per-request* limit. The studio stores
   metadata-stripped, high-resolution JPEGs for guests; it **does not** reuse
   ordinary 1600px website image optimisation.
4. Preview the branded collage. With permission to publish, confirm that
   participants agree to sharing, then **Publish & enable QR**. The QR
   opens an unlisted gallery at `/photos/<random token>`.
5. Tap **Share / AirPrint** and choose **Print** in the iOS share sheet.
   Choose the Canon SELPHY CP1500 on the same Wi-Fi or use the printer's
   direct Wi-Fi, with 4×6 print media and the matching print orientation.
   If AirPrint's share option is missing, **Open full-size print** and print
   that image via Photos/Files or the Canon SELPHY Photo Layout app.
   Always test-scan a freshly printed QR before offering prints to guests.
6. Scanning a print's QR gives the guest the original photo files individually
   plus a **Download all** ZIP; no EESoc account is required.

The generated 300 DPI 6×4 PNG contains actual EESoc branding, a layout for
1–4 photos, a title, and a QR whose URL is generated from the environment's
`SITE_ORIGIN`. **Staging QR codes intentionally point at staging**. Only
print production QR cards for a real event after production deployment.

## Permission grants and privacy

Grant the specific actions needed under **Photo print studio** in committee
permission management. `photos.view`, `photos.upload` and `photos.create`
are needed for the uploader and draft preview. `photos.publish` lets an
operator activate guest QR links. `photos.unpublish` and `photos.delete`
allow staff to revoke the gallery or delete the photo set. Admins and
superusers get access under their existing presets.

Photo sets are **unlisted, not private**: possession of a valid QR link is
sufficient to view and download. The gallery itself disappears when
unpublished, and removing a set also deletes its stored photos. Do not upload
photos without the subjects' permission; do not assume that copies already
downloaded or saved from the original image URLs can be retracted.

Uploaded files are re-encoded as high-quality JPEG, with EXIF location and
other metadata removed. They may be downscaled only above 6000px on the longest
edge. Guests receive separate images, not merely the print collage.

The API has named permission checks, CSRF on mutation routes, image type and
size validation, and random unguessable tokens. Failed multi-photo draft
uploads are cleaned up by a temporary-upload cancellation endpoint, scoped to
the uploading user. Different photo sets cannot reuse upload records.

## Implementation and testing

- API routes: `backend/api/routes/photos.py`
- Database adapter: `backend/database/routes/photos.py`
- Migration: `backend/database/migrations/090_photo_print_studio.sql`
- Print renderer: `backend/api/services/photo_print.py`
- Vue UI: `frontend/src/components/PhotoStudioPanel.vue`
- Gallery: `frontend/src/views/PhotoGalleryView.vue`
- Automated tests: `tests/photo-print`, `tests/photo-studio`, `tests/permissions`

Run backend tests:

```sh
.venv/bin/python -m pytest -q tests/photo-print tests/photo-studio tests/permissions
```

Build frontend:

```sh
cd frontend && npm run build
```
