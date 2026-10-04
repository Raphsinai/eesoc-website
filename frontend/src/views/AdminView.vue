<script setup>
// Committee admin.
//
// One page, several tabs: an overview that says what needs doing, then editors
// for events, resources, Union product links, files, people and sponsors.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import api from '@/api'
import Card from '@/components/ds/Card.vue'
import Button from '@/components/ds/Button.vue'
import Badge from '@/components/ds/Badge.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'
import StatBlock from '@/components/ds/StatBlock.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import session from '@/stores/session'

const route = useRoute()
const router = useRouter()

const TABS = [
  ['overview', 'Overview'],
  ['events', 'Events'],
  ['overdue', 'Unpaid bookings'],
  ['resources', 'Resources'],
  ['files', 'Files'],
  ['committee', 'Committee'],
  ['links', 'Union products'],
  ['people', 'People'],
  ['sponsors', 'Sponsors'],
]

const tab = ref(route.query.tab || 'overview')
watch(tab, (t) => router.replace({ query: { ...route.query, tab: t } }))

const dash = ref(null)
const events = ref([])
const products = ref([])
const links = ref([])
const pages = ref([])
const pageDetail = ref(null)
const uploads = ref(null)
const overdue = ref(null)
const orderNumbers = reactive({})
const admins = ref([])
const sponsors = ref([])
const sponsorTotals = ref(null)
const committee = ref([])
const busy = ref(false)
const error = ref('')
const notice = ref('')

function flash(message) {
  notice.value = message
  setTimeout(() => (notice.value = ''), 6000)
}

async function load() {
  error.value = ''
  try {
    const [d, ev, pr, ln, pg, ad, sp, cm] = await Promise.all([
      api.get('/admin/dashboard'),
      api.get('/admin/events'),
      api.get('/admin/union/products'),
      api.get('/admin/product-links'),
      api.get('/resources'),
      api.get('/admin/admins'),
      api.get('/admin/sponsors'),
      api.get('/admin/committee'),
    ])
    dash.value = d
    events.value = ev
    products.value = pr
    links.value = ln
    pages.value = pg.pages
    admins.value = ad
    sponsors.value = sp.sponsors ?? sp
    sponsorTotals.value = sp.totals ?? null
    committee.value = cm.committee
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)

const counts = computed(() => dash.value?.counts ?? {})

function mb(bytes) {
  return `${((bytes || 0) / 1024 / 1024).toFixed(1)} MB`
}

// ---------------------------------------------------------------- sync -----
async function sync() {
  busy.value = true
  flash('Syncing from eActivities…')
  try {
    const res = await api.post('/admin/sync')
    flash(
      res.ok
        ? `Synced. ${res.matched?.confirmed ?? 0} booking(s) confirmed, ${res.expired_reservations ?? 0} expired.`
        : `Finished with errors: ${(res.steps || []).filter((s) => !s.ok).map((s) => s.kind).join(', ')}`,
    )
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function reconcile() {
  busy.value = true
  try {
    const res = await api.post('/admin/reconcile')
    flash(`${res.confirmed ?? 0} confirmed, ${res.expired ?? 0} expired.`)
    await load()
  } finally {
    busy.value = false
  }
}

// -------------------------------------------------------------- events -----
const blank = () => ({
  slug: '', title: '', category: 'Social', description: '', image_url: '', venue: '',
  starts_at: '', ends_at: '', capacity: null, hold_hours: 48, published: false,
  sponsorship_claimable: false,
  ticket_types: [{ name: 'Standard', price_pence: 0, capacity: null, max_per_user: 1, members_only: false, requires_purchase: true }],
})
const draft = reactive(blank())
const editingId = ref(null)

function editEvent(e) {
  editingId.value = e.id
  Object.assign(draft, {
    ...blank(), ...e,
    starts_at: (e.starts_at || '').slice(0, 16),
    ends_at: (e.ends_at || '').slice(0, 16),
    ticket_types: e.ticket_types?.length ? e.ticket_types : blank().ticket_types,
  })
  // Prefill the product and per-ticket line selects from existing links.
  const ev = links.value.find((l) => l.event_id === e.id && l.ticket_type_id === null)
  eventProduct.value = ev?.union_product_id ?? null
  for (const t of draft.ticket_types) {
    ticketLines[t.id] = links.value.find((l) => l.ticket_type_id === t.id)
      ?.union_product_line_id ?? ''
  }
  tab.value = 'events'
}

function resetDraft() {
  Object.assign(draft, blank())
  editingId.value = null
}

async function saveEvent() {
  busy.value = true
  error.value = ''
  try {
    const payload = {
      ...draft,
      capacity: draft.capacity || null,
      starts_at: new Date(draft.starts_at).toISOString(),
      ends_at: draft.ends_at ? new Date(draft.ends_at).toISOString() : null,
      ticket_types: draft.ticket_types.filter((t) => t.name.trim())
        .map((t) => ({ ...t, capacity: t.capacity || null })),
    }
    if (editingId.value) await api.put(`/admin/events/${editingId.value}`, payload)
    else await api.post('/admin/events', payload)
    flash(editingId.value ? 'Event updated.' : 'Event created.')
    resetDraft()
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function togglePublished(e) {
  await api.put(`/admin/events/${e.id}`, {
    ...e,
    published: !e.published,
    ticket_types: e.ticket_types || [],
  })
  await load()
}

async function removeEvent(e) {
  if (!confirm(`Delete "${e.title}"? Bookings go with it.`)) return
  await api.del(`/admin/events/${e.id}`)
  await load()
}

// ----------------------------------------------------------- resources -----
async function openPage(slug) {
  pageDetail.value = await api.get(`/resources/${slug}`)
}

const newEntry = reactive({ title: '', kind: 'item', body: '', url: '', parent_id: null })

async function addEntry() {
  if (!pageDetail.value || !newEntry.title.trim()) return
  await api.post('/admin/resource-entries', {
    page_id: pageDetail.value.id,
    parent_id: newEntry.parent_id || null,
    kind: newEntry.kind,
    title: newEntry.title,
    body: newEntry.body,
    url: newEntry.url || null,
  })
  Object.assign(newEntry, { title: '', body: '', url: '', parent_id: null })
  await openPage(pageDetail.value.slug)
  flash('Entry added.')
}

async function removeEntry(entry) {
  if (!confirm(`Remove "${entry.title}"? Anything nested under it goes too.`)) return
  await api.del(`/admin/resource-entries/${entry.id}`)
  await openPage(pageDetail.value.slug)
}

async function moveEntry(entry, direction) {
  await api.post(`/admin/resource-entries/${entry.id}/move`, { direction })
  await openPage(pageDetail.value.slug)
}

async function reimport() {
  if (!confirm('Re-import from the eesoc.com snapshot? This overwrites edits to those pages.')) return
  busy.value = true
  try {
    await api.post('/admin/resources/import?force=true')
    flash('Re-imported.')
    await load()
  } finally {
    busy.value = false
  }
}

// --------------------------------------------------------------- files -----
async function copyUrl(url) {
  try {
    await navigator.clipboard.writeText(new URL(url, window.location.origin).href)
    flash('URL copied.')
  } catch {
    flash(url)
  }
}

async function loadUploads() {
  uploads.value = await api.get('/admin/uploads')
}

/** Shared uploader. FormData must not carry a JSON content-type, so this goes
 *  round the api helper and sets only the CSRF header. */
async function uploadFile(file, purpose = 'general') {
  const form = new FormData()
  form.append('file', file)
  form.append('purpose', purpose)
  const res = await fetch('/api/admin/uploads', {
    method: 'POST',
    body: form,
    credentials: 'same-origin',
    headers: { 'X-CSRF-Token': document.cookie.match(/(?:^|;\s*)csrf=([^;]*)/)?.[1] || '' },
  })
  if (!res.ok) {
    let detail = 'Upload failed'
    try {
      detail = (await res.json()).detail || detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.json()
}

const eventImageInput = ref(null)

async function uploadEventImage(event) {
  const file = event.target.files?.[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    const row = await uploadFile(file, 'event')
    // Point the event straight at it, so there is no copy-paste step.
    draft.image_url = row.url
    flash('Image uploaded and attached.')
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
    event.target.value = ''
  }
}

async function upload(event) {
  const file = event.target.files?.[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    await uploadFile(file, 'general')
    flash('Uploaded.')
    await loadUploads()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
    event.target.value = ''
  }
}

async function removeUpload(u) {
  if (!confirm(`Delete ${u.original_name}?`)) return
  await api.del(`/admin/uploads/${u.id}`)
  await loadUploads()
}

async function loadOverdue() {
  overdue.value = await api.get('/admin/reservations/overdue')
}

async function remind(row) {
  busy.value = true
  try {
    const res = await api.post(`/admin/reservations/${row.id}/remind`)
    flash(res.message)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function confirmBooking(row) {
  busy.value = true
  try {
    await api.post(`/admin/reservations/${row.id}/confirm`, {
      order_number: (orderNumbers[row.id] || '').trim() || null,
    })
    flash('Confirmed.')
    await Promise.all([loadOverdue(), load()])
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function cancelBooking(row) {
  if (!confirm(`Cancel ${row.email}'s ticket for ${row.eventTitle}?`)) return
  busy.value = true
  try {
    const res = await api.post(`/admin/reservations/${row.id}/cancel`)
    flash(
      res.promoted
        ? `Cancelled. ${res.promoted} waitlisted person got the place.`
        : 'Cancelled.',
    )
    await Promise.all([loadOverdue(), load()])
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

watch(tab, (t) => {
  if (t === 'files' && !uploads.value) loadUploads()
  if (t === 'overdue') loadOverdue()
})

// -------------------------------------------------------------- people -----
const newAdminEmail = ref('')

// ----------------------------------------------------------- committee -----
async function syncCommittee() {
  busy.value = true
  flash('Pulling the committee roster from eActivities…')
  try {
    const res = await api.post('/admin/sync/committee')
    flash(
      res.ok
        ? `Synced ${res.members} committee member(s). Intros, photos and ordering kept.`
        : `Sync failed: ${res.error || res.message || 'unknown'}`,
    )
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function saveMember(m) {
  busy.value = true
  error.value = ''
  try {
    await api.put(`/committee/${m.login}/profile`, {
      cid: m.cid,
      intro: m.intro || '',
      photo_url: m.photoUrl || null,
      display_role: (m.displayRole || '').trim() || null,
      row_number: Math.max(1, Number(m.row) || 1),
      sort_order: Number(m.sortOrder) || 100,
    })
    flash(`${m.name} saved.`)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function uploadMemberPhoto(m, event) {
  const file = event.target.files?.[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    const row = await uploadFile(file, 'committee')
    m.photoUrl = row.url
    await saveMember(m)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
    event.target.value = ''
  }
}

async function persist(person) {
  await api.put(`/committee/${person.login}/profile`, {
    cid: person.cid,
    intro: person.intro || '',
    photo_url: person.photoUrl || null,
    display_role: (person.displayRole || '').trim() || null,
    row_number: Math.max(1, Number(person.row) || 1),
    sort_order: Number(person.sortOrder) || 100,
  })
}

/** Move within a row. Ordering is per-row, so only that row is renumbered. */
async function moveMember(m, delta) {
  const row = committee.value
    .filter((x) => (x.row || 1) === (m.row || 1))
    .sort((a, b) => a.sortOrder - b.sortOrder)
  const i = row.findIndex((x) => x.login === m.login)
  const j = i + delta
  if (i < 0 || j < 0 || j >= row.length) return
  ;[row[i], row[j]] = [row[j], row[i]]
  row.forEach((person, index) => (person.sortOrder = (index + 1) * 10))
  busy.value = true
  try {
    for (const person of row) await persist(person)
    await load()
  } finally {
    busy.value = false
  }
}

/** Move to another row, landing at the end of it. */
async function setRow(m, row) {
  const target = Math.max(1, Number(row) || 1)
  const existing = committee.value.filter((x) => (x.row || 1) === target)
  m.row = target
  m.sortOrder = (existing.length + 1) * 10
  busy.value = true
  try {
    await persist(m)
    await load()
  } finally {
    busy.value = false
  }
}

/** Committee grouped the way the public page will render it. */
const committeeRows = computed(() => {
  const groups = new Map()
  for (const person of committee.value) {
    const row = person.row || 1
    if (!groups.has(row)) groups.set(row, [])
    groups.get(row).push(person)
  }
  return [...groups.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([row, members]) => ({
      row,
      members: members.sort((a, b) => a.sortOrder - b.sortOrder),
    }))
})

const nextRow = computed(() => (committeeRows.value.at(-1)?.row ?? 0) + 1)

// ------------------------------------------------------------ sponsors -----
const blankSponsor = () => ({
  slug: '', name: '', sector: '', strand: '', one_line: '', blurb: '',
  with_us: [], site: '', logo_url: '', roles: 0, active: true, sort_order: 100,
  amount_pence: 0, internal_note: '', agreement_year: '',
  cv_contact_email: '', cv_contact_name: '', cv_contact_note: '',
})
const sponsorDraft = reactive(blankSponsor())
const editingSponsor = ref(null)
const sponsorUserEmail = reactive({})

function editSponsor(sp) {
  editingSponsor.value = sp.id
  Object.assign(sponsorDraft, { ...blankSponsor(), ...sp, with_us: sp.with_us || [] })
}

function resetSponsor() {
  Object.assign(sponsorDraft, blankSponsor())
  editingSponsor.value = null
}

async function saveSponsor() {
  busy.value = true
  error.value = ''
  try {
    const payload = {
      ...sponsorDraft,
      amount_pence: Math.round(Number(sponsorDraft.amount_pence) || 0),
      with_us: Array.isArray(sponsorDraft.with_us)
        ? sponsorDraft.with_us
        : String(sponsorDraft.with_us).split(',').map((x) => x.trim()).filter(Boolean),
    }
    if (editingSponsor.value) await api.put(`/admin/sponsors/${editingSponsor.value}`, payload)
    else await api.post('/admin/sponsors', payload)
    flash(editingSponsor.value ? 'Sponsor updated.' : 'Sponsor added.')
    resetSponsor()
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function removeSponsor(sp) {
  if (!confirm(`Delete ${sp.name}? Their listings, claims and portal access go too.`)) return
  await api.del(`/admin/sponsors/${sp.id}`)
  await load()
}

async function grantSponsorAccess(sp) {
  const email = (sponsorUserEmail[sp.id] || '').trim()
  if (!email) return
  try {
    await api.post(`/admin/sponsors/${sp.id}/users`, { email })
    sponsorUserEmail[sp.id] = ''
    flash('Access granted.')
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function revokeSponsorAccess(sp, user) {
  await api.del(`/admin/sponsors/${sp.id}/users/${user.id}`)
  await load()
}

async function moveSponsor(sp, delta) {
  const ordered = [...sponsors.value].sort((a, b) => a.sort_order - b.sort_order)
  const i = ordered.findIndex((x) => x.id === sp.id)
  const j = i + delta
  if (i < 0 || j < 0 || j >= ordered.length) return
  ;[ordered[i], ordered[j]] = [ordered[j], ordered[i]]
  await api.post('/admin/sponsors/reorder', { order: ordered.map((x) => x.id) })
  await load()
}

async function addAdmin() {
  try {
    await api.post('/admin/admins', { email: newAdminEmail.value.trim() })
    newAdminEmail.value = ''
    flash('Admin added.')
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function removeAdmin(a) {
  if (!confirm(`Remove committee access for ${a.email}?`)) return
  try {
    await api.del(`/admin/admins/${a.user_id}`)
    await load()
  } catch (e) {
    error.value = e.message
  }
}

// ------------------------------------------------------- product links -----
// Union products are linked the way eActivities is shaped: ONE product for the
// event, and optionally ONE line per ticket type. Picking "any line or a
// specific line" against every ticket separately was two forms doing one job.
const linkForm = reactive({ purpose: 'locker', union_key: '' })

/** The event-level link, if any. */
const eventLink = computed(
  () => links.value.find((l) => l.event_id === editingId.value && l.ticket_type_id === null) || null,
)

/** Distinct products, for the event-level select. */
const productOptions = computed(() => {
  const seen = new Map()
  for (const p of products.value) {
    if (!seen.has(p.product_id)) seen.set(p.product_id, p.product_name)
  }
  return [...seen.entries()].map(([id, name]) => ({ id, name }))
})

/** Lines belonging to whichever product the event is linked to. */
const lineOptions = computed(() => {
  const productId = eventLink.value?.union_product_id
  if (!productId) return []
  return products.value
    .filter((p) => p.product_id === productId)
    .map((p) => ({ id: p.product_line_id, name: p.line_name || `Line ${p.product_line_id}` }))
})

const eventProduct = ref(null)

function lineFor(ticketTypeId) {
  const link = links.value.find((l) => l.ticket_type_id === ticketTypeId)
  return link?.union_product_line_id ?? null
}

async function saveEventProduct() {
  busy.value = true
  error.value = ''
  try {
    await api.put(`/admin/events/${editingId.value}/product`, {
      union_product_id: eventProduct.value || null,
    })
    flash(eventProduct.value ? 'Product linked to this event.' : 'Product unlinked.')
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function saveTicketLine(t) {
  busy.value = true
  error.value = ''
  try {
    await api.put(`/admin/ticket-types/${t.id}/line`, {
      union_product_line_id: ticketLines[t.id] || null,
    })
    flash(ticketLines[t.id] ? `${t.name} pinned to a line.` : `${t.name} accepts any line.`)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const ticketLines = reactive({})

async function createLink() {
  error.value = ''
  const [productId, lineId] = String(linkForm.union_key).split(':')
  if (!productId) {
    error.value = 'Pick a Union product.'
    return
  }
  try {
    await api.post('/admin/product-links', {
      purpose: linkForm.purpose,
      event_id: null,
      ticket_type_id: null,
      union_product_id: Number(productId),
      union_product_line_id: lineId ? Number(lineId) : null,
    })
    flash('Product linked.')
    linkForm.union_key = ''
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function deleteLink(l) {
  await api.del(`/admin/product-links/${l.id}`)
  await load()
}

function productLabel(l) {
  const p = products.value.find(
    (x) => x.product_id === l.union_product_id &&
      (l.union_product_line_id === null || x.product_line_id === l.union_product_line_id),
  )
  return p ? `${p.product_name}${l.union_product_line_id ? ` — ${p.line_name}` : ' (whole product)'}` : `Product ${l.union_product_id}`
}

// ----------------------------------------------------------- resources -----






// --------------------------------------------------------------- files -----


/** Shared uploader. FormData must not carry a JSON content-type, so this goes
 *  round the api helper and sets only the CSRF header. */






function linkFor(ticketTypeId) {
  return links.value.find((l) => l.ticket_type_id === ticketTypeId) || null
}

async function linkTicket(t) {
  const key = ticketLinks[t.id]
  if (!key) return
  const [productId, lineId] = String(key).split(':')
  busy.value = true
  error.value = ''
  try {
    const existing = linkFor(t.id)
    if (existing) await api.del(`/admin/product-links/${existing.id}`)
    await api.post('/admin/product-links', {
      purpose: 'event_ticket',
      event_id: editingId.value,
      ticket_type_id: t.id,
      union_product_id: Number(productId),
      union_product_line_id: lineId ? Number(lineId) : null,
    })
    flash(`${t.name} linked.`)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function unlinkTicket(t) {
  const existing = linkFor(t.id)
  if (!existing) return
  await api.del(`/admin/product-links/${existing.id}`)
  ticketLinks[t.id] = ''
  await load()
}

const ticketOptions = computed(() =>
  events.value.flatMap((e) =>
    (e.ticket_types || []).map((t) => ({ id: t.id, label: `${e.title} → ${t.name}`, eventId: e.id })),
  ),
)



const warnTone = { error: 'danger', warn: 'accent', info: 'neutral' }
</script>

<template>
  <main class="admin">
    <div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-6); flex-wrap:wrap">
      <h1 style="margin:0; font:var(--type-h1)">Committee admin</h1>
      <span v-if="dash" style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
        {{ dash.academicYear }} · {{ dash.environment }}
      </span>
    </div>

    <p v-if="!session.isAdmin" style="font:var(--type-small); color:var(--danger)">
      You need committee access for this page.
    </p>

    <template v-else>
      <nav class="admin-tabs">
        <Button
          v-for="[id, label] in TABS"
          :key="id"
          :variant="tab === id ? 'primary' : 'ghost'"
          size="sm"
          @click="tab = id"
        >{{ label }}</Button>
      </nav>

      <p v-if="notice" style="font:var(--type-small); color:var(--text-muted)">{{ notice }}</p>
      <p v-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>

      <!-- ============================ OVERVIEW ============================ -->
      <template v-if="tab === 'overview' && dash">
        <div v-if="dash.warnings.length" style="display:grid; gap:var(--space-3); margin-bottom:var(--space-8)">
          <Card v-for="(w, i) in dash.warnings" :key="i" variant="outline">
            <div style="display:flex; align-items:center; gap:var(--space-3); flex-wrap:wrap">
              <Badge :tone="warnTone[w.level]">{{ w.level }}</Badge>
              <span style="font:var(--type-small)">{{ w.message }}</span>
              <Button v-if="w.action === 'sync'" size="sm" variant="secondary" @click="sync">Sync now</Button>
              <Button v-if="w.action === 'links'" size="sm" variant="secondary" @click="tab = 'links'">Link products</Button>
              <Button v-if="w.action === 'overdue'" size="sm" variant="secondary" @click="tab = 'overdue'">Review them</Button>
            </div>
          </Card>
        </div>

        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:var(--space-4)">
          <StatBlock :value="String(counts.members || 0)" label="ICU members" :sub="`${counts.users_members || 0} with accounts`" />
          <StatBlock :value="String(counts.users || 0)" label="Accounts" :sub="`${counts.users_verified || 0} verified`" />
          <StatBlock :value="String(counts.events_upcoming || 0)" label="Upcoming" :sub="`${counts.events || 0} total`" />
          <StatBlock :value="String(counts.res_confirmed || 0)" label="Confirmed" :sub="`${counts.res_held || 0} on hold`" />
          <StatBlock :value="String(counts.res_guest || 0)" label="Guest bookings" :sub="`${counts.waitlist || 0} on waitlists`" />
          <StatBlock :value="String(counts.res_overdue || 0)" label="Unpaid" sub="need a decision" />
          <StatBlock :value="`${counts.lockers_taken || 0}/${counts.lockers || 0}`" label="Lockers" sub="in use" />
          <StatBlock :value="String(counts.union_sales || 0)" label="Union sales" :sub="`${counts.product_links || 0} links`" />
          <StatBlock :value="String(counts.resource_entries || 0)" label="Resource items" :sub="`${counts.resource_pages || 0} pages`" />
        </div>

        <div style="display:flex; gap:var(--space-3); margin:var(--space-8) 0; flex-wrap:wrap">
          <Button :disabled="busy" @click="sync">{{ busy ? 'Working…' : 'Sync from eActivities' }}</Button>
          <Button variant="secondary" :disabled="busy" @click="reconcile">Match bookings now</Button>
        </div>

        <div class="admin-cols admin-cols--split">
          <Card variant="offset">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Holds expiring within 48 hours</h2>
            <p v-if="!dash.needsAttention.length" style="margin:0; font:var(--type-small); color:var(--text-faint)">
              Nothing needs chasing.
            </p>
            <div
              v-for="r in dash.needsAttention"
              :key="r.id"
              style="display:flex; justify-content:space-between; gap:var(--space-4); padding:var(--space-3) 0; border-top:1px solid var(--border-hairline)"
            >
              <div style="display:grid; gap:2px">
                <span style="font:var(--type-small)">{{ r.event_title }} · {{ r.ticket_type_name }}</span>
                <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
                  {{ r.email }}<template v-if="r.booked_as_guest"> · guest</template>

                  <template v-if="r.order_number"> · order {{ r.order_number }}</template>
                </span>
              </div>
              <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-muted); white-space:nowrap">
                {{ new Date(r.expires_at).toLocaleString() }}
              </span>
            </div>
          </Card>

          <Card variant="outline">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Latest bookings</h2>
            <div
              v-for="r in dash.recentBookings"
              :key="r.id"
              style="display:flex; justify-content:space-between; gap:var(--space-3); padding:var(--space-2) 0; border-top:1px solid var(--border-hairline)"
            >
              <span style="font:var(--type-2xs)">{{ r.email }}</span>
              <Badge :tone="r.status === 'confirmed' ? 'success' : 'accent'">{{ r.status }}</Badge>
            </div>

            <TraceRule style="margin:var(--space-5) 0" />
            <h3 style="margin:0 0 var(--space-3); font:var(--type-h5)">Last sync</h3>
            <div v-for="s in dash.syncRuns" :key="s.id" style="font:var(--type-2xs); color:var(--text-faint)">
              {{ s.kind }}: {{ s.ok ? `${s.row_count} rows` : 'failed' }}
            </div>
            <p style="margin:var(--space-4) 0 0; font:var(--type-2xs); color:var(--text-faint)">
              Storage: {{ dash.storage.files }} files, {{ mb(dash.storage.bytes) }}
            </p>
          </Card>
        </div>
      </template>

      <!-- ============================== EVENTS ============================= -->
      <template v-if="tab === 'events'">
        <div class="admin-cols admin-cols--wide">
          <div style="display:grid; gap:var(--space-3)">
            <Card v-for="e in events" :key="e.id" variant="outline">
              <div style="display:flex; justify-content:space-between; gap:var(--space-4); flex-wrap:wrap; align-items:center">
                <div>
                  <div style="display:flex; gap:var(--space-2); align-items:center">
                    <strong style="font:var(--type-h5)">{{ e.title }}</strong>
                    <Badge :tone="e.published ? 'success' : 'neutral'">{{ e.published ? 'Live' : 'Draft' }}</Badge>
                    <Badge v-if="e.sponsorship_claimable" tone="brand">Sponsorable</Badge>
                  </div>
                  <p style="margin:var(--space-2) 0 0; font:var(--type-2xs); color:var(--text-faint)">
                    {{ new Date(e.starts_at).toLocaleString() }} ·
                    {{ e.taken }}/{{ e.capacity ?? '∞' }} booked ·
                    {{ (e.ticket_types || []).map((t) => t.name).join(', ') || 'no ticket types' }}
                  </p>
                </div>
                <div style="display:flex; gap:var(--space-2)">
                  <Button size="sm" variant="ghost" @click="togglePublished(e)">{{ e.published ? 'Unpublish' : 'Publish' }}</Button>
                  <Button size="sm" variant="ghost" @click="editEvent(e)">Edit</Button>
                  <Button size="sm" variant="danger" @click="removeEvent(e)">Delete</Button>
                </div>
              </div>
            </Card>
          </div>

          <Card variant="offset">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">
              {{ editingId ? 'Edit event' : 'New event' }}
            </h2>
            <div style="display:grid; gap:var(--space-4)">
              <Field label="Title"><Input v-model="draft.title" /></Field>
              <Field label="Slug" help="Used in the URL."><Input v-model="draft.slug" /></Field>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--space-3)">
                <Field label="Category"><Input v-model="draft.category" /></Field>
                <Field label="Venue"><Input v-model="draft.venue" /></Field>
              </div>
              <Field label="Event image" help="Upload a file, or paste a URL.">
                <Input v-model="draft.image_url" placeholder="/media/…" />
              </Field>
              <div style="display:flex; gap:var(--space-3); align-items:center; flex-wrap:wrap">
                <input
                  ref="eventImageInput"
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/gif"
                  :disabled="busy"
                  style="font-size:var(--text-xs)"
                  @change="uploadEventImage"
                />
                <img
                  v-if="draft.image_url"
                  :src="draft.image_url"
                  alt=""
                  style="width:64px; height:40px; object-fit:cover; border-radius:var(--radius-sm)"
                />
              </div>
              <Field label="Description">
                <textarea v-model="draft.description" rows="4" class="ees-textarea" />
              </Field>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--space-3)">
                <Field label="Starts"><Input v-model="draft.starts_at" type="datetime-local" /></Field>
                <Field label="Ends"><Input v-model="draft.ends_at" type="datetime-local" /></Field>
              </div>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--space-3)">
                <Field label="Total capacity" help="Blank = per-type only."><Input v-model.number="draft.capacity" type="number" min="1" /></Field>
                <Field label="Hold hours" help="Min 24."><Input v-model.number="draft.hold_hours" type="number" min="24" /></Field>
              </div>

              <TraceRule />
              <h3 style="margin:0; font:var(--type-h5)">Union product</h3>
              <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                One product for the whole event. Each ticket type below can then be
                pinned to one of that product's lines.
              </p>
              <div v-if="editingId" style="display:flex; gap:var(--space-2); align-items:flex-end; flex-wrap:wrap">
                <Field label="Product" style="flex:1 1 220px">
                  <select v-model="eventProduct" class="ees-select">
                    <option :value="null">Not linked</option>
                    <option v-for="p in productOptions" :key="p.id" :value="p.id">{{ p.name }}</option>
                  </select>
                </Field>
                <Button size="sm" :disabled="busy" @click="saveEventProduct">Link</Button>
              </div>
              <p v-else style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                Save the event first, then link its Union product.
              </p>
              <p v-if="!products.length" style="margin:0; font:var(--type-2xs); color:var(--danger)">
                No Union products synced — press "Sync from eActivities" on the Overview.
              </p>

              <TraceRule />
              <h3 style="margin:0; font:var(--type-h5)">Ticket types</h3>
              <div v-for="(t, i) in draft.ticket_types" :key="i" style="display:grid; gap:var(--space-2); padding:var(--space-3) 0; border-top:1px solid var(--border-hairline)">
                <div style="display:grid; grid-template-columns:2fr 1fr 1fr 1fr; gap:var(--space-2)">
                  <Field label="Name"><Input v-model="t.name" placeholder="With alcohol" /></Field>
                  <Field label="Price (p)"><Input v-model.number="t.price_pence" type="number" min="0" /></Field>
                  <Field label="Cap"><Input v-model.number="t.capacity" type="number" min="1" /></Field>
                  <Field label="Max/user"><Input v-model.number="t.max_per_user" type="number" min="1" /></Field>
                </div>
                <label style="font:var(--type-2xs); color:var(--text-muted)">
                  <input v-model="t.members_only" type="checkbox" /> Members only
                  <input v-model="t.requires_purchase" type="checkbox" style="margin-left:var(--space-4)" /> Needs a Union purchase
                </label>

                <!-- Only the LINE is chosen here; the product comes from the
                     event above. That is what stops a "with alcohol" purchase
                     confirming a "no alcohol" seat. -->
                <template v-if="t.id && t.requires_purchase && eventLink">
                  <div style="display:flex; gap:var(--space-2); align-items:flex-end; flex-wrap:wrap">
                    <Field label="Union product line" style="flex:1 1 200px">
                      <select v-model="ticketLines[t.id]" class="ees-select">
                        <option value="">Any line of the product</option>
                        <option v-for="l in lineOptions" :key="`${t.id}-${l.id}`" :value="l.id">
                          {{ l.name }}
                        </option>
                      </select>
                    </Field>
                    <Button size="sm" :disabled="busy" @click="saveTicketLine(t)">Assign</Button>
                  </div>
                  <p v-if="lineFor(t.id)" style="margin:0; font:var(--type-2xs); color:var(--text-muted)">
                    Pinned to line #{{ lineFor(t.id) }} — only purchases of that line confirm this ticket.
                  </p>
                </template>
                <p
                  v-else-if="t.id && t.requires_purchase"
                  style="margin:0; font:var(--type-2xs); color:var(--danger)"
                >
                  Link a Union product to this event first (above) — no purchase can
                  confirm this ticket until you do.
                </p>
              </div>
              <Button size="sm" variant="ghost" @click="draft.ticket_types.push({ name: '', price_pence: 0, capacity: null, max_per_user: 1, members_only: false, requires_purchase: true })">
                Add ticket type
              </Button>

              <label style="font:var(--type-small)"><input v-model="draft.published" type="checkbox" /> Published</label>
              <label style="font:var(--type-small)">
                <input v-model="draft.sponsorship_claimable" type="checkbox" />
                Open to sponsorship
              </label>
              <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                Off by default. Only events ticked here appear in a sponsor's
                claimable list — an event a sponsor requested is never offered back.
              </p>
              <div style="display:flex; gap:var(--space-3)">
                <Button :disabled="busy" @click="saveEvent">{{ editingId ? 'Save changes' : 'Create event' }}</Button>
                <Button variant="ghost" @click="resetDraft">Clear</Button>
              </div>
            </div>
          </Card>
        </div>
      </template>

      <!-- ========================= UNPAID BOOKINGS ======================== -->
      <template v-if="tab === 'overdue'">
        <Card variant="offset">
          <h2 style="margin:0 0 var(--space-3); font:var(--type-h4)">
            Past their purchase window
          </h2>
          <p style="margin:0 0 var(--space-5); font:var(--type-small); color:var(--text-muted)">
            Nothing here is cancelled automatically. A Union sale can be late,
            bought under a different account, or paid in cash — so these are
            yours to decide. Cancelling frees the place for the waitlist.
          </p>

          <p v-if="overdue && !overdue.count" style="margin:0; font:var(--type-small); color:var(--text-faint)">
            Nothing outstanding.
          </p>

          <div
            v-for="r in (overdue?.overdue || [])"
            :key="r.id"
            style="display:grid; gap:var(--space-3); padding:var(--space-4) 0; border-top:1px solid var(--border-hairline)"
          >
            <div style="display:flex; justify-content:space-between; gap:var(--space-4); flex-wrap:wrap">
              <div style="display:grid; gap:2px">
                <span style="font:var(--type-small)">
                  <strong>{{ r.eventTitle }}</strong> · {{ r.ticketType }} × {{ r.quantity }} · {{ r.price }}
                </span>
                <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
                  {{ r.email }}
                  <template v-if="r.guest"> · booked as guest</template>
                  · due {{ new Date(r.dueSince).toLocaleDateString() }}
                  <template v-if="r.orderNumber"> · order {{ r.orderNumber }}</template>
                </span>
              </div>
              <Badge tone="accent">Unpaid</Badge>
            </div>

            <div style="display:flex; gap:var(--space-2); align-items:flex-end; flex-wrap:wrap">
              <Field label="Order number (optional)" style="flex:1 1 200px">
                <Input v-model="orderNumbers[r.id]" placeholder="e.g. AB99ZZ4" />
              </Field>
              <Button size="sm" :disabled="busy" @click="confirmBooking(r)">Confirm</Button>
              <Button size="sm" variant="secondary" :disabled="busy" @click="remind(r)">Send reminder</Button>
              <Button size="sm" variant="danger" :disabled="busy" @click="cancelBooking(r)">Cancel booking</Button>
            </div>
          </div>
        </Card>
      </template>

      <!-- ============================ RESOURCES =========================== -->
      <template v-if="tab === 'resources'">
        <div class="admin-cols admin-cols--sidebar">
          <Card variant="outline">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h5)">Pages</h2>
            <button
              v-for="p in pages"
              :key="p.slug"
              style="display:block; width:100%; text-align:left; padding:var(--space-2) 0; background:none; border:0; cursor:pointer; font:var(--type-small); color:var(--text-body)"
              @click="openPage(p.slug)"
            >{{ p.label }}</button>
            <TraceRule style="margin:var(--space-4) 0" />
            <Button size="sm" variant="ghost" @click="reimport">Re-import from eesoc.com</Button>
          </Card>

          <Card v-if="pageDetail" variant="offset">
            <h2 style="margin:0 0 var(--space-2); font:var(--type-h4)">{{ pageDetail.title }}</h2>
            <p style="margin:0 0 var(--space-5); font:var(--type-2xs); color:var(--text-faint)">
              {{ pageDetail.entryCount }} entries · /resources/{{ pageDetail.slug }}
            </p>

            <div style="display:grid; gap:var(--space-2); max-height:460px; overflow:auto">
              <div
                v-for="node in pageDetail.entries"
                :key="node.id"
                style="border-top:1px solid var(--border-hairline); padding:var(--space-2) 0"
              >
                <div style="display:flex; justify-content:space-between; gap:var(--space-3); align-items:center">
                  <span style="font:var(--type-small)"><Badge tone="brand">{{ node.kind }}</Badge> {{ node.title }}</span>
                  <span style="display:flex; gap:4px">
                    <Button size="sm" variant="ghost" @click="moveEntry(node, -1)">↑</Button>
                    <Button size="sm" variant="ghost" @click="moveEntry(node, 1)">↓</Button>
                    <Button size="sm" variant="danger" @click="removeEntry(node)">×</Button>
                  </span>
                </div>
                <div v-for="c in node.children" :key="c.id" style="padding-left:var(--space-5); display:flex; justify-content:space-between; align-items:center">
                  <span style="font:var(--type-2xs); color:var(--text-muted)">{{ c.title }} ({{ c.children.length }})</span>
                  <Button size="sm" variant="ghost" @click="removeEntry(c)">×</Button>
                </div>
              </div>
            </div>

            <TraceRule style="margin:var(--space-5) 0" />
            <h3 style="margin:0 0 var(--space-3); font:var(--type-h5)">Add an entry</h3>
            <div style="display:grid; gap:var(--space-3)">
              <Field label="Title"><Input v-model="newEntry.title" /></Field>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--space-3)">
                <Field label="Kind">
                  <select v-model="newEntry.kind" class="ees-select">
                    <option value="section">Section</option>
                    <option value="item">Item</option>
                    <option value="year">Year</option>
                    <option value="link">Link</option>
                    <option value="note">Note</option>
                  </select>
                </Field>
                <Field label="Nest under">
                  <select v-model="newEntry.parent_id" class="ees-select">
                    <option :value="null">Top level</option>
                    <option v-for="n in pageDetail.entries" :key="n.id" :value="n.id">{{ n.title }}</option>
                  </select>
                </Field>
              </div>
              <Field label="URL (optional)"><Input v-model="newEntry.url" /></Field>
              <Field label="Body" help="Markdown: links, **bold**, *italics*, - lists.">
                <textarea v-model="newEntry.body" rows="4" class="ees-textarea" />
              </Field>
              <Button @click="addEntry">Add entry</Button>
            </div>
          </Card>
          <Card v-else variant="outline">
            <p style="margin:0; font:var(--type-small); color:var(--text-faint)">Pick a page to edit.</p>
          </Card>
        </div>
      </template>

      <!-- ============================== FILES ============================= -->
      <template v-if="tab === 'files'">
        <Card variant="offset">
          <h2 style="margin:0 0 var(--space-3); font:var(--type-h4)">Files</h2>
          <p style="margin:0 0 var(--space-5); font:var(--type-small); color:var(--text-muted)">
            Stored on the media volume, served by nginx. JPEG, PNG, WebP, GIF and PDF up to 10 MB.
            SVG is deliberately not accepted — it can carry script and would be served from our own origin.
          </p>
          <input type="file" accept="image/jpeg,image/png,image/webp,image/gif,application/pdf" :disabled="busy" @change="upload" />

          <div v-if="uploads" style="margin-top:var(--space-6)">
            <p style="font:var(--type-2xs); color:var(--text-faint)">
              {{ uploads.usage.files }} files · {{ mb(uploads.usage.bytes) }}
            </p>
            <div v-for="u in uploads.uploads" :key="u.id" style="display:flex; justify-content:space-between; align-items:center; gap:var(--space-4); padding:var(--space-3) 0; border-top:1px solid var(--border-hairline)">
              <div style="display:flex; align-items:center; gap:var(--space-3); min-width:0">
                <img v-if="u.content_type.startsWith('image/')" :src="u.url" alt="" style="width:44px; height:44px; object-fit:cover; border-radius:var(--radius-sm)" />
                <div style="display:grid; gap:2px; min-width:0">
                  <span style="font:var(--type-2xs); overflow:hidden; text-overflow:ellipsis">{{ u.original_name }}</span>
                  <code style="font-size:var(--text-2xs); color:var(--text-faint)">{{ u.url }}</code>
                </div>
              </div>
              <div style="display:flex; gap:var(--space-2)">
                <Button size="sm" variant="ghost" @click="copyUrl(u.url)">Copy URL</Button>
                <Button size="sm" variant="danger" @click="removeUpload(u)">Delete</Button>
              </div>
            </div>
          </div>
        </Card>
      </template>

      <!-- =========================== COMMITTEE ============================ -->
      <template v-if="tab === 'committee'">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:var(--space-4); flex-wrap:wrap; margin-bottom:var(--space-5)">
          <p style="margin:0; font:var(--type-small); color:var(--text-muted); max-width:70ch">
            Names, roles and emails come from the Union. Intros, photos, display
            names and ordering are ours and are kept when the roster is re-synced
            — they are matched on login, so the same person keeps their entry.
          </p>
          <Button :disabled="busy" @click="syncCommittee">
            {{ busy ? 'Working…' : 'Sync committee now' }}
          </Button>
        </div>

        <p v-if="!committee.length" style="font:var(--type-small); color:var(--text-faint)">
          No committee synced yet. Press "Sync committee now".
        </p>

        <div v-for="group in committeeRows" :key="group.row" style="margin-bottom:var(--space-8)">
          <div style="display:flex; align-items:baseline; gap:var(--space-3); margin-bottom:var(--space-3)">
            <span style="font-family:var(--font-mono); font-size:var(--text-2xs); text-transform:uppercase; letter-spacing:var(--tracking-label); color:var(--text-accent)">
              Row {{ group.row }}
            </span>
            <span style="font:var(--type-2xs); color:var(--text-faint)">
              {{ group.members.length }} across this row on the public page
            </span>
          </div>

          <div style="display:grid; gap:var(--space-3)">
            <Card v-for="(m, i) in group.members" :key="m.login || m.cid" variant="outline">
              <div style="display:grid; grid-template-columns:96px minmax(0,1fr) auto; gap:var(--space-4); align-items:start">
                <div>
                  <img
                    v-if="m.photoUrl"
                    :src="m.photoUrl"
                    :alt="m.name"
                    style="width:96px; height:96px; object-fit:cover; border-radius:var(--radius-sm)"
                  />
                  <div v-else style="width:96px; height:96px; background:var(--surface-sunken); border-radius:var(--radius-sm)" />
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    :disabled="busy"
                    style="margin-top:var(--space-2); font-size:10px; width:96px"
                    @change="(e) => uploadMemberPhoto(m, e)"
                  />
                </div>

                <div style="display:grid; gap:var(--space-3); min-width:0">
                  <div>
                    <strong style="font:var(--type-h5)">{{ m.name }}</strong>
                    <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
                      · {{ m.unionRole }} · {{ m.login }}
                    </span>
                  </div>
                  <Field label="Display role" help="Blank uses the Union's post name.">
                    <Input v-model="m.displayRole" :placeholder="m.unionRole" />
                  </Field>
                  <Field label="Intro">
                    <textarea
                      v-model="m.intro"
                      rows="2" class="ees-textarea"
                    />
                  </Field>
                </div>

                <div style="display:grid; gap:4px; justify-items:end">
                  <div style="display:flex; gap:4px; align-items:center">
                    <span style="font:var(--type-2xs); color:var(--text-faint)">Row</span>
                    <select
                      :value="m.row || 1"
                      class="ees-select"
                      @change="(e) => setRow(m, e.target.value)"
                    >
                      <option v-for="g in committeeRows" :key="g.row" :value="g.row">{{ g.row }}</option>
                      <option :value="nextRow">New row {{ nextRow }}</option>
                    </select>
                  </div>
                  <div style="display:flex; gap:4px">
                    <Button size="sm" variant="ghost" :disabled="i === 0" @click="moveMember(m, -1)">←</Button>
                    <Button size="sm" variant="ghost" :disabled="i === group.members.length - 1" @click="moveMember(m, 1)">→</Button>
                  </div>
                  <Button size="sm" :disabled="busy" @click="saveMember(m)">Save</Button>
                </div>
              </div>
            </Card>
          </div>
        </div>
      </template>

      <!-- ========================= UNION PRODUCTS ========================= -->
      <template v-if="tab === 'links'">
        <p style="margin:0 0 var(--space-5); font:var(--type-small); color:var(--text-muted); max-width:75ch">
          <strong>Event tickets are linked on the event itself</strong> — open an
          event under Events, pick its Union product, then pin a line to each
          ticket type. This page is only for links that belong to no event:
          lockers and membership.
        </p>

        <div class="admin-cols">
          <Card variant="offset">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Link a locker or membership product</h2>
            <div style="display:grid; gap:var(--space-4)">
              <Field label="What is it for">
                <select v-model="linkForm.purpose" class="ees-select">
                  <option value="locker">Locker</option>
                  <option value="membership">Membership</option>
                </select>
              </Field>
              <Field label="Union product" help="'Any line' is the safe default — line ids are not guaranteed unique across products.">
                <select v-model="linkForm.union_key" class="ees-select">
                  <option value="">Choose…</option>
                  <optgroup label="Any line of the product">
                    <option v-for="p in products" :key="`la${p.product_id}`" :value="`${p.product_id}`">{{ p.product_name }} (any line)</option>
                  </optgroup>
                  <optgroup label="Specific line">
                    <option v-for="p in products" :key="`ls${p.product_id}:${p.product_line_id}`" :value="`${p.product_id}:${p.product_line_id}`">{{ p.product_name }} — {{ p.line_name }}</option>
                  </optgroup>
                </select>
              </Field>
              <Button :disabled="!products.length" @click="createLink">Link product</Button>
              <p v-if="!products.length" style="margin:0; font:var(--type-2xs); color:var(--danger)">
                No Union products synced yet — press "Sync from eActivities" on the Overview first.
              </p>
            </div>
          </Card>

          <Card variant="outline">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">All links</h2>
            <p v-if="!links.length" style="margin:0; font:var(--type-small); color:var(--text-faint)">Nothing linked yet.</p>
            <div v-for="l in links" :key="l.id" style="display:flex; justify-content:space-between; align-items:center; gap:var(--space-3); padding:var(--space-3) 0; border-top:1px solid var(--border-hairline)">
              <span style="font:var(--type-small); min-width:0">
                <Badge tone="brand">{{ l.purpose.replace('_', ' ') }}</Badge>
                {{ productLabel(l) }}
                <span style="color:var(--text-faint)">(#{{ l.union_product_id }}<template v-if="l.union_product_line_id">:{{ l.union_product_line_id }}</template>)</span>
              </span>
              <Button size="sm" variant="ghost" @click="deleteLink(l)">Remove</Button>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================= PEOPLE ============================= -->
      <template v-if="tab === 'people'">
        <div class="admin-cols">
          <Card variant="offset">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Committee access</h2>
            <div v-for="a in admins" :key="a.user_id" style="display:flex; justify-content:space-between; align-items:center; padding:var(--space-3) 0; border-top:1px solid var(--border-hairline)">
              <div style="display:grid; gap:2px">
                <span style="font:var(--type-small)">{{ [a.first_name, a.last_name].filter(Boolean).join(' ') || a.email }}</span>
                <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">{{ a.email }}</span>
              </div>
              <Button size="sm" variant="ghost" @click="removeAdmin(a)">Remove</Button>
            </div>
            <TraceRule style="margin:var(--space-5) 0" />
            <Field label="Add an admin" help="They must have signed in at least once.">
              <Input v-model="newAdminEmail" placeholder="name@imperial.ac.uk" />
            </Field>
            <Button size="sm" style="margin-top:var(--space-3)" @click="addAdmin">Grant access</Button>
          </Card>

          <Card variant="outline">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Accounts</h2>
            <div style="display:grid; gap:var(--space-2); font:var(--type-small)">
              <div style="display:flex; justify-content:space-between"><span>Total accounts</span><strong>{{ counts.users }}</strong></div>
              <div style="display:flex; justify-content:space-between"><span>Verified</span><strong>{{ counts.users_verified }}</strong></div>
              <div style="display:flex; justify-content:space-between"><span>Linked to a member</span><strong>{{ counts.users_members }}</strong></div>
              <div style="display:flex; justify-content:space-between"><span>Live sessions</span><strong>{{ counts.sessions_live }}</strong></div>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================ SPONSORS ============================ -->
      <template v-if="tab === 'sponsors'">
        <p v-if="sponsorTotals" style="font:var(--type-small); color:var(--text-muted); margin:0 0 var(--space-5)">
          <strong>{{ sponsorTotals.total }}</strong> committed across
          {{ sponsorTotals.paying }} of {{ sponsorTotals.total_count ?? sponsors.length }} sponsors.
          <span style="color:var(--text-faint)">Committee only — never shown publicly.</span>
        </p>

        <div class="admin-cols admin-cols--wide">
          <div style="display:grid; gap:var(--space-3)">
            <Card v-for="(sp, i) in sponsors" :key="sp.id" variant="outline">
              <div style="display:flex; justify-content:space-between; gap:var(--space-3); align-items:flex-start; flex-wrap:wrap">
                <div style="display:grid; gap:2px; min-width:0">
                  <span style="font:var(--type-h5)">
                    {{ sp.name }}
                    <Badge :tone="sp.active ? 'success' : 'neutral'">{{ sp.active ? 'Live' : 'Hidden' }}</Badge>
                  </span>
                  <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
                    {{ sp.sector || 'No sector' }} · {{ sp.amount }}
                    <template v-if="sp.agreement_year"> · {{ sp.agreement_year }}</template>
                  </span>
                  <span v-if="sp.internal_note" style="font:var(--type-2xs); color:var(--text-muted)">
                    {{ sp.internal_note }}
                  </span>
                </div>
                <div style="display:flex; gap:4px">
                  <Button size="sm" variant="ghost" @click="moveSponsor(sp, -1)">↑</Button>
                  <Button size="sm" variant="ghost" @click="moveSponsor(sp, 1)">↓</Button>
                  <Button size="sm" variant="ghost" @click="editSponsor(sp)">Edit</Button>
                  <Button size="sm" variant="danger" @click="removeSponsor(sp)">Delete</Button>
                </div>
              </div>

              <!-- Several people can hold access to one sponsor. -->
              <div style="margin-top:var(--space-4); padding-top:var(--space-3); border-top:1px solid var(--border-hairline)">
                <span style="font-family:var(--font-mono); font-size:var(--text-2xs); text-transform:uppercase; color:var(--text-faint)">
                  Portal access
                </span>
                <div v-for="u in sp.users" :key="u.id" style="display:flex; justify-content:space-between; align-items:center; padding:var(--space-2) 0">
                  <span style="font:var(--type-2xs)">{{ u.email }}</span>
                  <Button size="sm" variant="ghost" @click="revokeSponsorAccess(sp, u)">Revoke</Button>
                </div>
                <p v-if="!sp.users?.length" style="margin:var(--space-2) 0; font:var(--type-2xs); color:var(--text-faint)">
                  Nobody can log in for this sponsor yet.
                </p>
                <div style="display:flex; gap:var(--space-2); align-items:flex-end">
                  <Field label="Add an email" style="flex:1">
                    <Input v-model="sponsorUserEmail[sp.id]" placeholder="name@company.com" />
                  </Field>
                  <Button size="sm" @click="grantSponsorAccess(sp)">Grant</Button>
                </div>
                <p style="margin:var(--space-2) 0 0; font:var(--type-2xs); color:var(--text-faint)">
                  They must have signed in once first, which proves they control that address.
                </p>
              </div>
            </Card>
            <p v-if="!sponsors.length" style="font:var(--type-small); color:var(--text-faint)">No sponsors yet.</p>
          </div>

          <Card variant="offset">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">
              {{ editingSponsor ? 'Edit sponsor' : 'Add a sponsor' }}
            </h2>
            <div style="display:grid; gap:var(--space-3)">
              <Field label="Name"><Input v-model="sponsorDraft.name" /></Field>
              <Field label="Slug" help="Used in URLs."><Input v-model="sponsorDraft.slug" /></Field>
              <Field label="Sector" help="e.g. Quantitative Trading"><Input v-model="sponsorDraft.sector" /></Field>
              <Field label="What we do together"><Input v-model="sponsorDraft.strand" /></Field>
              <Field label="One line"><Input v-model="sponsorDraft.one_line" /></Field>
              <Field label="Blurb">
                <textarea v-model="sponsorDraft.blurb" rows="3" class="ees-textarea" />
              </Field>
              <Field label="Website"><Input v-model="sponsorDraft.site" /></Field>
              <Field label="Logo URL" help="Upload under Files, then paste."><Input v-model="sponsorDraft.logo_url" /></Field>
              <Field label="CV contact email" help="Published so members can send CVs straight to them — EESoc never handles CVs.">
                <Input v-model="sponsorDraft.cv_contact_email" type="email" />
              </Field>

              <TraceRule />
              <span style="font-family:var(--font-mono); font-size:var(--text-2xs); text-transform:uppercase; letter-spacing:var(--tracking-label); color:var(--danger)">
                Committee only — never public
              </span>
              <Field label="Sponsorship amount (pence)" help="250000 = £2,500.">
                <Input v-model.number="sponsorDraft.amount_pence" type="number" min="0" />
              </Field>
              <Field label="Agreement year"><Input v-model="sponsorDraft.agreement_year" placeholder="26-27" /></Field>
              <Field label="Internal note">
                <textarea v-model="sponsorDraft.internal_note" rows="2" class="ees-textarea" />
              </Field>

              <label style="font:var(--type-small)"><input v-model="sponsorDraft.active" type="checkbox" /> Visible on the site</label>
              <div style="display:flex; gap:var(--space-3)">
                <Button :disabled="busy" @click="saveSponsor">{{ editingSponsor ? 'Save' : 'Add sponsor' }}</Button>
                <Button variant="ghost" @click="resetSponsor">Clear</Button>
              </div>
            </div>
          </Card>
        </div>
      </template>

    </template>
  </main>
</template>

<style scoped>
/* Fixed page frame. Everything inside must be able to shrink, or a wide tab
   (the event editor, the product tables) pushes the container past its
   max-width and the tab bar visibly slides as you switch tabs. */
.admin {
  width: 100%;
  max-width: var(--container-lg);
  margin-inline: auto;
  padding: var(--space-12) var(--space-8);
  min-width: 0;
  /* Wide tab content must not push the frame wider than this, or the centred
     tab bar visibly slides as you switch tabs. */
  overflow-x: clip;
}

/* Centred on screen rather than starting at the content's left edge. The frame
   above is already centred, so centring within it centres on screen. */
.admin-tabs {
  display: flex;
  justify-content: center;
  gap: var(--space-2);
  margin: var(--space-8) 0 var(--space-6);
  flex-wrap: wrap;
}
.admin-cols {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-8);
  align-items: start;
}
.admin-cols--wide {
  grid-template-columns: minmax(0, 1fr) minmax(0, 0.9fr);
}
.admin-cols--split {
  grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr);
}
.admin-cols--sidebar {
  grid-template-columns: minmax(0, 280px) minmax(0, 1fr);
}
.admin-cols > * {
  min-width: 0;
}
@media (max-width: 1080px) {
  .admin-cols,
  .admin-cols--wide,
  .admin-cols--split,
  .admin-cols--sidebar {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
