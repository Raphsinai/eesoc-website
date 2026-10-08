# Request 01 — committee can add tickets for people (auto-confirmed)

Received: round 2 (2026-10-08), from Raph:

> add the ability for committee to add tickets for people that are automatically
> confirmed with ticket allocation etc

## Triage

**Feature — outside the approved plan's scope.** Planned as new section
[`../10-committee-added-tickets.md`](../10-committee-added-tickets.md), **proposed,
awaiting approval — not built**.

## Reading of the note

Committee creates a booking **on behalf of** a person (member or guest — e.g. paid
offline / cash / bank transfer, or a comp) that lands **immediately confirmed** — no
reserve → Union-sale-match round trip — while counting fully against the event's ticket
allocation (capacity, waitlist, live counts, CSV export, check-in). "with ticket
allocation etc" is read as: the manually-added ticket participates in allocation exactly
like a normal one.

## Why not a tweak

The approved sections cover *self*-booked tickets (06: sign-up questions, QR, check-in)
and event lifecycle states (08: duplicate/series/schedule/cancel/feedback). Nothing lets
committee create a booking for someone else or confirm one without a Union sale — that's
new endpoint + model surface (booking source/audit columns, sync/hold-expiry exclusion,
allocation semantics), i.e. a new section, not a patch on an existing one.