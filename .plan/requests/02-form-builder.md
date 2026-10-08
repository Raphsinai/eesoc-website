# Request 02 — ability to make forms

Received: round 2 (2026-10-08), from Raph:

> add the ability to make forms

## Triage

**Feature — outside the approved plan's scope.** Planned as new section
[`../11-form-builder.md`](../11-form-builder.md), **proposed, awaiting approval — not
built**.

## Reading of the note

A generic form builder: committee creates *standalone* forms (surveys, interest
sign-ups, merch orders, feedback not tied to an event) with the same field types as the
event sign-up questions, shares a public link, and views/exports responses.

The approved plan already builds *per-event* sign-up questions (06) and a fixed
post-event feedback form (08). What it does **not** build is arbitrary,
committee-authored forms living outside events — that is the delta this section covers.
"Make forms" could alternatively have been read as "the per-event questions in 06", but
06 is already approved and specified; the only *new* capability worth a note is the
standalone builder. If Raph meant something narrower, say so and this section shrinks.

## Why not a tweak

New domain (forms + fields + responses tables), new routes/views, its own privacy and
retention surface. Reuses 06's machinery by design, but 06 is not built yet — so this
section sequences after 06 either way.