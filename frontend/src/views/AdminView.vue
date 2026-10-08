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
import Dialog from '@/components/ds/Dialog.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import session, { can, hasAdminArea } from '@/stores/session'

const route = useRoute()
const router = useRouter()

// Each tab names the capability that makes it useful. 'card' is the one tab
// that needs no role at all — a committee member's own entry — so it is keyed
// on owning a card instead.
const TABS = [
  ['overview', 'Overview', 'any'],
  ['card', 'My card', 'card'],
  ['events', 'Events', 'eventish'],
  ['overdue', 'Unpaid bookings', 'eventish'],
  ['resources', 'Resources', 'site'],
  ['files', 'Files', 'site'],
  ['committee', 'Committee', 'site'],
  ['links', 'Union products', 'site'],
  ['email', 'Email', 'site'],
  ['people', 'People', 'any'],
  ['sponsors', 'Sponsors', 'sponsors'],
]

function allowed(need) {
  if (need === 'any') return session.isAdmin
  if (need === 'card') return Boolean(session.committeeLogin)
  // Events and the bookings that go with them: the events officers see all of
  // it, the industry officers see it for their own events.
  if (need === 'eventish') return can('events') || can('industry')
  return can(need)
}

const visibleTabs = computed(() => TABS.filter(([, , need]) => allowed(need)))

const tab = ref(route.query.tab || '')
watch(tab, (t) => router.replace({ query: { ...route.query, tab: t } }))

// Land on the first tab this person can actually use. Somebody with only
// their own card has no Overview to open, and an events officer sent a link
// to ?tab=sponsors should not get an empty page.
watch(
  visibleTabs,
  (tabs) => {
    if (!tabs.length) return
    const ids = tabs.map(([id]) => id)
    if (!ids.includes(tab.value)) tab.value = ids[0]
  },
  { immediate: true },
)

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
const roleOptions = ref([])
const canManageUsers = ref(false)
const sponsors = ref([])
const sponsorTotals = ref(null)
const committee = ref([])
// This person's own committee entry, for the "My card" tab.
const myCard = ref(null)
const newRole = ref('admin')
const impersonateEmail = ref('')

// ------------------------------------------------------------------ email ---
const audiences = ref([])
const mailStatus = ref(null)
const campaigns = ref([])
const compose = reactive({ subject: '', heading: '', body: '', audiences: [] })
const previewHtml = ref('')
const previewCount = ref(null)
const previewSample = ref([])
// Non-null while the "email people about this event" dialog is open; holds the
// event it is about, so the same composer serves both.
const announcing = ref(null)

const busy = ref(false)
const error = ref('')
const notice = ref('')

function flash(message) {
  notice.value = message
  setTimeout(() => (notice.value = ''), 6000)
}

/** Fetch only what this role is allowed to see.
 *
 *  `Promise.all` rejects on the first failure, so asking for everything and
 *  letting the 403s land would leave an events officer staring at "Not your
 *  area of the site" with none of their own data loaded. Each request is
 *  therefore gated on the same capability that gates its tab, and `null` for
 *  anything they cannot have. */
function maybe(allow, request) {
  return allow ? request : Promise.resolve(null)
}

async function load() {
  error.value = ''
  const eventish = can('events') || can('industry')
  try {
    const [d, ev, pr, ln, pg, ad, sp, cm, mine] = await Promise.all([
      maybe(session.isAdmin, api.get('/admin/dashboard')),
      maybe(eventish, api.get('/admin/events')),
      maybe(session.isAdmin, api.get('/admin/union/products')),
      maybe(session.isAdmin, api.get('/admin/product-links')),
      maybe(can('site'), api.get('/resources')),
      maybe(session.isAdmin, api.get('/admin/admins')),
      maybe(can('sponsors'), api.get('/admin/sponsors')),
      maybe(can('site'), api.get('/admin/committee')),
      maybe(Boolean(session.committeeLogin), api.get('/committee/me')),
    ])
    dash.value = d
    events.value = ev ?? []
    products.value = pr ?? []
    links.value = ln ?? []
    pages.value = pg?.pages ?? []
    admins.value = ad?.admins ?? []
    roleOptions.value = ad?.roles ?? []
    canManageUsers.value = Boolean(ad?.canManage)
    sponsors.value = sp?.sponsors ?? sp ?? []
    sponsorTotals.value = sp?.totals ?? null
    committee.value = cm?.committee ?? []
    myCard.value = mine?.card ?? null
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  await load()
  loadSponsorInbox()
  // Arriving from an event's page with "Email everyone who hasn't bought".
  const id = Number(route.query.announce)
  if (id) {
    const e = events.value.find((x) => x.id === id)
    if (e) announceEvent(e, route.query.audience ? String(route.query.audience) : null)
    router.replace({ query: { tab: route.query.tab || 'events' } })
  }
})

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
  // Not announced is the default for a new event: the site shows On sale
  // soon and refuses bookings until someone chooses a date.
  sales_scheduled: false,
  sales_open_at: '',
  // Where to buy, for the emails. Only needed when the Union's own product
  // feed carries no URL — which, for our centre, is most of the time.
  purchase_url: '',
  // A sign-up form of somebody else's — usually a sponsor's. When set, the
  // event page links there instead of offering our booking.
  signup_url: '',
  sponsorship_claimable: false,
  // An industry event — careers fair, sponsor talk, site visit. It is what
  // decides whether the industry officers can edit it, so it is ticked
  // deliberately rather than guessed from the category. Somebody who may only
  // edit industry events gets it set for them, server-side.
  industry: !can('events'),
  // Empty by default. An event with no ticket types is a drop-in event: no
  // booking, no "on sale" state, just a listing. Adding a type is the
  // deliberate act, not removing the one that was there.
  ticket_types: [],
})
const draft = reactive(blank())
const editingId = ref(null)

// The API sends UTC ("…T17:00:00+00:00"); a datetime-local input wants the
// browser's own clock with no offset. Slicing the ISO string showed the UTC
// time as if it were local — an hour early in BST — and saving the form
// unchanged then moved the event an hour earlier every time.
function toLocalInput(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

function editEvent(e) {
  editingId.value = e.id
  Object.assign(draft, {
    ...blank(), ...e,
    starts_at: toLocalInput(e.starts_at),
    ends_at: toLocalInput(e.ends_at),
    sales_scheduled: Boolean(e.sales_open_at),
    sales_open_at: toLocalInput(e.sales_open_at),
    signup_url: e.signup_url ?? '',
    ticket_types: e.ticket_types ?? [],
  })
  // Prefill the product and per-ticket line selects from existing links.
  const ev = links.value.find((l) => l.event_id === e.id && l.ticket_type_id === null)
  eventProduct.value = ev?.union_product_id ?? null
  for (const t of draft.ticket_types) {
    const link = links.value.find((l) => l.ticket_type_id === t.id)
    // Same "productId:lineId" key the product <select> uses, so the
    // existing choice shows as selected rather than blank.
    ticketProducts[t.id] = link
      ? `${link.union_product_id}:${link.union_product_line_id ?? ''}`
      : ''
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
      // datetime-local is the browser's own clock; send the instant in UTC.
      starts_at: new Date(draft.starts_at).toISOString(),
      ends_at: draft.ends_at ? new Date(draft.ends_at).toISOString() : null,
      // The radio decides, not whether the field happens to be empty.
      sales_open_at:
        draft.sales_scheduled && draft.sales_open_at
          ? new Date(draft.sales_open_at).toISOString()
          : null,
      signup_url: draft.signup_url.trim() || null,
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
  // Reloaded every visit rather than cached: a send in progress updates its
  // counts as it goes, and a stale "0 sent" is exactly what makes somebody
  // press send a second time.
  if (t === 'email') loadEmail()
})

// --------------------------------------------------------------- email -----
async function loadEmail() {
  const [a, c] = await Promise.all([
    api.get('/admin/email/audiences'),
    api.get('/admin/email/campaigns'),
  ])
  audiences.value = a.audiences
  mailStatus.value = a.mail
  campaigns.value = c
  loadTestList().catch(() => {})
}

// The "Test list" audience: a few committee addresses to try a send on first.
const testRecipients = ref([])
const newTestEmail = ref('')

async function loadTestList() {
  testRecipients.value = (await api.get('/admin/email/test-recipients')).recipients
}

async function changeTestList(action) {
  busy.value = true
  error.value = ''
  try {
    const res =
      action === 'add'
        ? await api.post('/admin/email/test-recipients', { email: newTestEmail.value.trim() })
        : await api.del('/admin/email/test-recipients', { email: action })
    testRecipients.value = res.recipients
    if (action === 'add') newTestEmail.value = ''
    // The audience's count and description come from the server.
    audiences.value = (await api.get('/admin/email/audiences')).audiences
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

/** Audiences arrive flat with a `group` on each; the picker shows them grouped
 *  because "EESoc members" and "EEE Year 2" are different kinds of thing and
 *  mixing them in one list is how the wrong one gets ticked. */
const audienceGroups = computed(() => {
  const out = new Map()
  for (const a of audiences.value) {
    if (!out.has(a.group)) out.set(a.group, [])
    out.get(a.group).push(a)
  }
  return [...out.entries()]
})

function toggleAudience(key) {
  const at = compose.audiences.indexOf(key)
  if (at === -1) compose.audiences.push(key)
  else compose.audiences.splice(at, 1)
  // The count on screen must never describe a different audience from the one
  // ticked, so it is cleared the moment the selection changes.
  previewCount.value = null
}

async function preview() {
  busy.value = true
  error.value = ''
  try {
    const res = await api.post('/admin/email/preview', {
      body: compose.body,
      audiences: compose.audiences,
    })
    previewHtml.value = res.html
    previewCount.value = res.recipients
    previewSample.value = res.sample
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function sendEmail() {
  const count = previewCount.value
  const what = announcing.value ? `about ${announcing.value.title}` : 'newsletter'
  if (count === null) {
    error.value = 'Press Preview first, so you can see who this goes to.'
    return
  }
  if (!confirm(`Send this ${what} to ${count} recipient(s)? This cannot be undone.`)) return

  busy.value = true
  error.value = ''
  try {
    const path = announcing.value
      ? `/admin/events/${announcing.value.id}/announce`
      : '/admin/email/send'
    const res = await api.post(path, {
      subject: compose.subject,
      heading: compose.heading || null,
      body: compose.body,
      audiences: compose.audiences,
    })
    flash(res.message)
    closeCompose()
    await loadEmail()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

// The "This event" audiences (hasn't bought, bought, booked, waitlist), loaded
// when the dialog opens because their counts are per event.
const eventAudiences = ref([])
const composeGroups = computed(() =>
  eventAudiences.value.length
    ? [['This event', eventAudiences.value], ...audienceGroups.value]
    : audienceGroups.value,
)

/** Open the composer for one event, pre-filled with a sensible subject.
 *  `preselect` ticks one audience, e.g. "unpaid:12" from the event page. */
function announceEvent(e, preselect = null) {
  announcing.value = e
  eventAudiences.value = []
  api
    .get(`/admin/events/${e.id}/audiences`)
    .then((r) => {
      if (announcing.value?.id === e.id) eventAudiences.value = r.audiences
    })
    .catch(() => {})
  compose.subject = `${e.title} — ${new Date(e.starts_at).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}`
  compose.heading = ''
  compose.body = ''
  compose.audiences = preselect ? [preselect] : []
  previewHtml.value = ''
  previewCount.value = null
  if (!audiences.value.length) loadEmail()
}

function closeCompose() {
  announcing.value = null
  eventAudiences.value = []
  compose.subject = ''
  compose.heading = ''
  compose.body = ''
  compose.audiences = []
  previewHtml.value = ''
  previewCount.value = null
  previewSample.value = []
}

async function runAnnouncePass() {
  busy.value = true
  try {
    const res = await api.post('/admin/email/announce-now')
    const announced = res.announced?.events ?? 0
    const chased = res.chased?.sent ?? 0
    flash(`${announced} event(s) announced, ${chased} purchase reminder(s) sent.`)
    await loadEmail()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const campaignTone = {
  sent: 'success', sending: 'accent', failed: 'danger', draft: 'neutral',
}

// -------------------------------------------------------------- people -----
const newAdminEmail = ref('')

// ------------------------------------------------------------ my card -----
async function saveMyCard() {
  if (!myCard.value) return
  busy.value = true
  error.value = ''
  try {
    const res = await api.put('/committee/me', {
      intro: myCard.value.intro ?? '',
      photo_url: myCard.value.photoUrl ?? null,
    })
    myCard.value = res.card
    flash('Your card is updated. It is live on the committee page.')
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function uploadMyPhoto(event) {
  const file = event.target.files?.[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('file', file)
    // Its own endpoint, not /admin/uploads: a committee member with no role
    // can put a face on their card without being handed the file manager.
    const res = await fetch('/api/committee/me/photo', {
      method: 'POST',
      body: form,
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': document.cookie.match(/(^|;\s*)csrf=([^;]*)/)?.[2] ?? '' },
    })
    const row = await res.json()
    if (!res.ok) throw new Error(row?.detail || 'Upload failed')
    myCard.value.photoUrl = row.url
    await saveMyCard()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
    event.target.value = ''
  }
}

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
      hidden: Boolean(m.hidden),
      user_email: (m.linkedEmail || '').trim() || null,
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
    // Both carried explicitly. They are not on this form, and omitting them
    // would send the defaults — quietly un-hiding somebody, or dropping the
    // linked email, on a reorder that was only meant to move a card left.
    hidden: Boolean(person.hidden),
    user_email: (person.linkedEmail || '').trim() || null,
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

// What sponsors are waiting on, across all of them: marketing to approve,
// requests to answer, interest to decide, new roles members have not heard of.
const sponsorInbox = ref(null)
const rolesDigest = ref(null)

async function loadSponsorInbox() {
  if (!can('sponsors')) return
  try {
    const [inbox, digest] = await Promise.all([
      api.get('/admin/sponsors/inbox'),
      api.get('/admin/sponsors/roles-digest'),
    ])
    sponsorInbox.value = inbox
    rolesDigest.value = digest
  } catch (e) {
    error.value = e.message
  }
}

// The same inbox, summarised for the Overview: one line per kind of thing
// waiting, with the first few items, so nobody has to open the Sponsors tab to
// find out whether a sponsor is waiting on them.
const sponsorPending = computed(() => {
  const inbox = sponsorInbox.value
  if (!inbox) return []
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`
  const groups = [
    {
      key: 'notes',
      label: plural(inbox.notes.length, 'question or note from a sponsor', 'questions and notes from sponsors'),
      items: inbox.notes.map((n) => ({
        to: `/admin/sponsors/${n.sponsorId}#notes`,
        text: `${n.sponsor} · ${n.label}${n.count > 1 ? ` (${n.count})` : ''}`,
      })),
    },
    {
      key: 'marketing',
      label: plural(inbox.marketing.length, 'marketing request to share', 'marketing requests to share'),
      items: inbox.marketing.map((m) => ({
        to: `/admin/sponsors/${m.sponsorId}#marketing`,
        text: `${m.sponsor} · ${m.subject}`,
      })),
    },
    {
      key: 'requests',
      label: plural(inbox.requests.length, 'event request to answer', 'event requests to answer'),
      items: inbox.requests.map((r) => ({
        to: `/admin/sponsors/${r.sponsorId}#requests`,
        text: `${r.sponsor} · ${r.format}${r.status === 'open' ? '' : ' · in discussion'}`,
      })),
    },
    {
      key: 'interest',
      label: plural(inbox.interest.length, 'event interest to decide', 'event interests to decide'),
      items: inbox.interest.map((x) => ({
        to: `/admin/sponsors/${x.sponsorId}#events`,
        text: `${x.sponsor} · ${x.event}`,
      })),
    },
    {
      key: 'listings',
      label: plural(inbox.listings.length, 'new role members haven\'t been told about', 'new roles members haven\'t been told about'),
      items: inbox.listings.map((l) => ({ to: null, text: `${l.sponsor} · ${l.title}` })),
    },
  ]
  return groups.filter((g) => g.items.length)
})
const sponsorPendingTotal = computed(() =>
  sponsorPending.value.reduce((n, g) => n + g.items.length, 0),
)

// Coming back to the Overview or the Sponsors tab shows the current state,
// not whatever it was when the page first loaded.
watch(tab, (t) => {
  if (t === 'overview' || t === 'sponsors') loadSponsorInbox()
})

async function sendRolesDigest() {
  const d = rolesDigest.value
  if (!d?.count) return
  if (!confirm(`Email ${d.count} new role(s) to ${d.recipients} member(s) subscribed to jobs and internships?`)) return
  busy.value = true
  try {
    const res = await api.post('/admin/sponsors/roles-digest')
    flash(res.message)
    await loadSponsorInbox()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

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

// Same one-click publish as events. `active` is a full-row replace, so spread
// the row and flip the one field — `amount` and `users` are added by the API
// for display and are dropped by the model on the way back in.
async function toggleSponsorActive(sp) {
  await api.put(`/admin/sponsors/${sp.id}`, { ...sp, active: !sp.active })
  await load()
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
    const res = await api.post('/admin/admins', {
      email: newAdminEmail.value.trim(),
      role: newRole.value,
    })
    newAdminEmail.value = ''
    // An account that has never signed in may be a typo, so say whose it is.
    flash(
      res.neverSignedIn
        ? `${res.role} access granted to ${res.email}. That account has never signed in — check the address is right; they sign in with the usual emailed code.`
        : `${res.role} access granted to ${res.email}.`,
    )
    await load()
  } catch (e) {
    error.value = e.message
  }
}

/** Changing a role is the same call as granting one — the server upserts. */
async function setRole(a, role) {
  if (role === a.role) return
  try {
    await api.post('/admin/admins', { email: a.email, role })
    flash(`${a.email} is now ${role}.`)
    await load()
  } catch (e) {
    error.value = e.message
    await load()
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

/** Sign in as somebody else. A full reload: every panel on screen was built
 *  for the superuser, and the next page must be rendered as the other person
 *  from the ground up. */
async function impersonate() {
  const email = impersonateEmail.value.trim()
  if (!email) return
  if (!confirm(
    `Sign in as ${email}?\n\nIf there is no account for that address, one is `
    + 'made. You will be them until you press Stop in the red banner. Anything '
    + 'you do is recorded as them.',
  )) return
  busy.value = true
  try {
    await api.post('/admin/impersonate', { email })
    window.location.assign('/')
  } catch (e) {
    error.value = e.message
    busy.value = false
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

/** Every sellable Union product-line, for the per-ticket-type picker.
 *
 * Each ticket type is its own product in eActivities, so the choice here is a
 * product, not a line within the event's product. The line is still carried in
 * the key for the rarer case of one product with several lines.
 */
const ticketProductOptions = computed(() =>
  products.value.map((p) => ({
    key: `${p.product_id}:${p.product_line_id ?? ''}`,
    label:
      p.line_name && p.line_name !== p.product_name
        ? `${p.product_name} — ${p.line_name}`
        : p.product_name,
  })),
)

function productNameFor(ticketTypeId) {
  const link = linkFor(ticketTypeId)
  if (!link) return ''
  const row = products.value.find(
    (p) =>
      p.product_id === link.union_product_id &&
      (link.union_product_line_id === null ||
        p.product_line_id === link.union_product_line_id),
  )
  return row ? row.line_name || row.product_name : `product #${link.union_product_id}`
}

const eventProduct = ref(null)

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

async function saveTicketProduct(t) {
  busy.value = true
  error.value = ''
  try {
    // The select's value is "productId:lineId"; the line half is empty when
    // the product has only one line, which is the usual case now that each
    // ticket type is its own product.
    const [productId, lineId] = String(ticketProducts[t.id] || '').split(':')
    await api.put(`/admin/ticket-types/${t.id}/product`, {
      union_product_id: productId ? Number(productId) : null,
      union_product_line_id: lineId ? Number(lineId) : null,
    })
    flash(productId ? `${t.name} linked to its product.` : `${t.name} unlinked.`)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const ticketProducts = reactive({})

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




const warnTone = { error: 'danger', warn: 'accent', info: 'neutral' }
</script>

<template>
  <main class="admin">
    <div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-6); flex-wrap:wrap">
      <h1 style="margin:0; font:var(--type-h1)">Committee admin</h1>
      <div style="display:flex; gap:var(--space-4); align-items:center">
        <!-- Every committee member can work the door, role or not. -->
        <Button v-if="hasAdminArea" size="sm" variant="secondary" @click="router.push('/checkin')">Door check-in</Button>
        <span v-if="dash" style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
          {{ dash.academicYear }} · {{ dash.environment }}
        </span>
      </div>
    </div>

    <p v-if="!hasAdminArea" style="font:var(--type-small); color:var(--danger)">
      You need committee access for this page.
    </p>

    <template v-else>
      <nav class="admin-tabs">
        <Button
          v-for="[id, label] in visibleTabs"
          :key="id"
          :variant="tab === id ? 'primary' : 'ghost'"
          size="sm"
          @click="tab = id"
        >{{ label }}</Button>
      </nav>

      <p v-if="notice" style="font:var(--type-small); color:var(--text-muted)">{{ notice }}</p>
      <p v-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>

      <!-- ============================ OVERVIEW ============================ -->
      <!-- ============================= MY CARD ============================ -->
      <template v-if="tab === 'card'">
        <p style="margin:0 0 var(--space-5); font:var(--type-small); color:var(--text-muted); max-width:72ch">
          Your entry on the committee page. The name and the Union role come
          from ICU's records and are not editable here — a change made on this
          page would be reverted by the next roster sync anyway. Where you sit
          on the grid is set by an admin, so the rows stay even.
        </p>

        <Card v-if="myCard" variant="offset" style="max-width:62ch">
          <div style="display:grid; grid-template-columns:112px minmax(0,1fr); gap:var(--space-5); align-items:start">
            <div>
              <img
                v-if="myCard.photoUrl"
                :src="myCard.photoUrl"
                :alt="myCard.name"
                style="width:112px; height:112px; object-fit:cover; border-radius:var(--radius-sm)"
              />
              <div v-else style="width:112px; height:112px; background:var(--surface-sunken); border-radius:var(--radius-sm)" />
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                :disabled="busy"
                style="margin-top:var(--space-2); font-size:10px; width:112px"
                @change="uploadMyPhoto"
              />
            </div>

            <div style="display:grid; gap:var(--space-4); min-width:0">
              <div>
                <strong style="font:var(--type-h4)">{{ myCard.name }}</strong>
                <div style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
                  {{ myCard.displayRole || myCard.unionRole }} · {{ myCard.login }}
                </div>
              </div>

              <Field label="Intro" help="A sentence or two. This is what shows under your photo.">
                <textarea v-model="myCard.intro" rows="4" class="ees-textarea" />
              </Field>

              <div>
                <Button :disabled="busy" @click="saveMyCard">
                  {{ busy ? 'Saving…' : 'Save my card' }}
                </Button>
              </div>
            </div>
          </div>
        </Card>
      </template>

      <template v-if="tab === 'overview' && dash">
        <!-- Sponsors waiting on the committee. First on the page, because it is
             the one thing here somebody outside the committee is waiting on. -->
        <Card v-if="sponsorPendingTotal" variant="raised" class="pending-card">
          <div style="display:grid; gap:var(--space-4)">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:var(--space-4); flex-wrap:wrap">
              <h2 style="margin:0; font:var(--type-h4); display:flex; gap:var(--space-3); align-items:center">
                Awaiting the committee
                <Badge tone="danger">{{ sponsorPendingTotal }}</Badge>
              </h2>
              <Button @click="tab = 'sponsors'">Open Sponsors</Button>
            </div>
            <div v-for="g in sponsorPending" :key="g.key" style="display:grid; gap:var(--space-1, 4px)">
              <strong style="font:var(--type-small)">{{ g.label }}</strong>
              <template v-for="(it, i) in g.items.slice(0, 3)" :key="i">
                <RouterLink v-if="it.to" :to="it.to" class="pending-item">{{ it.text }}</RouterLink>
                <span v-else class="pending-item">{{ it.text }}</span>
              </template>
              <span v-if="g.items.length > 3" class="pending-item" style="color:var(--text-faint)">
                and {{ g.items.length - 3 }} more
              </span>
            </div>
          </div>
        </Card>

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
                    <Badge v-if="e.industry" tone="accent">Industry</Badge>
                    <Badge v-if="e.sponsorship_claimable" tone="brand">Sponsorable</Badge>
                    <Badge v-if="e.sponsors?.length" tone="accent">With {{ e.sponsors.join(', ') }}</Badge>
                  </div>
                  <p style="margin:var(--space-2) 0 0; font:var(--type-2xs); color:var(--text-faint)">
                    {{ new Date(e.starts_at).toLocaleString() }} ·
                    {{ e.taken }}/{{ e.capacity ?? '∞' }} booked ·
                    {{ (e.ticket_types || []).map((t) => t.name).join(', ') || 'no ticket types' }}
                  </p>
                </div>
                <div v-if="e.can_edit === false" style="font:var(--type-2xs); color:var(--text-faint); text-align:right; max-width:28ch">
                  Not an industry event — the events officers look after this one.
                </div>
                <div v-else style="display:flex; gap:var(--space-2)">
                  <Button size="sm" variant="ghost" @click="togglePublished(e)">{{ e.published ? 'Unpublish' : 'Publish' }}</Button>
                  <Button size="sm" variant="secondary" @click="router.push(`/admin/events/${e.id}`)">Details</Button>
                  <Button size="sm" variant="ghost" @click="editEvent(e)">Edit</Button>
                  <Button v-if="can('site')" size="sm" variant="secondary" @click="announceEvent(e)">Email about this</Button>
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
              <!-- An explicit choice, not "leave the date blank". Clearing a
                   datetime-local is fiddly in every browser and near-impossible
                   in Safari, which is why the date kept coming back as now. -->
              <fieldset
                v-if="draft.ticket_types.length"
                style="margin:0; padding:0; border:0; display:grid; gap:var(--space-2)"
              >
                <legend style="padding:0; font:var(--type-label); color:var(--text-muted)">Ticket sales</legend>
                <label style="font:var(--type-small); color:var(--text-body); display:flex; gap:var(--space-2); align-items:center">
                  <input v-model="draft.sales_scheduled" type="radio" :value="false" />
                  Not announced yet — show “On sale soon”
                </label>
                <label style="font:var(--type-small); color:var(--text-body); display:flex; gap:var(--space-2); align-items:center">
                  <input v-model="draft.sales_scheduled" type="radio" :value="true" />
                  On sale from a set date
                </label>
                <Input
                  v-if="draft.sales_scheduled"
                  v-model="draft.sales_open_at"
                  type="datetime-local"
                />
                <p
                  v-if="draft.sales_scheduled && !draft.sales_open_at"
                  style="margin:0; font:var(--type-2xs); color:var(--danger)"
                >
                  Pick a date, or choose “Not announced yet”.
                </p>
                <p v-else style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                  {{
                    draft.sales_scheduled
                      ? 'Bookings open automatically at that time.'
                      : 'The event is listed, but cannot be booked until you set a date.'
                  }}
                </p>
              </fieldset>

              <Field
                label="External sign-up form (optional)"
                help="For events that take sign-ups on their own form, e.g. a sponsor's. The event page shows a Sign up button linking here instead of booking."
              >
                <Input v-model="draft.signup_url" type="url" placeholder="https://forms.office.com/…" />
              </Field>

              <Field
                label="Where to buy (Union shop link)"
                help="Shown on the booking and in every email about an unpaid ticket. Blank uses the linked Union product's shop page (worked out from its id when the Union's feed has no URL)."
              >
                <Input v-model="draft.purchase_url" placeholder="https://www-s.imperialcollegeunion.org/shop/product/…" />
              </Field>

              <TraceRule />
              <h3 style="margin:0; font:var(--type-h5)">Union product (optional)</h3>
              <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                Only needed if every ticket type shares one product. Normally each
                ticket type is its own product — set that on the type itself below,
                which takes precedence over anything chosen here.
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

                <Field
                  v-if="t.requires_purchase"
                  label="Where to buy this one"
                  help="Overrides the event's link. Only needed when each ticket type is a separate shop page."
                >
                  <Input v-model="t.purchase_url" placeholder="Leave blank to use the event's link" />
                </Field>

                <!-- Each ticket type is sold as its own Union product, so the
                     product is chosen here rather than once for the event. It is
                     also what stops a "with alcohol" purchase confirming a
                     "no alcohol" seat. -->
                <template v-if="t.id && t.requires_purchase">
                  <div style="display:flex; gap:var(--space-2); align-items:flex-end; flex-wrap:wrap">
                    <Field label="Union product" style="flex:1 1 260px">
                      <select v-model="ticketProducts[t.id]" class="ees-select">
                        <option value="">Not linked — use the event's product</option>
                        <option v-for="o in ticketProductOptions" :key="`${t.id}-${o.key}`" :value="o.key">
                          {{ o.label }}
                        </option>
                      </select>
                    </Field>
                    <Button size="sm" :disabled="busy" @click="saveTicketProduct(t)">Link</Button>
                  </div>
                  <p v-if="linkFor(t.id)" style="margin:0; font:var(--type-2xs); color:var(--text-muted)">
                    Linked to {{ productNameFor(t.id) }} — only purchases of that confirm this ticket.
                  </p>
                </template>
                <p
                  v-else-if="!t.id && t.requires_purchase"
                  style="margin:0; font:var(--type-2xs); color:var(--text-faint)"
                >
                  Save the event first, then link this ticket type to its product.
                </p>
                <p
                  v-else-if="t.id && t.requires_purchase"
                  style="margin:0; font:var(--type-2xs); color:var(--danger)"
                >
                  Link a Union product to this event first (above) — no purchase can
                  confirm this ticket until you do.
                </p>
                <Button
                  size="sm"
                  variant="ghost"
                  @click="draft.ticket_types.splice(i, 1)"
                >Remove this ticket type</Button>
              </div>
              <p
                v-if="!draft.ticket_types.length"
                style="margin:0; font:var(--type-small); color:var(--text-muted)"
              >
                No ticket types — this is a drop-in event. The page will say
                “No booking needed” and nobody has to reserve a place.
              </p>
              <Button size="sm" variant="ghost" @click="draft.ticket_types.push({ name: '', price_pence: 0, capacity: null, max_per_user: 1, members_only: false, requires_purchase: true, purchase_url: '' })">
                Add ticket type
              </Button>

              <label style="font:var(--type-small)"><input v-model="draft.published" type="checkbox" /> Published</label>
              <label style="font:var(--type-small)">
                <input
                  v-model="draft.industry"
                  type="checkbox"
                  :disabled="!can('events')"
                />
                Industry event
              </label>
              <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                <template v-if="can('events')">
                  A careers fair, a sponsor's talk, a site visit. Ticking this
                  hands it to the industry officers as well as the events team.
                </template>
                <template v-else>
                  Your role covers industry events, so anything you create here
                  is one.
                </template>
              </p>
              <label style="font:var(--type-small)">
                <input v-model="draft.sponsorship_claimable" type="checkbox" />
                Open to sponsorship
              </label>
              <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                Off by default. Only events ticked here appear in sponsors' event
                calendars for them to register interest. Link the sponsor who takes
                it from their page under Sponsors, which takes it off everyone
                else's list.
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
                  <Field
                    label="Linked email"
                    help="Only needed when the account they sign in with is not their college address — then this is what lets them edit their own card."
                  >
                    <Input v-model="m.linkedEmail" :placeholder="m.email || 'none'" />
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
      <!-- ============================== EMAIL ============================= -->
      <template v-if="tab === 'email'">
        <!-- Mail is the one thing on this page that fails silently: a mailbox
             that cannot authenticate produces a campaign where every message
             fails, hours after somebody pressed send. So the state of both
             mailboxes is the first thing on the screen. -->
        <div v-if="mailStatus" style="display:grid; gap:var(--space-3); margin-bottom:var(--space-6)">
          <Card v-for="(m, key) in mailStatus" :key="key" variant="outline">
            <div style="display:flex; align-items:center; gap:var(--space-3); flex-wrap:wrap">
              <Badge :tone="m.configured ? 'success' : 'danger'">
                {{ m.configured ? 'ready' : 'not configured' }}
              </Badge>
              <span style="font:var(--type-small)">
                <strong>{{ key === 'publicity' ? 'Newsletters' : 'Transactional' }}</strong>
                — from {{ m.from }}
              </span>
              <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
                login {{ m.login || 'none' }}
              </span>
              <Badge v-if="m.sendsAs" tone="accent">sends as</Badge>
            </div>
            <p v-if="m.sendsAs" style="margin:var(--space-2) 0 0; font:var(--type-2xs); color:var(--text-muted)">
              Logged in as one mailbox, sending from another. Exchange only allows
              this with Send As granted on {{ m.from }} — if these bounce, that is
              the first thing to check.
            </p>
          </Card>
        </div>

        <div class="admin-cols admin-cols--split">
          <Card variant="offset">
            <h2 style="margin:0 0 var(--space-2); font:var(--type-h4)">Write a newsletter</h2>
            <p style="margin:0 0 var(--space-5); font:var(--type-small); color:var(--text-muted)">
              Goes from the publicity address. Everyone gets a working
              unsubscribe except the department lists, which the department owns.
            </p>

            <div style="display:grid; gap:var(--space-4)">
              <Field label="Subject" help="What lands in the inbox. Say the thing, don't tease it.">
                <Input v-model="compose.subject" placeholder="Careers Fair, lockers and the hack" />
              </Field>
              <Field label="Heading" help="The big line inside the email. Blank uses the subject.">
                <Input v-model="compose.heading" placeholder="" />
              </Field>
              <Field
                label="Body"
                help="Blank line between paragraphs. **bold**, - bullets, ## heading, [link](https://…)."
              >
                <textarea v-model="compose.body" rows="12" class="ees-textarea" />
              </Field>
            </div>

            <TraceRule style="margin:var(--space-6) 0" />
            <h3 style="margin:0 0 var(--space-4); font:var(--type-h5)">Who gets it</h3>

            <div v-for="[group, items] in audienceGroups" :key="group" style="margin-bottom:var(--space-5)">
              <span style="font:var(--type-label); color:var(--text-muted)">{{ group }}</span>
              <label v-for="a in items" :key="a.key" class="aud">
                <input
                  type="checkbox"
                  :checked="compose.audiences.includes(a.key)"
                  @change="toggleAudience(a.key)"
                />
                <span>
                  <strong class="aud__label">{{ a.label }}</strong>
                  <span class="aud__desc">{{ a.description }}</span>
                </span>
                <span class="aud__count">
                  {{ a.list ? 'list' : (a.count ?? '—') }}
                </span>
              </label>
            </div>

            <!-- Edit the Test list in place: it is what you tick to try a send. -->
            <div class="test-list">
              <span style="font:var(--type-label); color:var(--text-muted)">Test list</span>
              <div v-for="addr in testRecipients" :key="addr" class="test-list__row">
                <span style="font-family:var(--font-mono); font-size:var(--text-xs)">{{ addr }}</span>
                <Button size="sm" variant="ghost" :disabled="busy" @click="changeTestList(addr)">Remove</Button>
              </div>
              <span v-if="!testRecipients.length" style="font:var(--type-small); color:var(--text-faint)">Nobody yet.</span>
              <form class="test-list__add" @submit.prevent="changeTestList('add')">
                <Input v-model="newTestEmail" type="email" placeholder="name@ic.ac.uk" aria-label="Add to test list" />
                <Button type="submit" size="sm" variant="secondary" :disabled="busy || !newTestEmail.includes('@')">Add</Button>
              </form>
            </div>

            <div style="display:flex; gap:var(--space-3); flex-wrap:wrap; align-items:center">
              <Button variant="secondary" :disabled="busy || !compose.audiences.length" @click="preview">
                Preview
              </Button>
              <Button :disabled="busy || previewCount === null || !compose.subject.trim()" @click="sendEmail">
                {{ busy ? 'Working…' : 'Send' }}
              </Button>
              <span v-if="previewCount !== null" style="font:var(--type-small); color:var(--text-muted)">
                {{ previewCount }} recipient(s) after removing duplicates and unsubscribes
              </span>
            </div>
            <p v-if="previewSample.length" style="margin:var(--space-3) 0 0; font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
              e.g. {{ previewSample.join(', ') }}…
            </p>
          </Card>

          <div style="display:grid; gap:var(--space-6); align-content:start">
            <Card v-if="previewHtml" variant="outline">
              <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Preview</h2>
              <!-- Rendered by the same code that builds the real message, so
                   what is approved here is what arrives. The HTML is produced
                   server-side from an escape-first renderer — nothing a
                   committee member types reaches this as markup. -->
              <div class="mail-preview" v-html="previewHtml" />
            </Card>

            <Card variant="outline">
              <div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-3); flex-wrap:wrap">
                <h2 style="margin:0; font:var(--type-h4)">Sent</h2>
                <Button size="sm" variant="ghost" :disabled="busy" @click="runAnnouncePass">
                  Run on-sale &amp; reminder pass
                </Button>
              </div>
              <p v-if="!campaigns.length" style="margin:var(--space-4) 0 0; font:var(--type-small); color:var(--text-faint)">
                Nothing sent yet.
              </p>
              <div
                v-for="c in campaigns"
                :key="c.id"
                style="display:grid; gap:var(--space-2); padding:var(--space-3) 0; border-top:1px solid var(--border-faint)"
              >
                <div style="display:flex; align-items:center; justify-content:space-between; gap:var(--space-3)">
                  <span style="font:var(--type-small)">{{ c.subject }}</span>
                  <Badge :tone="campaignTone[c.status] || 'neutral'">{{ c.status }}</Badge>
                </div>
                <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
                  {{ c.sent }}/{{ c.recipients }} sent<template v-if="c.failed"> · {{ c.failed }} failed</template>
                  · {{ new Date(c.created_at).toLocaleString() }}
                </span>
                <span v-if="c.error" style="font:var(--type-2xs); color:var(--danger)">{{ c.error }}</span>
              </div>
            </Card>
          </div>
        </div>
      </template>

      <template v-if="tab === 'people'">
        <div class="admin-cols">
          <Card variant="offset">
            <h2 style="margin:0 0 var(--space-2); font:var(--type-h4)">Committee access</h2>
            <p style="margin:0 0 var(--space-4); font:var(--type-small); color:var(--text-muted)">
              <template v-if="canManageUsers">
                One role each. Everyone also edits their own committee card,
                whatever role they hold.
              </template>
              <template v-else>
                Who has access, and who to ask. Only a superuser can change it.
              </template>
            </p>

            <div
              v-for="a in admins"
              :key="a.email"
              style="display:flex; justify-content:space-between; align-items:center; gap:var(--space-3); padding:var(--space-3) 0; border-top:1px solid var(--border-hairline)"
            >
              <div style="display:grid; gap:2px; min-width:0">
                <span style="font:var(--type-small)">
                  {{ [a.first_name, a.last_name].filter(Boolean).join(' ') || a.email }}
                  <Badge v-if="a.standing" tone="accent">standing</Badge>
                </span>
                <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint); overflow-wrap:anywhere">
                  {{ a.email }}
                </span>
              </div>

              <div style="display:flex; gap:var(--space-2); align-items:center; flex-shrink:0">
                <select
                  v-if="canManageUsers && !a.standing && a.user_id"
                  :value="a.role"
                  class="ees-select"
                  @change="(e) => setRole(a, e.target.value)"
                >
                  <option v-for="r in roleOptions" :key="r.id" :value="r.id">{{ r.label }}</option>
                </select>
                <Badge v-else tone="neutral">{{ a.role }}</Badge>

                <Button
                  v-if="canManageUsers && !a.standing && a.user_id"
                  size="sm"
                  variant="ghost"
                  @click="removeAdmin(a)"
                >Remove</Button>
              </div>
            </div>

            <template v-if="canManageUsers">
              <TraceRule style="margin:var(--space-5) 0" />
              <Field label="Give somebody access" help="Any address. If there is no account yet, one is made; it works once they sign in with the emailed code.">
                <Input v-model="newAdminEmail" placeholder="name@imperial.ac.uk" />
              </Field>
              <Field label="As" style="margin-top:var(--space-3)">
                <select v-model="newRole" class="ees-select">
                  <option v-for="r in roleOptions" :key="r.id" :value="r.id">{{ r.label }}</option>
                </select>
              </Field>
              <p v-if="roleOptions.length" style="margin:var(--space-2) 0 0; font:var(--type-2xs); color:var(--text-faint)">
                {{ roleOptions.find((r) => r.id === newRole)?.blurb }}
              </p>
              <Button size="sm" style="margin-top:var(--space-3)" @click="addAdmin">Grant access</Button>
            </template>
          </Card>

          <Card v-if="canManageUsers" variant="outline">
            <h2 style="margin:0 0 var(--space-2); font:var(--type-h4)">Sign in as somebody</h2>
            <p style="margin:0 0 var(--space-4); font:var(--type-small); color:var(--text-muted)">
              Opens a real session as that account, so you see exactly what they
              see. A red banner stays on every page until you stop, and both
              names go in the log.
            </p>
            <Field label="Email" help="Any account on the site — member, guest or sponsor.">
              <Input v-model="impersonateEmail" placeholder="name@imperial.ac.uk" />
            </Field>
            <Button
              size="sm"
              variant="ghost"
              style="margin-top:var(--space-3)"
              :disabled="busy || !impersonateEmail.trim()"
              @click="impersonate"
            >Sign in as them</Button>
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

        <Card v-if="sponsorInbox" variant="offset" style="margin-bottom:var(--space-6)">
          <div style="display:grid; gap:var(--space-4)">
            <h2 style="margin:0; font:var(--type-h4)">Waiting on the committee</h2>
            <p
              v-if="!sponsorInbox.notes.length && !sponsorInbox.marketing.length && !sponsorInbox.requests.length && !sponsorInbox.interest.length && !rolesDigest?.count"
              style="margin:0; font:var(--type-small); color:var(--text-faint)"
            >Nothing — every sponsor has an answer.</p>
            <div v-if="sponsorInbox.notes.length" class="inbox-group">
              <span class="inbox-label">Questions and notes from sponsors ({{ sponsorInbox.notes.length }})</span>
              <RouterLink v-for="n in sponsorInbox.notes" :key="`n${n.sponsorId}-${n.topic}`" :to="`/admin/sponsors/${n.sponsorId}#notes`" class="inbox-row">
                <strong>{{ n.sponsor }}</strong> · {{ n.label }}<template v-if="n.count > 1"> ({{ n.count }})</template> — “{{ n.latest }}”
              </RouterLink>
            </div>
            <div v-if="sponsorInbox.marketing.length" class="inbox-group">
              <span class="inbox-label">Marketing to share ({{ sponsorInbox.marketing.length }})</span>
              <RouterLink v-for="m in sponsorInbox.marketing" :key="`m${m.id}`" :to="`/admin/sponsors/${m.sponsorId}#marketing`" class="inbox-row">
                <strong>{{ m.sponsor }}</strong> · {{ m.subject }}
              </RouterLink>
            </div>
            <div v-if="sponsorInbox.requests.length" class="inbox-group">
              <span class="inbox-label">Event requests ({{ sponsorInbox.requests.length }})</span>
              <RouterLink v-for="r in sponsorInbox.requests" :key="`r${r.id}`" :to="`/admin/sponsors/${r.sponsorId}#requests`" class="inbox-row">
                <strong>{{ r.sponsor }}</strong> · {{ r.format }} · {{ r.status === 'open' ? 'not answered' : 'in discussion' }}
              </RouterLink>
            </div>
            <div v-if="sponsorInbox.interest.length" class="inbox-group">
              <span class="inbox-label">Interest in events ({{ sponsorInbox.interest.length }})</span>
              <RouterLink v-for="x in sponsorInbox.interest" :key="`i${x.id}`" :to="`/admin/sponsors/${x.sponsorId}#events`" class="inbox-row">
                <strong>{{ x.sponsor }}</strong> · {{ x.event }} · {{ x.date }}
              </RouterLink>
            </div>
            <div v-if="rolesDigest?.count" class="inbox-group">
              <span class="inbox-label">New roles members haven't heard about ({{ rolesDigest.count }})</span>
              <span v-for="l in sponsorInbox.listings" :key="`l${l.id}`" class="inbox-row">
                <strong>{{ l.sponsor }}</strong> · {{ l.title }}<template v-if="l.location"> · {{ l.location }}</template>
              </span>
              <div style="display:flex; gap:var(--space-3); align-items:center; flex-wrap:wrap">
                <Button size="sm" :disabled="busy || !can('site')" @click="sendRolesDigest">
                  Email {{ rolesDigest.recipients }} jobs subscriber(s)
                </Button>
                <span style="font:var(--type-2xs); color:var(--text-faint)">
                  <template v-if="can('site')">One email listing every new role, to members who opted in to jobs and internships.</template>
                  <template v-else>Sending needs a committee member with email access.</template>
                </span>
              </div>
            </div>
          </div>
        </Card>

        <div class="admin-cols admin-cols--wide">
          <div style="display:grid; gap:var(--space-3)">
            <Card v-for="(sp, i) in sponsors" :key="sp.id" variant="outline">
              <div style="display:flex; justify-content:space-between; gap:var(--space-3); align-items:flex-start; flex-wrap:wrap">
                <div style="display:grid; gap:2px; min-width:0">
                  <span style="font:var(--type-h5)">
                    {{ sp.name }}
                    <Badge :tone="sp.active ? 'success' : 'neutral'">{{ sp.active ? 'Live' : 'Draft' }}</Badge>
                  </span>
                  <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
                    {{ sp.sector || 'No sector' }} · {{ sp.amount }}
                    <template v-if="sp.agreement_year"> · {{ sp.agreement_year }}</template>
                  </span>
                  <span v-if="sp.internal_note" style="font:var(--type-2xs); color:var(--text-muted)">
                    {{ sp.internal_note }}
                  </span>
                </div>
                <div style="display:flex; gap:4px; flex-wrap:wrap">
                  <Button size="sm" variant="ghost" @click="moveSponsor(sp, -1)">↑</Button>
                  <Button size="sm" variant="ghost" @click="moveSponsor(sp, 1)">↓</Button>
                  <Button size="sm" variant="ghost" @click="toggleSponsorActive(sp)">{{ sp.active ? 'Unpublish' : 'Publish' }}</Button>
                  <Button size="sm" variant="secondary" @click="router.push(`/admin/sponsors/${sp.id}`)">Agreement &amp; activity</Button>
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
                  They sign in with that address using the usual emailed code, which is
                  what proves they control it.
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
              <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                Contact points (CV addresses, careers pages) are edited in the sponsor's
                portal under Contacts — open it from Agreement &amp; activity.
              </p>

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

              <label style="font:var(--type-small)"><input v-model="sponsorDraft.active" type="checkbox" /> Published</label>
              <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                Draft sponsors stay on this page only. Publish once the agreement is signed.
              </p>
              <div style="display:flex; gap:var(--space-3)">
                <Button :disabled="busy" @click="saveSponsor">{{ editingSponsor ? 'Save' : 'Add sponsor' }}</Button>
                <Button variant="ghost" @click="resetSponsor">Clear</Button>
              </div>
            </div>
          </Card>
        </div>
      </template>

    </template>
    <!-- Opened from the Events tab. Same audiences, same preview, same send
         path — the only difference is that the event's date, venue, tickets
         and booking link are rendered into the message from the database, so a
         reminder cannot advertise last week's room. -->
    <Dialog
      :open="Boolean(announcing)"
      :title="announcing ? `Email people about ${announcing.title}` : ''"
      width="720px"
      @close="closeCompose"
    >
      <div v-if="announcing" style="display:grid; gap:var(--space-4)">
        <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
          Write what you want to say. The date, venue, ticket prices and a link
          to the event page are added underneath automatically.
        </p>

        <Field label="Subject"><Input v-model="compose.subject" /></Field>
        <Field label="Heading" help="Blank uses the subject.">
          <Input v-model="compose.heading" />
        </Field>
        <Field label="Message" help="**bold**, - bullets, ## heading, [link](https://…).">
          <textarea v-model="compose.body" rows="7" class="ees-textarea" />
        </Field>

        <TraceRule />
        <span style="font:var(--type-label); color:var(--text-muted)">Who gets it</span>
        <div v-for="[group, items] in composeGroups" :key="group">
          <span style="font:var(--type-2xs); color:var(--text-faint)">{{ group }}</span>
          <label v-for="a in items" :key="a.key" class="aud">
            <input
              type="checkbox"
              :checked="compose.audiences.includes(a.key)"
              @change="toggleAudience(a.key)"
            />
            <span>
              <strong class="aud__label">{{ a.label }}</strong>
              <span class="aud__desc">{{ a.description }}</span>
            </span>
            <span class="aud__count">{{ a.list ? 'list' : (a.count ?? '—') }}</span>
          </label>
        </div>

        <div v-if="previewHtml" class="mail-preview" v-html="previewHtml" />
        <p v-if="error" style="margin:0; font:var(--type-small); color:var(--danger)">{{ error }}</p>
      </div>

      <template #footer>
        <span v-if="previewCount !== null" style="font:var(--type-small); color:var(--text-muted); margin-right:auto">
          {{ previewCount }} recipient(s)
        </span>
        <Button variant="ghost" @click="closeCompose">Cancel</Button>
        <Button variant="secondary" :disabled="busy || !compose.audiences.length" @click="preview">
          Preview
        </Button>
        <Button :disabled="busy || previewCount === null || !compose.subject.trim()" @click="sendEmail">
          {{ busy ? 'Sending…' : 'Send' }}
        </Button>
      </template>
    </Dialog>
  </main>
</template>

<style scoped>
.test-list {
  display: grid;
  gap: var(--space-2);
  margin-bottom: var(--space-5);
  padding: var(--space-4);
  border: var(--border-width-hairline) dashed var(--border-default);
  border-radius: var(--radius-sm);
}

.test-list__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
}

.test-list__add {
  display: flex;
  gap: var(--space-3);
  align-items: center;
}

/* One audience row: tickbox, name + what it is, and the live count. The count
   is right-aligned and tabular so a column of them can be scanned — the
   difference between 42 and 420 recipients is the whole decision. */
.aud {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: var(--space-3);
  align-items: start;
  padding: var(--space-2) 0;
  cursor: pointer;
}

.aud input {
  margin-top: 3px;
  width: 15px;
  height: 15px;
  accent-color: var(--action-primary);
}

.aud__label {
  display: block;
  font: var(--type-small);
  color: var(--text-strong);
}

.aud__desc {
  display: block;
  font: var(--type-2xs);
  color: var(--text-muted);
}

.aud__count {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* The preview is a fragment of an HTML email: it carries its own inline styles
   and must not inherit the admin page's. Contain it and let it scroll rather
   than letting a long newsletter stretch the panel. */
.mail-preview {
  max-height: 460px;
  overflow: auto;
  padding: var(--space-4);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  background: var(--white, #fff);
}

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
.inbox-group {
  display: grid;
  gap: var(--space-2);
}
.inbox-label {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: var(--text-muted);
}
.inbox-row {
  font: var(--type-small);
  color: var(--text-body);
}
.pending-card {
  margin-bottom: var(--space-8);
  border-left: 4px solid var(--danger);
}
.pending-item {
  font: var(--type-2xs);
  color: var(--text-body);
  padding-left: var(--space-3);
}
</style>
