<script setup>
// Committee admin.
//
// One page, several tabs: an overview that says what needs doing, then editors
// for events, resources, Union product links, files, people and sponsors.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePageTitle } from '@/utils/pageTitle'

import api from '@/api'
import Card from '@/components/ds/Card.vue'
import Button from '@/components/ds/Button.vue'
import Badge from '@/components/ds/Badge.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'
import StatBlock from '@/components/ds/StatBlock.vue'
import Dialog from '@/components/ds/Dialog.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import PasskeyLinkMade from '@/components/PasskeyLinkMade.vue'
import PermissionSelector from '@/components/PermissionSelector.vue'
import RoleManagerPanel from '@/components/RoleManagerPanel.vue'
import AuditActivityPanel from '@/components/AuditActivityPanel.vue'
import OpportunitiesAdminPanel from '@/components/OpportunitiesAdminPanel.vue'
import PhotoStudioPanel from '@/components/PhotoStudioPanel.vue'
import WhatsAppGroupPicker from '@/components/WhatsAppGroupPicker.vue'
import WhatsAppLink from '@/components/WhatsAppLink.vue'
import WhatsAppPost from '@/components/WhatsAppPost.vue'
import SocietiesAdminPanel from '@/components/SocietiesAdminPanel.vue'
import WhatsAppGroupAdmin from '@/components/WhatsAppGroupAdmin.vue'
import session, { can, anyPermission, hasAdminArea } from '@/stores/session'

const route = useRoute()
const router = useRouter()

// Each tab names the capability that makes it useful. 'card' is the one tab
// that needs no role at all — a committee member's own entry — so it is keyed
// on owning a card instead.
const TABS = [
  ['overview', 'Overview', 'any'],
  ['card', 'My card', 'card'],
  ['events', 'Events', 'eventview'],
  ['overdue', 'Review cancellations', 'bookingish'],
  ['resources', 'Resources', 'resources'],
  ['opportunities', 'Opportunities', 'opportunities.view'],
  ['photos', 'Photo prints', 'photoish'],
  ['files', 'Files', 'files.view'],
  ['committee', 'Committee', 'committee.view'],
  ['links', 'Union products', 'products.view'],
  ['lockers', 'Lockers', 'lockerish'],
  ['email', 'Email', 'emailish'],
  ['whatsapp', 'WhatsApp', 'whatsappish'],
  ['societies', 'Societies', 'societyish'],
  ['people', 'People', 'access.view'],
  ['roles', 'Roles', 'rolesish'],
  ['audit', 'Activity log', 'access.audit.view'],
  ['sponsors', 'Sponsors', 'sponsors.view'],
]

function allowed(need) {
  if (need === 'any') return session.isAdmin
  if (need === 'card') return Boolean(session.committeeLogin)
  // Events and the bookings that go with them: the events officers see all of
  // it, the industry officers see it for their own events.
  if (need === 'eventview') return can('events.view') || can('events.bookings.view') || can('events.create')
  if (need === 'bookingish') return can('events.bookings.view')
  if (need === 'rolesish') return can('access.roles.create') || can('access.roles.edit')
  if (need === 'lockerish') return can('lockers.inventory.view') || can('lockers.allowances.view') || can('lockers.holders.view')
  if (need === 'emailish') return anyPermission('email.')
  if (need === 'whatsappish') return anyPermission('whatsapp.') || anyPermission('societies.whatsapp.')
  if (need === 'societyish') return anyPermission('societies.')
  if (need === 'photoish') return anyPermission('photos.')
  return can(need)
}

const visibleTabs = computed(() => TABS.filter(([, , need]) => allowed(need)))

const tab = ref(route.query.tab || '')
watch(tab, (t) => { if (t !== route.query.tab) router.replace({ query: { ...route.query, tab: t } }) })
watch(() => route.query.tab, (t) => { if (t && t !== tab.value) tab.value = t })

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
// The API returns the complete (permission-scoped) list, but showing every
// event beside the editor makes the Events tab needlessly long on a phone.
// Sort without mutating the API array so other views can still find by id.
const EVENT_PAGE_SIZE = 3
const eventPage = ref(0)
const eventSearch = ref('')
const orderedEvents = computed(() => [...events.value].sort((a, b) => {
  const aTime = Date.parse(a.starts_at) || 0
  const bTime = Date.parse(b.starts_at) || 0
  return bTime - aTime || Number(b.id) - Number(a.id)
}))
const filteredEvents = computed(() => {
  const query = eventSearch.value.trim().toLowerCase()
  return query
    ? orderedEvents.value.filter(e => `${e.title} ${e.slug} ${e.category}`.toLowerCase().includes(query))
    : orderedEvents.value
})
const eventPageCount = computed(() => Math.max(1, Math.ceil(filteredEvents.value.length / EVENT_PAGE_SIZE)))
const activeEventPage = computed(() => Math.min(eventPage.value, eventPageCount.value - 1))
const pagedEvents = computed(() => filteredEvents.value.slice(
  activeEventPage.value * EVENT_PAGE_SIZE,
  (activeEventPage.value + 1) * EVENT_PAGE_SIZE,
))
watch(eventSearch, () => { eventPage.value = 0 })
watch(eventPageCount, count => {
  if (eventPage.value >= count) eventPage.value = count - 1
})
function changeEventPage(delta) {
  eventPage.value = Math.max(0, Math.min(eventPageCount.value - 1, activeEventPage.value + delta))
}
const products = ref([])
const eligibleSocieties = ref([])
onMounted(async () => {
  if (can('events.ticket_types.eligibility') || can('events.membership_eligibility') || can('societies.events.assign')) {
    try { eligibleSocieties.value = await api.get('/admin/societies/eligible-lookup') } catch {}
  }
})
const links = ref([])
const pages = ref([])
const pageDetail = ref(null)
const uploads = ref(null)
const overdue = ref(null)
const orderNumbers = reactive({})
const reviewOverrides = reactive({})
const admins = ref([])
const roleOptions = ref([])
const newRole = ref('admin')
const permissionOptions = ref([])
const newPermissions = ref([])
const editingAccessId = ref(null)
const editingAccessRole = ref('admin')
const editingPermissions = ref([])
let accessFormInitialized = false

function preset(role) {
  return [...(roleOptions.value.find((r) => r.id === role)?.capabilities ?? [])]
}
function isPreset(role, permissions) {
  const selected = new Set(permissions)
  const defaults = preset(role)
  return selected.size === defaults.length && defaults.every((p) => selected.has(p))
}
function permissionsPayload(role, selection) {
  return isPreset(role, selection) ? null : [...selection].sort()
}
function editAccess(a) {
  if (editingAccessId.value === a.user_id) {
    editingAccessId.value = null
    return
  }
  editingAccessId.value = a.user_id
  editingAccessRole.value = a.role
  editingPermissions.value = [...a.capabilities]
}
// Role defaults are mutable on the Roles tab. The People tab must fetch the
// persisted presets, rather than reusing the snapshot from its initial mount.
async function refreshAccessPresets() {
  if (!can('access.view')) return
  const data = await api.get('/admin/admins')
  roleOptions.value = data.roles || []
  permissionOptions.value = data.permissions || []
  admins.value = data.admins || []
  canManageUsers.value = Boolean(data.canManage)
}
async function resetEditPermissions() {
  const role = editingAccessRole.value
  try {
    await refreshAccessPresets()
    if (editingAccessRole.value === role) editingPermissions.value = preset(role)
  } catch (e) { error.value = e.message }
}
async function resetNewPermissions() {
  const role = newRole.value
  try {
    await refreshAccessPresets()
    if (newRole.value === role) newPermissions.value = preset(role)
  } catch (e) { error.value = e.message }
}
watch(newRole, resetNewPermissions)

const canManageUsers = ref(false)
const sponsors = ref([])
const sponsorTotals = ref(null)
// The standard sponsor package: one allowance per countable clause, for every
// sponsor not given their own on their agreement page.
const sponsorPackage = ref([])
const packageEdits = reactive({})
const committee = ref([])
// This person's own committee entry, for the "My card" tab.
const myCard = ref(null)
const impersonateEmail = ref('')

// ---------------------------------------------------------- passkey links ---
// For somebody our sign-in codes do not reach. The link is shown once, as made
// (only its hash is stored), keyed by the address it was made for so the
// Sponsors tab can show it under the right person.
const passkeyLinks = ref([])
const passkeyLinkTtlHours = ref(null)
const passkeyLinkEmail = ref('')
const madeLinks = reactive({})

// ------------------------------------------------------ locker allowances ---
// Free lockers on top of any bought, per academic year. Guarded by the
// independent `locker_allowances` permission.
const allowances = ref([])
const managedLockers = ref([])
const lockerHolders = ref([])
const allowanceYear = ref('')
const allowanceMax = ref(10)
const allowanceForm = reactive({ who: '', free: 1, note: '' })

// ------------------------------------------------------------------ email ---
const whatsappHeading = ref('')
const whatsappBody = ref('')
const whatsappRecipients = ref([])
async function sendWhatsAppPost() {
  if (!whatsappBody.value.trim() || !whatsappRecipients.value.length) return
  if (!confirm(`Post to ${whatsappRecipients.value.length} WhatsApp group(s)?`)) return
  error.value = ''
  busy.value = true
  try {
    const result = await api.post('/admin/email/send', {
      heading: whatsappHeading.value, body: whatsappBody.value,
      audiences: whatsappRecipients.value.map((id) => `whatsapp:${id}`),
    })
    flash(result.message || 'WhatsApp post queued.')
    whatsappHeading.value = ''
    whatsappBody.value = ''
    whatsappRecipients.value = []
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

const audiences = ref([])
const mailStatus = ref(null)
const campaigns = ref([])
const compose = reactive({ subject: '', heading: '', body: '', audiences: [] })
const previewHtml = ref('')
const previewCount = ref(null)
const previewSample = ref([])
// Non-null while the "announce this event" dialog is open; holds the event it
// is about, so the same composer serves both.
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
  // Lazy: never start a request for a feature this account cannot access.
  return allow ? request() : Promise.resolve(null)
}

async function load() {
  error.value = ''
  const eventish = can('events.view') || can('events.bookings.view') || can('events.create')
  try {
    const [d, ev, pr, ln, pg, ad, sp, cm, mine] = await Promise.all([
      maybe(session.isAdmin, () => api.get('/admin/dashboard')),
      maybe(eventish, () => api.get('/admin/events')),
      maybe(can('products.view'), () => api.get('/admin/union/products')),
      maybe(can('products.view'), () => api.get('/admin/product-links')),
      maybe(can('resources'), () => api.get('/resources')),
      maybe(can('access.view'), () => api.get('/admin/admins')),
      maybe(can('sponsors.view'), () => api.get('/admin/sponsors')),
      maybe(can('committee.view'), () => api.get('/admin/committee')),
      maybe(Boolean(session.committeeLogin), () => api.get('/committee/me')),
    ])
    dash.value = d
    events.value = ev ?? []
    products.value = pr ?? []
    links.value = ln ?? []
    pages.value = pg?.pages ?? []
    admins.value = ad?.admins ?? []
    roleOptions.value = ad?.roles ?? []
    permissionOptions.value = ad?.permissions ?? []
    if (!accessFormInitialized && roleOptions.value.length) {
      newPermissions.value = preset(newRole.value)
      accessFormInitialized = true
    }
    canManageUsers.value = Boolean(ad?.canManage)
    sponsors.value = sp?.sponsors ?? sp ?? []
    sponsorTotals.value = sp?.totals ?? null
    sponsorPackage.value = sp?.package ?? []
    for (const c of sponsorPackage.value) packageEdits[c.key] = c.allowance
    committee.value = cm?.committee ?? []
    myCard.value = mine?.card ?? null
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  await load()
  loadSponsorInbox()
  loadPasskeyLinks()
  loadAllowances()
  loadManagedLockers()
  // Arriving from an event's page with "Email everyone who hasn't bought".
  const id = Number(route.query.announce)
  if (id) {
    const e = events.value.find((x) => x.id === id)
    if (e) announceEvent(e, route.query.audience ? String(route.query.audience) : null)
    router.replace({ query: { tab: route.query.tab || 'events' } })
  }
  // Landing straight on the Email tab (a bookmark, or the link in the
  // "waiting for approval" notice). The tab watcher only loads it on a
  // *change* of tab, so without this the queue would read empty.
  if (anyPermission('email.') && tab.value === 'email') {
    await loadEmail().catch((e) => (error.value = e.message))
  }
  const review = Number(route.query.review)
  if (review && can('email.queue.view')) {
    tab.value = 'email'
    router.replace({ query: { tab: 'email' } })
    openReview(review)
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
  slug: '', title: '', photo_short_name: '', category: 'Social', description: '', image_url: '', venue: '',
  starts_at: '', ends_at: '', doors_close_at: '', capacity: null, published: false,
  membership_restricted: false, allow_eesoc_members: false, eligible_society_ids: [],
  eesoc_organised: true, organiser_society_ids: [],
  // Holds are whole days ending at midnight London time (section 17); the
  // form edits days and sends hold_hours = days × 24.
  hold_days: 2,
  // Not announced is the default for a new event: the site shows On sale
  // soon and refuses bookings until someone chooses a date.
  sales_scheduled: false,
  sales_open_at: '',
  // The end of the sale window, each optional and off by default. A tickbox
  // rather than "leave the time blank", for the same Safari reason as above.
  reservations_close_enabled: false,
  reservations_close_at: '',
  union_close_enabled: false,
  union_sales_close_at: '',
  // Ticket types on sale at different times — early bird, then general. Off
  // by default: every type then sells for the event's whole window.
  ticket_windows: false,
  // Where to buy, for the emails. Only needed when the Union's own product
  // feed carries no URL — which, for our centre, is most of the time.
  purchase_url: '',
  // A sign-up form of somebody else's — usually a sponsor's. When set, the
  // event page links there instead of offering our booking.
  signup_url: '',
  // A map of the venue and the online-meeting link. The meeting link can be
  // restricted to booked attendees: a sponsor's Zoom link is often only for
  // the people who actually booked, and the event page then withholds it from
  // everyone else.
  map_url: '',
  meeting_url: '',
  meeting_url_private: false,
  // Which year groups the event is aimed at; empty = everyone. Nothing sends
  // emails from this form yet — the year list is what the (future) targeted
  // announcements will read.
  target_years: [],
  sponsorship_claimable: false,
  // An industry event — careers fair, sponsor talk, site visit. It is what
  // decides whether the industry officers can edit it, so it is ticked
  // deliberately rather than guessed from the category. Somebody who may only
  // edit industry events gets it set for them, server-side.
  industry: !can('events.scope.all'),
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

// Prices are typed in pounds — "17.50" — and stored in pence. A pence field
// invited £17.50 typed as 17.5, which the API refuses as not a whole number.
// The form keeps the typed text in price_pounds and converts it on save.
function toPounds(pence) {
  const p = Number(pence) || 0
  return p % 100 === 0 ? String(p / 100) : (p / 100).toFixed(2)
}

function toPence(name, typed) {
  const text = String(typed ?? '').trim().replace(/^£/, '')
  if (text === '') return 0
  const pounds = Number(text)
  if (!Number.isFinite(pounds) || pounds < 0) {
    throw new Error(`The price for "${name.trim()}" should be in pounds, like 17.50.`)
  }
  return Math.round(pounds * 100)
}

function editEvent(e) {
  editingId.value = e.id
  Object.assign(draft, {
    ...blank(), ...e,
    starts_at: toLocalInput(e.starts_at),
    ends_at: toLocalInput(e.ends_at),
    doors_close_at: toLocalInput(e.doors_close_at),
    sales_scheduled: Boolean(e.sales_open_at),
    sales_open_at: toLocalInput(e.sales_open_at),
    hold_days: Math.max(1, Math.ceil((e.hold_hours ?? 48) / 24)),
    reservations_close_enabled: Boolean(e.reservations_close_at),
    reservations_close_at: toLocalInput(e.reservations_close_at),
    union_close_enabled: Boolean(e.union_sales_close_at),
    union_sales_close_at: toLocalInput(e.union_sales_close_at),
    signup_url: e.signup_url ?? '',
    map_url: e.map_url ?? '',
    meeting_url: e.meeting_url ?? '',
    meeting_url_private: Boolean(e.meeting_url_private),
    target_years: e.target_years ?? [],
    membership_restricted: Boolean(e.membership_restricted),
    allow_eesoc_members: Boolean(e.allow_eesoc_members),
    eligible_society_ids: [...(e.eligible_society_ids || [])],
    eesoc_organised: e.eesoc_organised !== false,
    organiser_society_ids: [...(e.organiser_society_ids || [])],
    ticket_types: (e.ticket_types ?? []).map((t) => ({
      ...t,
      price_pounds: toPounds(t.price_pence),
      sales_open_enabled: Boolean(t.sales_open_at),
      sales_open_at: toLocalInput(t.sales_open_at),
      sales_close_enabled: Boolean(t.sales_close_at),
      sales_close_at: toLocalInput(t.sales_close_at),
    })),
    ticket_windows: (e.ticket_types ?? []).some((t) => t.sales_open_at || t.sales_close_at),
  })
  // Prefill the product and per-ticket line selects from existing links.
  const ev = links.value.find((l) => l.event_id === e.id && l.ticket_type_id === null)
  eventProduct.value = ev?.union_product_id ?? null
  eventProductMode.value = ev && isManualOnlyProduct(ev) ? 'manual' : 'api'
  eventShopRef.value = ev && isManualOnlyProduct(ev) ? shortShopId(ev.union_product_id) : ''
  for (const t of draft.ticket_types) {
    const link = links.value.find((l) => l.ticket_type_id === t.id)
    // Retain existing linked IDs, even when the feed has never listed them.
    ticketProducts[t.id] = link
      ? `${link.union_product_id}:${link.union_product_line_id ?? ''}`
      : ''
    ticketProductModes[t.id] = link && isManualOnlyProduct(link) ? 'manual' : 'api'
    ticketShopRefs[t.id] = link && isManualOnlyProduct(link)
      ? shortShopId(link.union_product_id) : ''
  }
  tab.value = 'events'
}

function resetDraft() {
  Object.assign(draft, blank())
  editingId.value = null
}

async function savePhotoShortName() {
  if (!editingId.value || !can('events.photo_short_name.edit')) return
  busy.value = true
  error.value = ''
  try {
    const updated = await api.patch(
      '/admin/events/' + editingId.value + '/photo-short-name',
      { photo_short_name: (draft.photo_short_name || '').trim() || null },
    )
    draft.photo_short_name = updated.photo_short_name || ''
    flash('Event photo short name saved.')
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function saveEvent() {
  busy.value = true
  error.value = ''
  try {
    const { photo_short_name, ...standardEventFields } = draft
    const payload = {
      ...standardEventFields,
      capacity: draft.capacity || null,
      // datetime-local is the browser's own clock; send the instant in UTC.
      starts_at: new Date(draft.starts_at).toISOString(),
      ends_at: draft.ends_at ? new Date(draft.ends_at).toISOString() : null,
      // Explicit null when blank, so clearing the field clears the event's
      // doors-close time rather than leaving the old one in place.
      doors_close_at: draft.doors_close_at
        ? new Date(draft.doors_close_at).toISOString()
        : null,
      // The radio decides, not whether the field happens to be empty.
      sales_open_at:
        draft.sales_scheduled && draft.sales_open_at
          ? new Date(draft.sales_open_at).toISOString()
          : null,
      hold_hours: Math.max(1, Math.round(draft.hold_days) || 1) * 24,
      // Only while tickets are on sale from a date: closing a sale that never
      // opened means nothing, and a hidden value must not linger.
      reservations_close_at:
        draft.sales_scheduled && draft.reservations_close_enabled && draft.reservations_close_at
          ? new Date(draft.reservations_close_at).toISOString()
          : null,
      union_sales_close_at:
        draft.sales_scheduled && draft.union_close_enabled && draft.union_sales_close_at
          ? new Date(draft.union_sales_close_at).toISOString()
          : null,
      signup_url: draft.signup_url.trim() || null,
      map_url: draft.map_url.trim() || null,
      meeting_url: draft.meeting_url.trim() || null,
      meeting_url_private: Boolean(draft.meeting_url_private),
      // Sent as a list — the backend stores the canonical comma string and
      // returns a list, so an untouched save round-trips unchanged.
      target_years: draft.target_years,
      ticket_types: draft.ticket_types.filter((t) => t.name.trim())
        .map(({ price_pounds, sales_open_enabled, sales_close_enabled, ...t }) => ({
          ...t, price_pence: toPence(t.name, price_pounds), capacity: t.capacity || null,
          // A ticket type's own window only means something inside the
          // event's, and only while its box is ticked — a hidden value must
          // not linger, as with the event's own close times above.
          sales_open_at:
            draft.sales_scheduled && draft.ticket_windows && sales_open_enabled && t.sales_open_at
              ? new Date(t.sales_open_at).toISOString()
              : null,
          sales_close_at:
            draft.sales_scheduled && draft.ticket_windows && sales_close_enabled && t.sales_close_at
              ? new Date(t.sales_close_at).toISOString()
              : null,
        })),
    }
    const createdNew = !editingId.value
    if (editingId.value) await api.put(`/admin/events/${editingId.value}`, payload)
    else await api.post('/admin/events', payload)
    flash(editingId.value ? 'Event updated.' : 'Event created.')
    resetDraft()
    if (createdNew) { eventPage.value = 0; eventSearch.value = '' }
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function togglePublished(e) {
  await api.patch(`/admin/events/${e.id}/published`, { published: !e.published })
  await load()
}

async function saveEventAvailabilityBadge(e, badge) {
  busy.value = true
  error.value = ''
  try {
    await api.patch(`/admin/events/${e.id}/availability-badge`, { availability_badge: badge })
    flash(`What's On label updated for ${e.title}.`)
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function saveEventWaitlistEnabled(e, enabled) {
  busy.value = true
  error.value = ''
  try {
    await api.patch(`/admin/events/${e.id}/waitlist-enabled`, { waitlist_enabled: enabled })
    flash(enabled ? `Waitlist enabled for ${e.title}.` : `Waitlist disabled for ${e.title}.`)
    await load()
  } catch (err) {
    error.value = err.message
    await load()
  } finally {
    busy.value = false
  }
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
  const value = (orderNumbers[row.id] || '').trim()
  const override = can('events.bookings.confirm_override') && Boolean(reviewOverrides[row.id])
  if (!value && !override) {
    error.value = 'Provide a Union order number, or choose the separately permissioned payment override.'
    return
  }
  if (override && !confirm('Confirm without Union purchase evidence? This requires an explicit override.')) return
  busy.value = true
  error.value = ''
  try {
    const res = row.status === 'confirmed' && !override
      ? await api.patch(`/admin/reservations/${row.id}/order-number`, { order_number: value })
      : await api.post(`/admin/reservations/${row.id}/confirm`, { order_number: value || null, override })
    flash(res.message || 'Order submitted for verification.')
    reviewOverrides[row.id] = false
    orderNumbers[row.id] = ''
    await Promise.all([loadOverdue(), load()])
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
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
  if (t === 'people') refreshAccessPresets().catch(e => { error.value = e.message })
  if (t === 'files' && !uploads.value) loadUploads()
  if (t === 'overdue') loadOverdue()
  // Reloaded every visit rather than cached: a send in progress updates its
  // counts as it goes, and a stale "0 sent" is exactly what makes somebody
  // press send a second time.
  if (t === 'email') loadEmail()
})

async function openOpportunityEmail(id) {
  tab.value = 'email'
  await loadEmail()
  await openReview(id)
}

// --------------------------------------------------------------- email -----
async function loadEmail() {
  const [a, c, q] = await Promise.all([
    maybe(can('email.audiences'), () => api.get('/admin/email/audiences')),
    maybe(can('email.campaigns.view'), () => api.get('/admin/email/campaigns')),
    maybe(can('email.queue.view'), () => api.get('/admin/email/queue')),
  ])
  audiences.value = a?.audiences ?? []
  emailEnvironment.value = a?.environment ?? null
  mailStatus.value = a?.mail ?? null
  campaigns.value = c ?? []
  setQueue(q?.items ?? [])
  if (can('email.test_recipients.view')) loadTestList().catch(() => {})
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
    if (can('email.audiences')) audiences.value = (await api.get('/admin/email/audiences')).audiences
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
    if (a.whatsapp && !can('whatsapp.post')) continue
    if (!a.whatsapp && !can('email.send') && !can('email.preview')) continue
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

/** The audience list again, with its counts — after a WhatsApp group is
 *  enabled or disabled, so "Who gets it" shows it straight away. */
async function refreshAudiences() {
  try {
    const a = await api.get('/admin/email/audiences')
    audiences.value = a.audiences
    emailEnvironment.value = a.environment
  } catch (e) {
    error.value = e.message
  }
}

/** WhatsApp groups are audiences like any other — ticked in the same list,
 *  sent by the same Send. These name the ones ticked among some keys. */
function whatsappTicked(keys) {
  return audiences.value.filter((a) => a.whatsapp && keys.includes(a.key)).map((a) => a.label)
}

/** "42 recipient(s) and EESoc 26/27 on WhatsApp", for a send confirmation. */
function sendTargets(count, keys) {
  const groups = whatsappTicked(keys)
  const parts = []
  if (count) parts.push(`${count} recipient(s)`)
  if (groups.length) parts.push(`${groups.join(', ')} on WhatsApp`)
  return parts.join(' and ') || 'nobody'
}

// Which environment the audience list came from. Staging's WhatsApp is linked
// to the real number, so its groups are the real ones: Send says so first.
const emailEnvironment = ref(null)

function stagingNote(keys) {
  return emailEnvironment.value === 'staging' && whatsappTicked(keys).length
    ? '\n\nThis is staging, but WhatsApp is real: those groups get it for real.'
    : ''
}

/** The right-hand column of an audience row. */
function audCount(a) {
  return a.whatsapp ? 'WhatsApp' : a.list ? 'list' : (a.count ?? '—')
}

const composeWhatsApp = computed(() => whatsappTicked(compose.audiences))
// The ticked audiences that are email rather than WhatsApp.
const composeEmail = computed(() => compose.audiences.filter((k) => !k.startsWith('whatsapp:')))

/** Why Send is not on offer yet, or '' when it is. Shown beside it, so a
 *  greyed-out button never has to be guessed at. A subject belongs to the
 *  email: WhatsApp alone needs only something to post — and an event's
 *  details are that by themselves. */
const sendBlocked = computed(() => {
  if (previewCount.value === null) return 'Press Preview first.'
  if (composeEmail.value.length && !compose.subject.trim()) return 'Give the email a subject.'
  const words = compose.subject.trim() || compose.heading.trim() || compose.body.trim()
  if (!composeEmail.value.length && !announcing.value && !words) return 'Write something to post.'
  return ''
})

/** "42 recipient(s) …", "No email — posts into X on WhatsApp", or both. */
const audienceSummary = computed(() => {
  const wa = composeWhatsApp.value.length ? `${composeWhatsApp.value.join(', ')} on WhatsApp` : ''
  if (!composeEmail.value.length) return wa ? `No email — posts into ${wa}` : ''
  const mail = `${previewCount.value} email recipient(s) after removing duplicates and unsubscribes`
  return wa ? `${mail}, and posts into ${wa}` : mail
})
const draftWhatsApp = computed(() => whatsappTicked(mailDraft.audiences))

async function sendEmail() {
  const count = previewCount.value
  const what = announcing.value ? `about ${announcing.value.title}` : 'newsletter'
  if (count === null) {
    error.value = 'Press Preview first, so you can see who this goes to.'
    return
  }
  if (!confirm(`Send this ${what} to ${sendTargets(count, compose.audiences)}? This cannot be undone.${stagingNote(compose.audiences)}`)) return

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

async function runReminderPass() {
  busy.value = true
  try {
    const res = await api.post('/admin/email/announce-now')
    const chased = res.chased?.sent ?? 0
    flash(`${chased} purchase reminder(s) sent.`)
    await loadEmail()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const campaignTone = {
  sent: 'success', sending: 'accent', failed: 'danger', draft: 'neutral',
  awaiting_approval: 'brand', scheduled: 'accent', dismissed: 'neutral',
}

// ------------------------------------------------------- approval queue ---
// Bulk email the site wrote on its own — today, "tickets are on sale", written
// as soon as an event has a sale time. None of it goes until somebody approves
// it: sent now, or approved for its time (scheduled) and sent then by itself.
const queue = ref([])
// The draft open in the review dialog, exactly as the server last described it
// (recipients resolved, preview rendered). `mailDraft` is the editable copy.
const reviewing = ref(null)
const mailDraft = reactive({ subject: '', heading: '', body: '', audiences: [], extra: [], excluded: [] })
const addAddresses = ref('')
const recipientFilter = ref('')
const reviewError = ref('')
const reviewNotice = ref('')

const awaitingCount = computed(() => queue.value.filter((q) => q.status === 'awaiting_approval').length)

function setQueue(items) {
  queue.value = items
  // The tab label's count comes from the dashboard until the queue itself has
  // been loaded; keep the two saying the same thing. Scheduled emails are
  // already approved, so only the ones still needing a decision count.
  if (dash.value) dash.value.emailsAwaitingApproval = items.filter((q) => q.status === 'awaiting_approval').length
}

function fillDraft(d) {
  reviewing.value = d
  mailDraft.subject = d.subject
  mailDraft.heading = d.heading
  mailDraft.body = d.body
  mailDraft.audiences = [...d.audiences]
  mailDraft.extra = [...d.extra]
  mailDraft.excluded = [...d.excluded]
}

async function openReview(id) {
  busy.value = true
  error.value = ''
  reviewError.value = ''
  reviewNotice.value = ''
  recipientFilter.value = ''
  addAddresses.value = ''
  try {
    // The picker's labels and counts come from the audiences list.
    if (!audiences.value.length) await loadEmail()
    fillDraft(await api.get(`/admin/email/queue/${id}`))
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function closeReview() {
  reviewing.value = null
}

/** Unsaved changes to the words. Recipient changes save as they are made. */
const draftDirty = computed(() =>
  Boolean(reviewing.value) &&
  (mailDraft.subject !== reviewing.value.subject ||
    mailDraft.heading !== reviewing.value.heading ||
    mailDraft.body !== reviewing.value.body),
)

/** Save the draft as it stands on screen. The answer is the whole review
 *  screen worked out afresh, so the list and preview never describe anything
 *  other than what Send would do. */
async function saveDraft() {
  fillDraft(
    await api.put(`/admin/email/queue/${reviewing.value.id}`, {
      subject: mailDraft.subject,
      heading: mailDraft.heading,
      body: mailDraft.body,
      audiences: mailDraft.audiences,
      extra: mailDraft.extra,
      excluded: mailDraft.excluded,
    }),
  )
}

async function draftAction(fn) {
  busy.value = true
  reviewError.value = ''
  reviewNotice.value = ''
  try {
    await fn()
  } catch (e) {
    reviewError.value = e.message
  } finally {
    busy.value = false
  }
}

function toggleDraftAudience(key) {
  const at = mailDraft.audiences.indexOf(key)
  if (at === -1) mailDraft.audiences.push(key)
  else mailDraft.audiences.splice(at, 1)
  draftAction(saveDraft)
}

/** Take one address off. One typed in by hand is simply forgotten; one that
 *  came from a ticked audience is remembered as taken off, so re-resolving the
 *  audience at send does not put it back. */
function removeRecipient(email) {
  const at = mailDraft.extra.indexOf(email)
  if (at !== -1) mailDraft.extra.splice(at, 1)
  else if (!mailDraft.excluded.includes(email)) mailDraft.excluded.push(email)
  draftAction(saveDraft)
}

function restoreRecipient(email) {
  mailDraft.excluded = mailDraft.excluded.filter((e) => e !== email)
  draftAction(saveDraft)
}

function addRecipients() {
  const typed = addAddresses.value
    .split(/[\s,;]+/)
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.includes('@'))
  if (!typed.length) return
  for (const email of typed) {
    mailDraft.excluded = mailDraft.excluded.filter((e) => e !== email)
    if (!mailDraft.extra.includes(email)) mailDraft.extra.push(email)
  }
  addAddresses.value = ''
  draftAction(saveDraft)
}

function testDraft() {
  draftAction(async () => {
    await saveDraft()
    const res = await api.post(`/admin/email/queue/${reviewing.value.id}/test`)
    reviewNotice.value = res.message
  })
}

/** Approved, waiting for its time: read-only until taken off the schedule,
 *  so nothing approved changes without being approved again. */
const isScheduled = computed(() => reviewing.value?.status === 'scheduled')

/** Whether "approve for its time" is on offer: it has a time, still ahead. */
const canSchedule = computed(() => {
  const at = reviewing.value?.sendAt
  return !isScheduled.value && Boolean(at) && new Date(at) > new Date()
})

function sendNowDraft() {
  draftAction(async () => {
    if (!isScheduled.value) await saveDraft()
    const d = reviewing.value
    if (!confirm(`Send "${d.subject}" to ${sendTargets(d.count, mailDraft.audiences)} now? This cannot be undone.${stagingNote(mailDraft.audiences)}`)) return
    const res = await api.post(`/admin/email/queue/${d.id}/send`)
    flash(res.message)
    closeReview()
    await loadEmail()
  })
}

function scheduleDraft() {
  draftAction(async () => {
    await saveDraft()
    const d = reviewing.value
    if (!confirm(
      `Approve "${d.subject}" to go out ${d.sendAtText}?\n\n` +
      `It goes to whoever is on the list then (${sendTargets(d.count, mailDraft.audiences)} now), with nobody needing to press anything.${stagingNote(mailDraft.audiences)}`,
    )) return
    const res = await api.post(`/admin/email/queue/${d.id}/schedule`)
    flash(res.message)
    closeReview()
    await loadEmail()
  })
}

function unscheduleDraft() {
  draftAction(async () => {
    const res = await api.post(`/admin/email/queue/${reviewing.value.id}/unschedule`)
    reviewNotice.value = res.message
    fillDraft(await api.get(`/admin/email/queue/${reviewing.value.id}`))
    await loadEmail()
  })
}

function dismissDraft() {
  if (!confirm('Dismiss this email? Nothing is sent, and it will not come back.')) return
  draftAction(async () => {
    const res = await api.post(`/admin/email/queue/${reviewing.value.id}/dismiss`)
    flash(res.message)
    closeReview()
    await loadEmail()
  })
}

const draftGroups = computed(() =>
  reviewing.value?.eventAudiences?.length
    ? [['This event', reviewing.value.eventAudiences], ...audienceGroups.value]
    : audienceGroups.value,
)

/** What each recipient's `source` is called, for the little tag beside it. */
const sourceLabels = computed(() => {
  const out = { added: 'added by hand', alerts: 'asked about this event' }
  for (const a of [...audiences.value, ...(reviewing.value?.eventAudiences ?? [])]) out[a.key] = a.label
  return out
})

// A few hundred rows with a button each is fine; a few thousand is not. The
// filter finds anybody, and the count above the list is always the real one.
const RECIPIENTS_SHOWN = 200
const shownRecipients = computed(() => {
  const list = reviewing.value?.recipientList ?? []
  const q = recipientFilter.value.trim().toLowerCase()
  return (q ? list.filter((r) => r.email.includes(q)) : list).slice(0, RECIPIENTS_SHOWN)
})

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
  // These are two independently revocable read permissions. A sponsor directory
  // viewer should not get a 403 on every admin tab simply for lacking either.
  const canInbox = can('sponsors.inbox')
  const canDigest = can('sponsors.digest.view')
  if (!canInbox && !canDigest) return
  try {
    const [inbox, digest] = await Promise.all([
      canInbox ? api.get('/admin/sponsors/inbox') : Promise.resolve(null),
      canDigest ? api.get('/admin/sponsors/roles-digest') : Promise.resolve(null),
    ])
    sponsorInbox.value = inbox ?? (canDigest
      ? { notes: [], marketing: [], requests: [], interest: [], listings: [] }
      : null)
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
  if (t === 'overview') load()
})

// The WhatsApp groups the roles digest is also posted into. None until ticked.
const digestWhatsApp = ref([])

async function sendRolesDigest() {
  const d = rolesDigest.value
  if (!d?.count) return
  const wa = digestWhatsApp.value.length ? ` and post it into ${digestWhatsApp.value.length} WhatsApp group(s)` : ''
  if (!confirm(`Email ${d.count} new role(s) to ${d.recipients} member(s) subscribed to jobs and internships${wa}?`)) return
  busy.value = true
  try {
    const res = await api.post('/admin/sponsors/roles-digest', { whatsapp: digestWhatsApp.value })
    flash(res.message)
    digestWhatsApp.value = []
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

async function savePackageAllowance(c) {
  const allowance = Number(packageEdits[c.key])
  if (!Number.isInteger(allowance) || allowance < 0) {
    error.value = 'An allowance is a whole number, 0 or more.'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const res = await api.put(`/admin/sponsor-package/${c.key}`, { allowance })
    sponsorPackage.value = res.package
    for (const x of res.package) packageEdits[x.key] = x.allowance
    flash(res.message)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
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
      permissions: permissionsPayload(newRole.value, newPermissions.value),
    })
    newAdminEmail.value = ''
    resetNewPermissions()
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

/** Save role and its chosen individual permissions as one atomic update. */
async function saveAccess(a) {
  busy.value = true
  error.value = ''
  try {
    await api.post('/admin/admins', {
      email: a.email,
      role: editingAccessRole.value,
      permissions: permissionsPayload(editingAccessRole.value, editingPermissions.value),
    })
    editingAccessId.value = null
    flash(`Access updated for ${a.email}.`)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
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

async function loadPasskeyLinks() {
  if (!can('passkeys.view')) return
  try {
    const res = await api.get('/admin/passkey-links')
    passkeyLinks.value = res.links
    passkeyLinkTtlHours.value = res.ttlHours
  } catch (e) {
    error.value = e.message
  }
}

/** Make a passkey link for an address. A fresh one revokes any live link for
 *  the same account, so the list never holds two that work. */
async function makePasskeyLink(email) {
  const address = (email || '').trim()
  if (!address) return
  error.value = ''
  busy.value = true
  try {
    const res = await api.post('/admin/passkey-links', { email: address })
    // Both spellings: an Imperial alias comes back as shortcode@ic.ac.uk.
    madeLinks[address.toLowerCase()] = res
    madeLinks[res.email.toLowerCase()] = res
    await loadPasskeyLinks()
    return res
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function makeLinkFromForm() {
  if (await makePasskeyLink(passkeyLinkEmail.value)) passkeyLinkEmail.value = ''
}

async function revokePasskeyLink(l) {
  if (!confirm(`Revoke the passkey link for ${l.email}? It stops working straight away.`)) return
  try {
    await api.del(`/admin/passkey-links/${l.id}`)
    for (const [key, made] of Object.entries(madeLinks)) {
      if (made.id === l.id) delete madeLinks[key]
    }
    await loadPasskeyLinks()
  } catch (e) {
    error.value = e.message
  }
}

async function loadManagedLockers() {
  if (!can('lockers.inventory.view')) return
  try {
    const [all, holders] = await Promise.all([
      api.get('/admin/lockers'), api.get('/admin/locker-holders'),
    ])
    managedLockers.value = all ?? []
    lockerHolders.value = holders?.holders ?? []
  } catch (e) { error.value = e.message }
}

async function releaseLockerHolder(holder) {
  if (!confirm(`Release locker ${holder.locker} from ${holder.name || holder.email}?`)) return
  busy.value = true
  error.value = ''
  try {
    await api.post(`/lockers/bookings/${holder.bookingId}/release`)
    flash('Locker released.')
    await loadManagedLockers()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function toggleLockerActive(locker) {
  if (!confirm(`${locker.is_active ? 'Disable' : 'Enable'} locker ${locker.number}?`)) return
  busy.value = true
  try {
    await api.patch(`/admin/lockers/${locker.id}`, { is_active: !locker.is_active })
    await loadManagedLockers()
    flash('Locker updated.')
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function loadAllowances() {
  if (!can('lockers.allowances.view')) return
  try {
    const res = await api.get('/admin/locker-grants')
    allowances.value = res.allowances
    allowanceYear.value = res.year
    allowanceMax.value = res.max
  } catch (e) {
    error.value = e.message
  }
}

/** Give, or change, somebody's allowance. Setting it again replaces the
 *  number rather than adding to it. */
async function giveAllowance() {
  const who = allowanceForm.who.trim()
  if (!who) return
  error.value = ''
  busy.value = true
  try {
    const res = await api.put(`/admin/locker-grants/${encodeURIComponent(who)}`, {
      free_count: Number(allowanceForm.free),
      note: allowanceForm.note,
    })
    const n = res.free_count
    flash(`${res.shortcode} has ${n} free locker${n === 1 ? '' : 's'} for ${res.academic_year}.`)
    Object.assign(allowanceForm, { who: '', free: 1, note: '' })
    await loadAllowances()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function changeAllowance(a) {
  Object.assign(allowanceForm, { who: a.shortcode, free: a.free, note: a.note || '' })
}

async function removeAllowance(a) {
  if (!confirm(
    `Take away ${a.name || a.shortcode}'s free locker allowance? `
    + 'A locker they have already booked stays theirs.',
  )) return
  try {
    await api.del(`/admin/locker-grants/${encodeURIComponent(a.shortcode)}`)
    await loadAllowances()
  } catch (e) {
    error.value = e.message
  }
}

function shortDate(v) {
  return new Date(v).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

/** The link made most recently from the People tab's form, if still on screen. */
const lastMadeLink = computed(() => {
  const made = Object.values(madeLinks)
  return made.length ? made.reduce((a, b) => (a.id > b.id ? a : b)) : null
})

function linkStatus(l) {
  const at = (v) => new Date(v).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  if (l.status === 'live') return `works until ${at(l.expiresAt)}`
  if (l.status === 'used') return `used ${at(l.usedAt)}`
  if (l.status === 'revoked') return `revoked ${at(l.revokedAt)}${l.revokedBy ? ` by ${l.revokedBy}` : ''}`
  return `expired ${at(l.expiresAt)}`
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
const linkForm = reactive({ purpose: 'locker', union_key: '', input_mode: 'api', shop_ref: '' })

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
  if (eventProduct.value && !seen.has(eventProduct.value)) {
    seen.set(eventProduct.value, `Previously linked product #${eventProduct.value} (not in feed)`)
  }
  return [...seen.entries()].map(([id, name]) => ({ id, name }))
})

/** Every sellable Union product-line, for the per-ticket-type picker.
 *
 * Each ticket type is its own product in eActivities, so the choice here is a
 * product, not a line within the event's product. The line is still carried in
 * the key for the rarer case of one product with several lines.
 */
const ticketProductOptions = computed(() => {
  const options = products.value.map((p) => ({
    key: `${p.product_id}:${p.product_line_id ?? ''}`,
    label:
      p.line_name && p.line_name !== p.product_name
        ? `${p.product_name} — ${p.line_name}`
        : p.product_name,
  }))
  for (const key of Object.values(ticketProducts)) {
    if (key && !options.some((p) => p.key === key)) {
      options.push({ key, label: `Previously linked product #${key} (not in feed)` })
    }
  }
  return options
})

// The Union shop displays #3023 but eActivities' sales feed records #1003023.
// The API selection path continues to send its original id untouched; only
// manual entry is converted on the server when saved.
function shortShopId(productId) {
  return String(productId > 1_000_000 ? productId - 1_000_000 : productId)
}
function isManualOnlyProduct(link) {
  return link.union_product_id > 1_000_000 && !products.value.some(
    (p) => p.product_id === link.union_product_id &&
      (link.union_product_line_id == null || p.product_line_id === link.union_product_line_id),
  )
}
function manualRef(input) {
  const value = String(input || '').trim()
  if (!value) throw new Error('Enter a SUMS product ID or Union shop URL.')
  return value
}

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
const eventProductMode = ref('api')
const eventShopRef = ref('')
const ticketProductModes = reactive({})
const ticketShopRefs = reactive({})

async function saveEventProduct() {
  busy.value = true
  error.value = ''
  try {
    const manual = eventProductMode.value === 'manual'
    await api.put(`/admin/events/${editingId.value}/product`, manual
      ? { shop_product_ref: manualRef(eventShopRef.value) }
      : { union_product_id: eventProduct.value || null })
    flash(manual || eventProduct.value ? 'Product linked to this event.' : 'Product unlinked.')
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
    const manual = ticketProductModes[t.id] === 'manual'
    const [productId, lineId] = String(ticketProducts[t.id] || '').split(':')
    await api.put(`/admin/ticket-types/${t.id}/product`, manual
      ? { shop_product_ref: manualRef(ticketShopRefs[t.id]) }
      : {
          union_product_id: productId ? Number(productId) : null,
          union_product_line_id: lineId ? Number(lineId) : null,
        })
    flash(manual || productId ? `${t.name} linked to its product.` : `${t.name} unlinked.`)
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
  const manual = linkForm.input_mode === 'manual'
  const [productId, lineId] = String(linkForm.union_key).split(':')
  if (!manual && !productId) {
    error.value = 'Pick a Union product.'
    return
  }
  try {
    await api.post('/admin/product-links', {
      purpose: linkForm.purpose,
      event_id: null,
      ticket_type_id: null,
      ...(manual
        ? { shop_product_ref: manualRef(linkForm.shop_ref) }
        : { union_product_id: Number(productId), union_product_line_id: lineId ? Number(lineId) : null }),
    })
    flash('Product linked.')
    linkForm.union_key = ''
    linkForm.shop_ref = ''
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
usePageTitle(computed(() => `Committee · ${TABS.find(([id]) => id === tab.value)?.[1] || 'Admin'}`))
</script>

<template>
  <main class="admin">
    <div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-6); flex-wrap:wrap">
      <h1 style="margin:0; font:var(--type-h1)">Committee admin</h1>
      <div style="display:flex; gap:var(--space-4); align-items:center">
        <!-- Every committee member can work the door, role or not. -->
        <Button v-if="(hasAdminArea) && (can('events.checkin.view'))" size="sm" variant="secondary" @click="router.push('/checkin')">Door check-in</Button>
        <Button v-if="can('photos.view')" size="sm" variant="secondary" @click="router.push('/photo-studio')">Photo print studio</Button>
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
        >{{ label }}<template v-if="id === 'email' && dash?.emailsAwaitingApproval"> ({{ dash.emailsAwaitingApproval }})</template><template v-if="id === 'opportunities' && dash?.opportunitiesAwaitingApproval"> ({{ dash.opportunitiesAwaitingApproval }})</template></Button>
      </nav>

      <p v-if="notice" style="font:var(--type-small); color:var(--text-muted)">{{ notice }}</p>
      <p v-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>

      <PhotoStudioPanel v-if="tab === 'photos'" />

      <!-- ========================== OPPORTUNITIES ========================== -->
      <OpportunitiesAdminPanel
        v-if="tab === 'opportunities'"
        @open-email="openOpportunityEmail"
        @changed="load"
      />

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
        <Card v-if="can('opportunities') && dash.opportunitiesAwaitingApproval" variant="raised" class="pending-card" style="margin-bottom:var(--space-6)">
          <div style="display:grid; gap:var(--space-4)">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:var(--space-4)">
              <h2 style="margin:0; font:var(--type-h4)">Opportunities awaiting approval <Badge tone="danger">{{ dash.opportunitiesAwaitingApproval }}</Badge></h2>
              <Button size="sm" @click="tab = 'opportunities'">Review opportunities</Button>
            </div>
            <div v-for="item in dash.pendingOpportunities.slice(0, 5)" :key="item.id" style="display:flex; justify-content:space-between; gap:var(--space-3); flex-wrap:wrap; align-items:center">
              <div style="display:grid; gap:var(--space-1)">
                <strong style="font:var(--type-small)">{{ item.title }}<template v-if="item.isRevision"> (revised listing)</template></strong>
                <span style="font:var(--type-2xs); color:var(--text-muted)">{{ item.submittedBy || 'Member' }} · {{ new Date(item.createdAt).toLocaleDateString('en-GB') }}</span>
              </div>
              <Button variant="secondary" size="sm" @click="tab = 'opportunities'">Review</Button>
            </div>
            <span v-if="dash.opportunitiesAwaitingApproval > 5" style="font:var(--type-small); color:var(--text-muted)">and {{ dash.opportunitiesAwaitingApproval - 5 }} more awaiting review</span>
          </div>
        </Card>
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
              <Button v-if="w.action === 'sync' && can('events.sync.union')" size="sm" variant="secondary" @click="sync">Sync now</Button>
              <Button v-if="w.action === 'links'" size="sm" variant="secondary" @click="tab = 'links'">Link products</Button>
              <Button v-if="w.action === 'overdue'" size="sm" variant="secondary" @click="tab = 'overdue'">Review them</Button>
              <Button v-if="w.action === 'email' && can('email')" size="sm" variant="secondary" @click="tab = 'email'">Review them</Button>
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
          <Button v-if="can('events.sync.union')" :disabled="busy" @click="sync">{{ busy ? 'Working…' : 'Sync from eActivities' }}</Button>
          <Button v-if="can('events.sync.reconcile')" variant="secondary" :disabled="busy" @click="reconcile">Match bookings now</Button>
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
          <div class="admin-event-list" data-testid="admin-events-list">
            <div class="admin-event-list__heading">
              <div>
                <h2>Events</h2>
                <span>Latest event date first · {{ orderedEvents.length }} total</span>
              </div>
              <span v-if="filteredEvents.length" data-testid="admin-events-range">
                {{ activeEventPage * EVENT_PAGE_SIZE + 1 }}–{{ Math.min((activeEventPage + 1) * EVENT_PAGE_SIZE, filteredEvents.length) }} of {{ filteredEvents.length }}{{ eventSearch ? ' matches' : '' }}
              </span>
            </div>
            <Input v-if="orderedEvents.length > EVENT_PAGE_SIZE" v-model="eventSearch"
                   aria-label="Find an event" placeholder="Find an event by name or category…" />
            <p v-if="!filteredEvents.length" class="admin-event-list__empty">{{ eventSearch ? 'No matching events.' : 'No events yet.' }}</p>
            <Card v-for="e in pagedEvents" :key="e.id" variant="outline" class="admin-event-card" data-testid="admin-event-card">
              <div class="admin-event-card__layout">
                <div class="admin-event-card__summary">
                  <div class="admin-event-card__badges">
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
                <div class="admin-event-card__controls">
                  <label v-if="e.can_badge" style="display:flex; gap:var(--space-2); align-items:center; font:var(--type-2xs); color:var(--text-muted)">
                    What's On label
                    <select
                      class="ees-select"
                      :aria-label="`What's On label for ${e.title}`"
                      :value="e.availability_badge || 'auto'"
                      :disabled="busy"
                      @change="saveEventAvailabilityBadge(e, $event.target.value)"
                    >
                      <option value="auto">Automatic</option>
                      <option value="not_many_left">Not many left</option>
                      <option value="selling_fast">Selling fast</option>
                    </select>
                  </label>
                  <label
                    v-if="e.can_waitlist"
                    style="display:flex; gap:var(--space-2); align-items:center; font:var(--type-2xs); color:var(--text-muted)"
                  >
                    <input
                      type="checkbox"
                      :checked="e.waitlist_enabled !== false"
                      :disabled="busy"
                      :aria-label="`Allow waitlist for ${e.title}`"
                      @change="saveEventWaitlistEnabled(e, $event.target.checked)"
                    />
                    Allow waitlist when full
                  </label>
                  <Button v-if="e.can_publish" size="sm" variant="ghost" @click="togglePublished(e)">{{ e.published ? 'Unpublish' : 'Publish' }}</Button>
                  <Button v-if="e.can_book" size="sm" variant="secondary" @click="router.push(`/admin/events/${e.id}`)">Bookings</Button>
                  <Button v-if="e.can_edit && (can('events.edit') || can('events.create'))" size="sm" variant="ghost" @click="editEvent(e)">Edit</Button>
                  <Button v-if="(can('email')) && (can('email.announce_event'))" size="sm" variant="secondary" title="Email people about it" @click="announceEvent(e)">Announce</Button>
                  <Button v-if="e.can_delete" size="sm" variant="danger" @click="removeEvent(e)">Delete</Button>
                  <span v-if="!e.can_edit && !e.can_book" style="font:var(--type-2xs); color:var(--text-faint)">Read-only event</span>
                </div>
              </div>
            </Card>
            <nav v-if="filteredEvents.length > EVENT_PAGE_SIZE" class="admin-event-list__pages" aria-label="Event list pages">
              <Button size="sm" variant="secondary" :disabled="activeEventPage === 0" @click="changeEventPage(-1)">Previous</Button>
              <span aria-live="polite">Page {{ activeEventPage + 1 }} of {{ eventPageCount }}</span>
              <Button size="sm" variant="secondary" :disabled="activeEventPage === eventPageCount - 1" @click="changeEventPage(1)">Next</Button>
            </nav>
          </div>

          <Card v-if="can('events.create') || can('events.edit')" variant="offset" class="event-editor">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">
              {{ editingId ? 'Edit event' : 'New event' }}
            </h2>
            <div class="event-editor__form">
              <Field label="Title"><Input v-model="draft.title" /></Field>
              <template v-if="editingId && can('events.photo_short_name.edit')">
                <Field label="Photo short name" help="Optional. Replaces the full event name only on new photo print designs. The guest gallery keeps the full title.">
                  <Input v-model="draft.photo_short_name" maxlength="64" placeholder="e.g. Halloween Social" />
                </Field>
                <Button size="sm" variant="secondary" :disabled="busy || !can('events.photo_short_name.edit')"
                        @click="savePhotoShortName">Save photo short name</Button>
              </template>
              <Field label="Slug" help="Used in the URL."><Input v-model="draft.slug" /></Field>
              <div class="event-editor__pair">
                <Field label="Category"><Input v-model="draft.category" /></Field>
                <Field label="Venue"><Input v-model="draft.venue" /></Field>
              </div>
              <Field label="Event image" help="Upload a file, or paste a URL.">
                <Input v-model="draft.image_url" placeholder="/media/…" />
              </Field>
              <div style="display:flex; gap:var(--space-3); align-items:center; flex-wrap:wrap">
                <input
                  v-if="can('files')"
                  ref="eventImageInput"
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/gif"
                  :disabled="busy"
                  class="event-editor__file"
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
              <div class="event-editor__pair">
                <Field label="Starts"><Input v-model="draft.starts_at" type="datetime-local" /></Field>
                <Field label="Ends"><Input v-model="draft.ends_at" type="datetime-local" /></Field>
              </div>
              <Field
                label="Doors close"
                help="Optional. Shown on the event page and in every email — no entry after this time."
              >
                <Input v-model="draft.doors_close_at" type="datetime-local" />
              </Field>
              <div class="event-editor__pair">
                <Field label="Total capacity" help="Blank = per-type only."><Input v-model.number="draft.capacity" type="number" min="1" /></Field>
                <Field label="Hold (days)" help="Pay by midnight on the last day; 1 = the day they book."><Input v-model.number="draft.hold_days" type="number" min="1" /></Field>
              </div>
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

              <Field
                label="Venue map link (optional)"
                help="A map for the venue, shown on the event page. Google Maps links to a place work best: they open correctly on phones."
              >
                <Input v-model="draft.map_url" type="url" placeholder="https://maps.app.goo.gl/…" />
              </Field>

              <Field
                label="Online meeting link (optional)"
                help="For online events — Zoom, Teams, Meet. Tick the box below to show it only to people who have booked."
              >
                <Input v-model="draft.meeting_url" type="url" placeholder="https://imperial.zoom.us/j/…" />
              </Field>
              <label style="font:var(--type-small); color:var(--text-body); display:flex; gap:var(--space-2); align-items:center">
                <input v-model="draft.meeting_url_private" type="checkbox" />
                Meeting link visible to booked attendees only
              </label>

              <fieldset style="margin:0; padding:var(--space-3); border:1px solid var(--border-hairline); display:grid; gap:var(--space-3)">
                <legend style="padding:0 var(--space-2); font:var(--type-label)">Organising societies</legend>
                <p style="margin:0;font:var(--type-2xs);color:var(--text-muted)">Select the societies running this event. An external society's managing committee can manage the event when assigned. Booking restrictions are set separately below.</p>
                <template v-if="can('societies.events.assign')">
                  <label style="font:var(--type-small)"><input v-model="draft.eesoc_organised" type="checkbox" /> EESoc</label>
                  <label v-for="society in eligibleSocieties" :key="society.id" style="font:var(--type-small)">
                    <input v-model="draft.organiser_society_ids" type="checkbox" :value="society.id" /> {{ society.name }}
                  </label>
                  <p v-if="!draft.eesoc_organised && !draft.organiser_society_ids.length" style="margin:0;color:var(--danger);font:var(--type-small)">Select at least one organiser.</p>
                  <p style="margin:0;font:var(--type-2xs);color:var(--text-muted)">For an external-only event, untick EESoc and choose the external society.</p>
                  <p v-if="!draft.eesoc_organised && draft.organiser_society_ids.length && !draft.membership_restricted" style="margin:0;font:var(--type-2xs);color:var(--text-muted)">Booking is still public. To make this members-only, use the separate booking eligibility selector below.</p>
                </template>
                <p v-else style="margin:0;font:var(--type-small);color:var(--text-muted)">
                  {{ [draft.eesoc_organised ? 'EESoc' : null, ...eligibleSocieties.filter(s => draft.organiser_society_ids.includes(s.id)).map(s => s.name)].filter(Boolean).join(', ') || 'No organisers' }}
                </p>
              </fieldset>

              <fieldset style="margin:0; padding:var(--space-3); border:1px solid var(--border-hairline); display:grid; gap:var(--space-3)">
                <legend style="padding:0 var(--space-2); font:var(--type-label)">Who can book this event?</legend>
                <template v-if="can('events.membership_eligibility')">
                  <label style="font:var(--type-small)"><input v-model="draft.membership_restricted" type="radio" :value="false" /> Everyone (no event-level membership requirement)</label>
                  <label style="font:var(--type-small)"><input v-model="draft.membership_restricted" type="radio" :value="true" /> Members of the selected societies only</label>
                  <div v-if="draft.membership_restricted" style="display:grid;gap:var(--space-2);padding-left:var(--space-4)">
                    <label style="font:var(--type-small)"><input v-model="draft.allow_eesoc_members" type="checkbox" /> EESoc members</label>
                    <label v-for="society in eligibleSocieties" :key="society.id" style="font:var(--type-small)">
                      <input v-model="draft.eligible_society_ids" type="checkbox" :value="society.id" /> {{ society.name }} members
                    </label>
                    <p style="margin:0;font:var(--type-2xs);color:var(--text-muted)">Leave EESoc unticked for an external-society-only event. Students belonging to any ticked society qualify. Ticket-specific restrictions still apply.</p>
                    <p v-if="!draft.allow_eesoc_members && !draft.eligible_society_ids.length" style="margin:0;color:var(--danger);font:var(--type-small)">Select at least one society to restrict bookings.</p>
                  </div>
                </template>
                <p v-else style="margin:0;font:var(--type-small);color:var(--text-muted)">
                  {{ draft.membership_restricted ? `Restricted: ${draft.allow_eesoc_members ? 'EESoc, ' : ''}${draft.eligible_society_ids.length} external societies` : 'Public event' }}
                </p>
              </fieldset>

              <!-- Who the event is for. Nothing is emailed from here — the
                   year groups are recorded so a future announcement can aim
                   at the right department lists; blank means everyone. -->
              <fieldset style="margin:0; padding:0; border:0; display:grid; gap:var(--space-2)">
                <legend style="padding:0; font:var(--type-label); color:var(--text-muted)">Aimed at year groups</legend>
                <div style="display:flex; gap:var(--space-4); flex-wrap:wrap">
                  <label
                    v-for="y in 4"
                    :key="y"
                    style="font:var(--type-small); color:var(--text-body); display:flex; gap:var(--space-2); align-items:center"
                  >
                    <input v-model="draft.target_years" type="checkbox" :value="y" />
                    {{ ['1st', '2nd', '3rd', '4th'][y - 1] }} year
                  </label>
                </div>
                <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                  None ticked = aimed at everyone.
                </p>
              </fieldset>

              <TraceRule />
              <h3 style="margin:0; font:var(--type-h5)">Union product (optional)</h3>
              <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                Only needed if every ticket type shares one product. Normally each
                ticket type is its own product — set that on the type itself below,
                which takes precedence over anything chosen here.
              </p>
              <div v-if="editingId" style="display:flex; gap:var(--space-2); align-items:flex-end; flex-wrap:wrap">
                <Field label="Link using" style="flex:0 1 180px">
                  <select v-model="eventProductMode" class="ees-select">
                    <option value="api">Synced API product</option>
                    <option value="manual">SUMS ID / shop URL</option>
                  </select>
                </Field>
                <Field v-if="eventProductMode === 'api'" label="Product" style="flex:1 1 220px">
                  <select v-model="eventProduct" class="ees-select">
                    <option :value="null">Not linked</option>
                    <option v-for="p in productOptions" :key="p.id" :value="p.id">{{ p.name }}</option>
                  </select>
                </Field>
                <Field v-else label="SUMS product ID or shop URL" style="flex:1 1 220px">
                  <Input v-model="eventShopRef" placeholder="3023 or https://www-s.imperialcollegeunion.org/shop/product/3023-/" />
                </Field>
                <Button v-if="can('events.ticket_types.product')" size="sm" :disabled="busy" @click="saveEventProduct">Link</Button>
              </div>
              <p v-else style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                Save the event first, then link its Union product.
              </p>
              <p v-if="!products.length" style="margin:0; font:var(--type-2xs); color:var(--danger)">
                No products in the API feed yet. You can still link using a SUMS ID or shop URL.
              </p>

              <TraceRule />
              <h3 style="margin:0; font:var(--type-h5)">Ticket types</h3>
              <div v-for="(t, i) in draft.ticket_types" :key="i" class="event-editor__ticket">
                <div class="event-editor__ticket-fields">
                  <Field label="Name"><Input v-model="t.name" :disabled="!can(t.id ? 'events.ticket_types.edit' : 'events.ticket_types.create')" placeholder="With alcohol" /></Field>
                  <Field label="Price (£)"><Input v-model="t.price_pounds" :disabled="!can(t.id ? 'events.ticket_types.edit' : 'events.ticket_types.create')" inputmode="decimal" placeholder="0" /></Field>
                  <Field label="Cap"><Input v-model.number="t.capacity" :disabled="!can(t.id ? 'events.ticket_types.edit' : 'events.ticket_types.create')" type="number" min="1" /></Field>
                  <Field label="Max/user"><Input v-model.number="t.max_per_user" :disabled="!can(t.id ? 'events.ticket_types.edit' : 'events.ticket_types.create')" type="number" min="1" /></Field>
                </div>
                <label style="font:var(--type-2xs); color:var(--text-muted)">
                  <input v-model="t.members_only" type="checkbox" /> EESoc members only
                  <input v-model="t.requires_purchase" type="checkbox" style="margin-left:var(--space-4)" /> Needs a Union purchase
                </label>

                <Field v-if="eligibleSocieties.length && can('events.ticket_types.eligibility')" label="Also allow members of these societies" help="If EESoc members only is selected, qualifying EESoc members OR members of any selected society can book.">
                  <div style="display:flex;flex-wrap:wrap;gap:var(--space-3)">
                    <label v-for="society in eligibleSocieties" :key="society.id" style="font:var(--type-small)">
                      <input v-model="t.eligible_society_ids" type="checkbox" :value="society.id" /> {{ society.name }}
                    </label>
                  </div>
                </Field>

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
                <template v-if="t.id && t.requires_purchase && can('events.ticket_types.product')">
                  <div style="display:flex; gap:var(--space-2); align-items:flex-end; flex-wrap:wrap">
                    <Field label="Link using" style="flex:0 1 180px">
                      <select v-model="ticketProductModes[t.id]" class="ees-select">
                        <option value="api">Synced API product</option>
                        <option value="manual">SUMS ID / shop URL</option>
                      </select>
                    </Field>
                    <Field v-if="ticketProductModes[t.id] !== 'manual'" label="Union product" style="flex:1 1 260px">
                      <select v-model="ticketProducts[t.id]" class="ees-select">
                        <option value="">Not linked — use the event's product</option>
                        <option v-for="o in ticketProductOptions" :key="`${t.id}-${o.key}`" :value="o.key">
                          {{ o.label }}
                        </option>
                      </select>
                    </Field>
                    <Field v-else label="SUMS product ID or shop URL" style="flex:1 1 260px">
                      <Input v-model="ticketShopRefs[t.id]" placeholder="3023 or paste the full Union shop URL" />
                    </Field>
                    <Button v-if="can('events.ticket_types.product')" size="sm" :disabled="busy" @click="saveTicketProduct(t)">Link</Button>
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
                <Button v-if="can('events.ticket_types.delete')"
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
              <Button v-if="can('events.ticket_types.create')" size="sm" variant="ghost" @click="draft.ticket_types.push({ name: '', price_pounds: '', capacity: null, max_per_user: 1, members_only: false, eligible_society_ids: [], requires_purchase: true, purchase_url: '', sales_open_enabled: false, sales_open_at: '', sales_close_enabled: false, sales_close_at: '' })">
                Add ticket type
              </Button>

              <!-- When booking opens. Right after the ticket types because it
                   only appears once there is one — above them, it turned up
                   out of sight, back up the form.

                   An explicit choice, not "leave the date blank". Clearing a
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
                      ? 'Bookings open automatically at that time. Once published, its “tickets are on sale” email waits in the Email tab for you to approve.'
                      : 'The event is listed, but cannot be booked until you set a date.'
                  }}
                </p>

                <!-- The end of the sale window (section 17). Both optional;
                     unticked, booking stays open until the event. -->
                <template v-if="draft.sales_scheduled">
                  <label style="margin-top:var(--space-2); font:var(--type-small); color:var(--text-body); display:flex; gap:var(--space-2); align-items:center">
                    <input v-model="draft.reservations_close_enabled" type="checkbox" />
                    Stop taking reservations at a set time
                  </label>
                  <template v-if="draft.reservations_close_enabled">
                    <Input v-model="draft.reservations_close_at" type="datetime-local" aria-label="Reservations close" />
                    <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                      Nobody can reserve after this. Tickets you add for someone still work.
                    </p>
                  </template>
                  <label style="font:var(--type-small); color:var(--text-body); display:flex; gap:var(--space-2); align-items:center">
                    <input v-model="draft.union_close_enabled" type="checkbox" />
                    The Union shop stops selling at a set time
                  </label>
                  <template v-if="draft.union_close_enabled">
                    <Input v-model="draft.union_sales_close_at" type="datetime-local" aria-label="Union sales close" />
                    <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                      No pay-by deadline goes past this: booked at 12:00 with a 16:00
                      close, they have until 16:00. Must not be before reservations close.
                    </p>
                  </template>

                  <!-- Each ticket type's own window (080), inside the event's:
                       early bird from the sale date until Friday, general from
                       Friday. Tickboxes rather than blank times, for the same
                       Safari reason as above. -->
                  <label
                    v-if="draft.ticket_types.length > 1 || draft.ticket_windows"
                    style="margin-top:var(--space-2); font:var(--type-small); color:var(--text-body); display:flex; gap:var(--space-2); align-items:center"
                  >
                    <input v-model="draft.ticket_windows" type="checkbox" data-testid="ticket-windows" />
                    Ticket types go on sale at different times (early bird, general…)
                  </label>
                  <template v-if="draft.ticket_windows">
                    <div
                      v-for="(t, i) in draft.ticket_types"
                      :key="`window-${i}`"
                      :data-testid="`ticket-window-${i}`"
                      style="display:grid; gap:var(--space-2); padding:var(--space-3); border-left:2px solid var(--border-hairline)"
                    >
                      <span style="font:var(--type-small); color:var(--text-strong)">{{ t.name.trim() || 'Unnamed ticket type' }}</span>
                      <label style="font:var(--type-2xs); color:var(--text-muted); display:flex; gap:var(--space-2); align-items:center">
                        <input v-model="t.sales_open_enabled" type="checkbox" />
                        Goes on sale later than the event's sale date
                      </label>
                      <Input
                        v-if="t.sales_open_enabled"
                        v-model="t.sales_open_at"
                        type="datetime-local"
                        :aria-label="`${t.name} goes on sale`"
                      />
                      <label style="font:var(--type-2xs); color:var(--text-muted); display:flex; gap:var(--space-2); align-items:center">
                        <input v-model="t.sales_close_enabled" type="checkbox" />
                        Stops selling at a set time
                      </label>
                      <Input
                        v-if="t.sales_close_enabled"
                        v-model="t.sales_close_at"
                        type="datetime-local"
                        :aria-label="`${t.name} stops selling`"
                      />
                      <p
                        v-if="(t.sales_open_enabled && !t.sales_open_at) || (t.sales_close_enabled && !t.sales_close_at)"
                        style="margin:0; font:var(--type-2xs); color:var(--danger)"
                      >
                        Pick a time, or untick the box.
                      </p>
                    </div>
                    <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                      Early bird: tick “Stops selling” on it. General: tick “Goes on
                      sale later” with the same time. Unticked, a ticket type sells for
                      as long as the event does. The “tickets are on sale” email goes
                      out at the event's sale date and lists a later ticket with the
                      time it goes on sale.
                    </p>
                  </template>
                </template>
              </fieldset>

              <label style="font:var(--type-small)"><input v-model="draft.published" type="checkbox" :disabled="!can('events.publish')" /> Published</label>
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
                calendars for them to register interest. It need not be published
                or planned: tick it on a draft with a rough date and a description
                of the idea, and sponsors see it as provisional while students see
                nothing until you publish. Link the sponsor who takes it from their
                page under Sponsors, which takes it off everyone else's list.
              </p>
              <div class="event-editor__actions">
                <Button v-if="editingId ? (can('events.edit') || can('events.create')) : can('events.create')" :disabled="busy" @click="saveEvent">{{ editingId ? 'Save changes' : 'Create event' }}</Button>
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
            Bookings requiring cancellation review
          </h2>
          <p style="margin:0 0 var(--space-5); font:var(--type-small); color:var(--text-muted)">
            No booking is cancelled automatically. Overdue holds and Union order
            numbers rejected after a successful next-day sales sync appear here
            for a committee decision. You can check a corrected order, use a
            separately permitted override, or cancel and release the place.
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
                  · {{ r.reviewCancellation ? 'checked' : 'due' }} {{ new Date(r.dueSince).toLocaleDateString() }}
                  <template v-if="r.orderNumber"> · order {{ r.orderNumber }}</template>
                </span>
              </div>
              <Badge :tone="r.reviewCancellation ? 'danger' : 'accent'">{{ r.reviewCancellation ? 'Order rejected — review cancellation' : 'Overdue' }}</Badge>
            </div>

            <div style="display:flex; gap:var(--space-2); align-items:flex-end; flex-wrap:wrap">
              <Field label="Union order number" style="flex:1 1 200px">
                <Input v-model="orderNumbers[r.id]" :placeholder="r.orderNumber || 'Enter a corrected order number'" maxlength="128" />
              </Field>
              <label v-if="can('events.bookings.confirm_override')" style="display:flex; gap:var(--space-2);align-items:center;font:var(--type-small)">
                <input v-model="reviewOverrides[r.id]" type="checkbox" /> Bypass Union payment verification
              </label>
              <Button v-if="r.status === 'confirmed' ? (can('events.bookings.order_number.edit') || can('events.bookings.confirm_override')) : (can('events.bookings.confirm') || can('events.bookings.confirm_override'))"
                size="sm" :disabled="busy || (!orderNumbers[r.id]?.trim() && !reviewOverrides[r.id])"
                @click="confirmBooking(r)">{{ reviewOverrides[r.id] ? (r.status === 'confirmed' ? 'Keep booking (override)' : 'Confirm override') : r.status === 'confirmed' ? 'Verify corrected order' : 'Verify order' }}</Button>
              <Button v-if="r.status === 'held' && can('events.bookings.remind')" size="sm" variant="secondary" :disabled="busy" @click="remind(r)">Send reminder</Button>
              <Button v-if="can('events.bookings.cancel')" size="sm" variant="danger" :disabled="busy" @click="cancelBooking(r)">Cancel booking</Button>
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
            <Button v-if="can('resources.import')" size="sm" variant="ghost" @click="reimport">Re-import from eesoc.com</Button>
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
                    <Button v-if="can('resources.entries.move')" size="sm" variant="ghost" @click="moveEntry(node, -1)">↑</Button>
                    <Button v-if="can('resources.entries.move')" size="sm" variant="ghost" @click="moveEntry(node, 1)">↓</Button>
                    <Button v-if="can('resources.entries.move')" size="sm" variant="danger" @click="removeEntry(node)">×</Button>
                  </span>
                </div>
                <div v-for="c in node.children" :key="c.id" style="padding-left:var(--space-5); display:flex; justify-content:space-between; align-items:center">
                  <span style="font:var(--type-2xs); color:var(--text-muted)">{{ c.title }} ({{ c.children.length }})</span>
                  <Button v-if="can('resources.entries.move')" size="sm" variant="ghost" @click="removeEntry(c)">×</Button>
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
              <Button v-if="can('resources.entries.create')" @click="addEntry">Add entry</Button>
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
                <Button v-if="can('files.delete')" size="sm" variant="danger" @click="removeUpload(u)">Delete</Button>
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
          <Button v-if="can('committee.sync')" :disabled="busy" @click="syncCommittee">
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
                    <Button v-if="can('committee.profiles')" size="sm" variant="ghost" :disabled="i === 0" @click="moveMember(m, -1)">←</Button>
                    <Button v-if="can('committee.profiles')" size="sm" variant="ghost" :disabled="i === group.members.length - 1" @click="moveMember(m, 1)">→</Button>
                  </div>
                  <Button v-if="can('committee.profiles')" size="sm" :disabled="busy" @click="saveMember(m)">Save</Button>
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
              <Field label="Link using">
                <select v-model="linkForm.input_mode" class="ees-select">
                  <option value="api">Synced API product</option>
                  <option value="manual">SUMS ID / shop URL</option>
                </select>
              </Field>
              <Field v-if="linkForm.input_mode === 'api'" label="Union product" help="'Any line' is the safe default — line ids are not guaranteed unique across products.">
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
              <Field v-else label="SUMS product ID or shop URL">
                <Input v-model="linkForm.shop_ref" placeholder="2771 or paste the full Union shop URL" />
              </Field>
              <Button v-if="can('products.link')" :disabled="busy || (linkForm.input_mode === 'api' && !products.length)" @click="createLink">Link product</Button>
              <p v-if="!products.length" style="margin:0; font:var(--type-2xs); color:var(--danger)">
                No products in the API feed yet. You can still link using a SUMS ID or shop URL.
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
              <Button v-if="can('products.unlink')" size="sm" variant="ghost" @click="deleteLink(l)">Remove</Button>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================ LOCKERS ============================= -->
      <template v-if="tab === 'lockers'">
        <div class="admin-cols">
          <Card v-if="can('lockers.allowances.view') || can('lockers.allowances.grant')" variant="offset">
            <h2 style="margin:0 0 var(--space-2); font:var(--type-h4)">Give free lockers</h2>
            <p style="margin:0 0 var(--space-4); font:var(--type-small); color:var(--text-muted)">
              For {{ allowanceYear }}, on top of any they buy on the Union shop.
              They pick a locker on the Lockers page like anyone else, and the
              allowance lapses when the academic year ends. Lockers are for
              Union members, so it does nothing until they are one.
            </p>
            <div style="display:grid; gap:var(--space-3)">
              <Field label="Shortcode" help="Or their Imperial email. Works before they have ever signed in.">
                <Input v-model="allowanceForm.who" placeholder="ab1234" />
              </Field>
              <Field label="How many lockers" :help="`1 to ${allowanceMax}. If they already have an allowance, this replaces it.`">
                <Input v-model="allowanceForm.free" type="number" min="1" :max="allowanceMax" />
              </Field>
              <Field label="Why" help="Optional, and only shown here.">
                <Input v-model="allowanceForm.note" placeholder="Committee — Treasurer" />
              </Field>
            </div>
            <Button v-if="can('lockers.allowances.grant')"
              size="sm"
              style="margin-top:var(--space-4)"
              :disabled="busy || !allowanceForm.who.trim()"
              @click="giveAllowance"
            >Give allowance</Button>
          </Card>

          <Card v-if="can('lockers.allowances.view') || can('lockers.allowances.grant')" variant="outline">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Allowances for {{ allowanceYear }}</h2>
            <p v-if="!allowances.length" style="margin:0; font:var(--type-small); color:var(--text-faint)">Nobody has free lockers yet.</p>
            <div
              v-for="a in allowances"
              :key="a.shortcode"
              style="display:flex; justify-content:space-between; align-items:center; gap:var(--space-3); padding:var(--space-3) 0; border-top:1px solid var(--border-hairline)"
            >
              <div style="display:grid; gap:2px; min-width:0">
                <span style="font:var(--type-small); display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2)">
                  {{ a.name || a.shortcode }}
                  <Badge tone="brand">{{ a.free }} free</Badge>
                  <Badge v-if="!a.isMember" tone="danger">not a member</Badge>
                </span>
                <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint); overflow-wrap:anywhere">
                  {{ a.shortcode }} · holds {{ a.held }} locker{{ a.held === 1 ? '' : 's' }}
                  · given {{ shortDate(a.givenAt) }}<template v-if="a.givenBy"> by {{ a.givenBy }}</template><template v-if="a.note"> · {{ a.note }}</template>
                </span>
              </div>
              <div style="display:flex; gap:var(--space-2); flex-shrink:0">
                <Button v-if="can('lockers.allowances.grant')" size="sm" variant="ghost" @click="changeAllowance(a)">Change</Button>
                <Button v-if="can('lockers.allowances.revoke')" size="sm" variant="ghost" @click="removeAllowance(a)">Remove</Button>
              </div>
            </div>
          </Card>
          <Card v-if="can('lockers.inventory.view')" variant="outline">
            <h2 style="margin:0 0 var(--space-3); font:var(--type-h4)">Locker administration</h2>
            <p style="font:var(--type-small); color:var(--text-muted)">{{ managedLockers.length }} lockers · {{ lockerHolders.length }} current holders</p>
            <div style="max-height:420px; overflow:auto">
              <div v-for="locker in managedLockers" :key="locker.id" style="display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); border-top:1px solid var(--border-hairline); padding:var(--space-2) 0">
                <span style="font:var(--type-small)">Locker {{ locker.number }} · {{ locker.is_active ? 'Active' : 'Disabled' }}</span>
                <Button v-if="can('lockers.inventory.edit')" size="sm" variant="ghost" :disabled="busy" @click="toggleLockerActive(locker)">{{ locker.is_active ? 'Disable' : 'Enable' }}</Button>
              </div>
            </div>
          </Card>
          <Card v-if="can('lockers.inventory.view')" variant="outline">
            <h2 style="margin:0 0 var(--space-3); font:var(--type-h4)">Current holders</h2>
            <p v-if="!lockerHolders.length" style="font:var(--type-small); color:var(--text-muted)">No lockers currently assigned.</p>
            <div v-for="holder in lockerHolders" :key="holder.bookingId" style="border-top:1px solid var(--border-hairline); padding:var(--space-2) 0; font:var(--type-small)">
              {{ holder.locker }} · {{ holder.name || holder.email }}
              <Button v-if="can('lockers.bookings.release')" size="sm" variant="ghost" :disabled="busy" @click="releaseLockerHolder(holder)">Release</Button>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================= PEOPLE ============================= -->
      <!-- ============================ WHATSAPP ============================ -->
      <template v-if="tab === 'whatsapp' && (anyPermission('whatsapp.') || anyPermission('societies.whatsapp.'))">
        <div class="admin-cols">
          <Card variant="outline">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Society WhatsApp account</h2>
            <WhatsAppLink v-if="anyPermission('whatsapp.')" />
          </Card>
          <Card v-if="can('whatsapp.post')" variant="offset">
            <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Post to WhatsApp</h2>
            <Field label="Heading"><Input v-model="whatsappHeading" placeholder="Optional heading" /></Field>
            <Field label="Message"><textarea v-model="whatsappBody" rows="7" class="ees-textarea" placeholder="Write the message…" /></Field>
            <WhatsAppGroupPicker v-model="whatsappRecipients" :disabled="busy" />
            <Button v-if="can('whatsapp.post')" :disabled="busy || !whatsappBody.trim() || !whatsappRecipients.length" @click="sendWhatsAppPost">Post to selected groups</Button>
          </Card>
        </div>
      </template>

      <WhatsAppGroupAdmin v-if="tab === 'whatsapp' && can('societies.whatsapp.groups.view')" />
      <SocietiesAdminPanel v-if="tab === 'societies'" :events="events" />

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

        <!-- The approval queue. Above the composer, because a draft here is
             something the site has already decided somebody should hear about
             and is only waiting on a person. -->
        <Card :variant="awaitingCount ? 'raised' : 'outline'" :class="{ 'pending-card': awaitingCount }" style="margin-bottom:var(--space-6)">
          <h2 style="margin:0 0 var(--space-2); font:var(--type-h4); display:flex; gap:var(--space-3); align-items:center">
            Email queue
            <Badge v-if="awaitingCount" tone="danger">{{ awaitingCount }} to approve</Badge>
          </h2>
          <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
            Emails the site writes on its own wait here — “tickets are on sale”
            is written as soon as an event is published with a sale time. Nothing
            goes until somebody approves it: send it now, or approve it for its
            time and it goes out then by itself. Email about somebody's own
            booking or sign-in still goes straight to them.
          </p>
          <p v-if="!queue.length" style="margin:var(--space-3) 0 0; font:var(--type-small); color:var(--text-faint)">
            Nothing queued.
          </p>
          <div
            v-for="q in queue"
            :key="q.id"
            class="queue-row"
          >
            <div style="display:grid; gap:var(--space-1, 4px); min-width:0">
              <strong style="font:var(--type-small); overflow-wrap:anywhere">{{ q.subject }}</strong>
              <span style="display:flex; gap:var(--space-2); align-items:center; flex-wrap:wrap; font:var(--type-2xs); color:var(--text-muted)">
                <Badge :tone="q.status === 'scheduled' ? 'accent' : 'brand'">
                  {{ q.status === 'scheduled' ? 'approved' : 'needs approval' }}
                </Badge>
                <template v-if="q.status === 'scheduled'">goes out {{ q.sendAtText }}</template>
                <template v-else-if="q.sendAtText">due {{ q.sendAtText }}</template>
              </span>
              <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
                {{ q.kindLabel }} · {{ q.recipients }} recipient(s)<template v-if="q.whatsapp"> · and WhatsApp</template> · written {{ new Date(q.createdAt).toLocaleString() }}
              </span>
              <span v-if="q.note" style="font:var(--type-2xs); color:var(--danger)">{{ q.note }}</span>
              <span v-if="q.warning" style="font:var(--type-2xs); color:var(--danger)">{{ q.warning }}</span>
            </div>
            <Button v-if="can('email.queue.view')" size="sm" :disabled="busy" @click="openReview(q.id)">Review</Button>
          </div>
        </Card>

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
                  {{ audCount(a) }}
                </span>
              </label>
            </div>

            <!-- Edit the Test list in place: it is what you tick to try a send. -->
            <div class="test-list">
              <span style="font:var(--type-label); color:var(--text-muted)">Test list</span>
              <div v-for="addr in testRecipients" :key="addr" class="test-list__row">
                <span style="font-family:var(--font-mono); font-size:var(--text-xs)">{{ addr }}</span>
                <Button v-if="can('email.test_recipients.remove')" size="sm" variant="ghost" :disabled="busy" @click="changeTestList(addr)">Remove</Button>
              </div>
              <span v-if="!testRecipients.length" style="font:var(--type-small); color:var(--text-faint)">Nobody yet.</span>
              <form v-if="can('email.test_recipients.add')" class="test-list__add" @submit.prevent="changeTestList('add')">
                <Input v-model="newTestEmail" type="email" placeholder="name@ic.ac.uk" aria-label="Add to test list" />
                <Button type="submit" size="sm" variant="secondary" :disabled="busy || !newTestEmail.includes('@')">Add</Button>
              </form>
            </div>

            <div style="display:flex; gap:var(--space-3); flex-wrap:wrap; align-items:center">
              <Button v-if="can('email.preview')" variant="secondary" :disabled="busy || !compose.audiences.length" @click="preview">
                Preview
              </Button>
              <Button v-if="can('email.send') || can('whatsapp.post')" :disabled="busy || Boolean(sendBlocked)" @click="sendEmail">
                {{ busy ? 'Working…' : 'Send' }}
              </Button>
              <span v-if="previewCount !== null" style="font:var(--type-small); color:var(--text-muted)">
                {{ audienceSummary }}
              </span>
              <span v-if="sendBlocked && compose.audiences.length && !busy" style="font:var(--type-2xs); color:var(--text-faint)">
                {{ sendBlocked }}
              </span>
            </div>
            <p v-if="previewSample.length" style="margin:var(--space-3) 0 0; font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
              e.g. {{ previewSample.join(', ') }}…
            </p>
          </Card>

          <div style="display:grid; gap:var(--space-6); align-content:start">
            <Card v-if="previewHtml && composeEmail.length" variant="outline">
              <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Preview</h2>
              <!-- Rendered by the same code that builds the real message, so
                   what is approved here is what arrives. The HTML is produced
                   server-side from an escape-first renderer — nothing a
                   committee member types reaches this as markup. -->
              <div class="mail-preview" v-html="previewHtml" />
            </Card>

            <Card v-if="can('whatsapp')" variant="outline">
              <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">WhatsApp</h2>
              <WhatsAppLink @changed="refreshAudiences" />
              <TraceRule style="margin:var(--space-5) 0" />
              <WhatsAppPost
                :heading="compose.heading || compose.subject"
                :body="compose.body"
                :open="composeWhatsApp.length > 0"
              />
            </Card>

            <Card variant="outline">
              <div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-3); flex-wrap:wrap">
                <h2 style="margin:0; font:var(--type-h4)">Sent</h2>
                <Button v-if="can('email.announce_now')" size="sm" variant="ghost" :disabled="busy" title="Send any due last-chance reminders now, instead of at the next ten-minute pass" @click="runReminderPass">
                  Run reminder pass
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
                  <span style="display:flex; gap:var(--space-2); align-items:center; flex-shrink:0">
                    <Button v-if="(c.status === 'awaiting_approval') && (can('email.queue.view'))" size="sm" variant="ghost" :disabled="busy" @click="openReview(c.id)">Review</Button>
                    <Badge :tone="campaignTone[c.status] || 'neutral'">{{ c.status.replace('_', ' ') }}</Badge>
                  </span>
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

      <template v-if="tab === 'audit' && can('access.audit.view')">
        <AuditActivityPanel />
      </template>

      <template v-if="tab === 'roles' && (can('access.roles.create') || can('access.roles.edit'))">
        <RoleManagerPanel @updated="refreshAccessPresets" />
      </template>

      <template v-if="tab === 'people'">
        <div class="admin-cols">
          <Card variant="offset">
            <h2 style="margin:0 0 var(--space-2); font:var(--type-h4)">Committee access</h2>
            <p style="margin:0 0 var(--space-4); font:var(--type-small); color:var(--text-muted)">
              <template v-if="canManageUsers || can('access.revoke')">
                A role preselects permissions; authorised managers can grant, customise or revoke access independently.
                Committee members can still edit their own card independently.
              </template>
              <template v-else>
                Who has access, and who to ask. Changes require explicit access-management permissions.
              </template>
            </p>

            <div v-for="a in admins" :key="a.email" style="border-top:1px solid var(--border-hairline); padding:var(--space-3) 0">
              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:var(--space-3)">
                <div style="display:grid; gap:3px; min-width:0">
                  <span style="font:var(--type-small)">
                    {{ [a.first_name, a.last_name].filter(Boolean).join(' ') || a.email }}
                    <Badge v-if="a.standing" tone="accent">standing</Badge>
                    <Badge v-if="a.permissions !== null && a.permissions !== undefined && !a.standing" tone="neutral">custom</Badge>
                  </span>
                  <span style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint); overflow-wrap:anywhere">{{ a.email }}</span>
                  <span style="font:var(--type-2xs); color:var(--text-muted)">{{ roleOptions.find((r) => r.id === a.role)?.label || a.role }} · {{ a.capabilities?.length ?? 0 }} permissions</span>
                </div>
                <div style="display:flex; gap:var(--space-2); flex-wrap:wrap; align-items:center">
                  <Button v-if="(canManageUsers && !a.standing && a.user_id) && (can('access.grant'))" size="sm" variant="secondary" @click="editAccess(a)">{{ editingAccessId === a.user_id ? 'Close' : 'Edit permissions' }}</Button>
                  <Button v-if="!a.standing && a.user_id && can('access.revoke')" size="sm" variant="ghost" @click="removeAdmin(a)">Remove</Button>
                </div>
              </div>
              <div v-if="editingAccessId === a.user_id && canManageUsers" style="display:grid; gap:var(--space-3); margin-top:var(--space-4)">
                <Field label="Role preset">
                  <select v-model="editingAccessRole" @change="resetEditPermissions" class="ees-select" style="width:100%; max-width:300px">
                    <option v-for="r in roleOptions" :key="r.id" :value="r.id">{{ r.label }}</option>
                  </select>
                </Field>
                <p style="font:var(--type-2xs); color:var(--text-muted); margin:0">Changing the role preselects its default permissions. Untick or tick any permission to customise this person’s access.</p>
                <PermissionSelector v-model="editingPermissions" :permissions="permissionOptions" />
                <div style="display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2)">
                  <Button v-if="can('access.grant')" size="sm" :disabled="busy" @click="saveAccess(a)">Save access</Button>
                  <Button size="sm" variant="ghost" @click="resetEditPermissions">Reset to role defaults</Button>
                  <span style="font:var(--type-2xs); color:var(--text-faint)">{{ editingPermissions.length }} selected · {{ isPreset(editingAccessRole, editingPermissions) ? 'Role defaults' : 'Custom selection' }}</span>
                </div>
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
              <div style="display:grid; gap:var(--space-3); margin-top:var(--space-4)">
                <PermissionSelector v-model="newPermissions" :permissions="permissionOptions" />
                <div style="display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2)">
                  <Button v-if="can('access.grant')" size="sm" :disabled="!newAdminEmail.trim() || busy" @click="addAdmin">Grant access</Button>
                  <Button size="sm" variant="ghost" @click="resetNewPermissions">Reset to role defaults</Button>
                  <span style="font:var(--type-2xs); color:var(--text-faint)">{{ newPermissions.length }} permissions selected</span>
                </div>
              </div>
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
            <Button v-if="can('access.impersonate')"
              size="sm"
              variant="ghost"
              style="margin-top:var(--space-3)"
              :disabled="busy || !impersonateEmail.trim()"
              @click="impersonate"
            >Sign in as them</Button>
          </Card>

          <Card v-if="can('passkey_links')" variant="outline">
            <h2 style="margin:0 0 var(--space-2); font:var(--type-h4)">Passkey setup links</h2>
            <p style="margin:0 0 var(--space-4); font:var(--type-small); color:var(--text-muted)">
              For somebody our sign-in codes never reach — a sponsor whose spam
              filter eats them. The link lets them save a passkey on that
              address's account and sign in without a code. Nothing is emailed:
              send it yourself, by whatever does reach them. It works once<template v-if="passkeyLinkTtlHours">
              and lasts {{ Math.round(passkeyLinkTtlHours / 24) }} days</template>.
              <template v-if="!canManageUsers">Accounts with committee access need a superuser.</template>
            </p>
            <Field label="Email" help="If there is no account yet, one is made — as when granting sponsor access.">
              <Input v-model="passkeyLinkEmail" placeholder="name@company.com" />
            </Field>
            <Button v-if="can('passkeys.create')"
              size="sm"
              style="margin-top:var(--space-3)"
              :disabled="busy || !passkeyLinkEmail.trim()"
              @click="makeLinkFromForm"
            >Make a link</Button>
            <PasskeyLinkMade v-if="lastMadeLink" :link="lastMadeLink" />

            <template v-if="passkeyLinks.length">
              <TraceRule style="margin:var(--space-5) 0 var(--space-2)" />
              <div
                v-for="l in passkeyLinks"
                :key="l.id"
                style="display:flex; justify-content:space-between; align-items:center; gap:var(--space-3); padding:var(--space-2) 0; border-top:1px solid var(--border-hairline)"
              >
                <div style="display:grid; gap:2px; min-width:0">
                  <span style="font:var(--type-2xs); overflow-wrap:anywhere">
                    {{ l.email }}
                    <Badge :tone="l.status === 'live' ? 'accent' : l.status === 'used' ? 'success' : 'neutral'">{{ l.status }}</Badge>
                  </span>
                  <span style="font:var(--type-2xs); color:var(--text-faint)">
                    {{ linkStatus(l) }}<template v-if="l.createdBy"> · made by {{ l.createdBy }}</template>
                  </span>
                </div>
                <Button v-if="(l.status === 'live') && (can('passkeys.revoke'))" size="sm" variant="ghost" @click="revokePasskeyLink(l)">Revoke</Button>
              </div>
            </template>
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
              <WhatsAppGroupPicker v-if="can('whatsapp')" v-model="digestWhatsApp" :disabled="busy" />
              <div style="display:flex; gap:var(--space-3); align-items:center; flex-wrap:wrap">
                <Button v-if="can('sponsors.digest.send') && can('email.send')" size="sm" :disabled="busy" @click="sendRolesDigest">
                  Email {{ rolesDigest.recipients }} jobs subscriber(s)<template v-if="digestWhatsApp.length"> + WhatsApp</template>
                </Button>
                <span style="font:var(--type-2xs); color:var(--text-faint)">
                  <template v-if="can('email')">One email listing every new role, to members who opted in to jobs and internships.</template>
                  <template v-else>Sending needs a committee member with email access.</template>
                </span>
              </div>
            </div>
          </div>
        </Card>

        <Card v-if="sponsorPackage.length" variant="outline" style="margin-bottom:var(--space-6)">
          <div style="display:grid; gap:var(--space-4)">
            <h2 style="margin:0; font:var(--type-h4)">Standard package</h2>
            <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
              What every sponsor gets a year unless their agreement page gives them
              their own number. Changing one here moves every sponsor on the
              standard at once — in their portal and in what it will let them ask for.
            </p>
            <div class="package-grid">
              <label v-for="c in sponsorPackage" :key="c.key" class="package-row">
                <span style="font:var(--type-small)">{{ c.label }}</span>
                <span style="display:flex; gap:var(--space-2); align-items:center">
                  <input
                    v-model.number="packageEdits[c.key]"
                    type="number"
                    min="0"
                    step="1"
                    class="ees-input"
                    style="width:5.5em"
                    :aria-label="`${c.label}: allowance a year`"
                  />
                  <Button
                    size="sm"
                    variant="secondary"
                    :disabled="busy || packageEdits[c.key] === c.allowance"
                    @click.prevent="savePackageAllowance(c)"
                  >Save</Button>
                </span>
              </label>
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
                  <Button v-if="can('sponsors.reorder')" size="sm" variant="ghost" @click="moveSponsor(sp, -1)">↑</Button>
                  <Button v-if="can('sponsors.reorder')" size="sm" variant="ghost" @click="moveSponsor(sp, 1)">↓</Button>
                  <Button v-if="can('sponsors.edit')" size="sm" variant="ghost" @click="toggleSponsorActive(sp)">{{ sp.active ? 'Unpublish' : 'Publish' }}</Button>
                  <Button size="sm" variant="secondary" @click="router.push(`/admin/sponsors/${sp.id}`)">Agreement &amp; activity</Button>
                  <Button v-if="can('sponsors.edit')" size="sm" variant="ghost" @click="editSponsor(sp)">Edit</Button>
                  <Button v-if="can('sponsors.delete')" size="sm" variant="danger" @click="removeSponsor(sp)">Delete</Button>
                </div>
              </div>

              <!-- Several people can hold access to one sponsor. -->
              <div style="margin-top:var(--space-4); padding-top:var(--space-3); border-top:1px solid var(--border-hairline)">
                <span style="font-family:var(--font-mono); font-size:var(--text-2xs); text-transform:uppercase; color:var(--text-faint)">
                  Portal access
                </span>
                <div v-for="u in sp.users" :key="u.id" style="padding:var(--space-2) 0">
                  <div style="display:flex; justify-content:space-between; align-items:center; gap:var(--space-2)">
                    <span style="font:var(--type-2xs); overflow-wrap:anywhere">{{ u.email }}</span>
                    <div style="display:flex; gap:4px; flex-shrink:0">
                      <!-- For a contact whose mail server eats our sign-in codes. -->
                      <Button
                        v-if="(can('passkey_links')) && (can('passkeys.create'))"
                        size="sm"
                        variant="ghost"
                        :disabled="busy"
                        title="Make a link that lets them sign in with a passkey, no emailed code"
                        @click="makePasskeyLink(u.email)"
                      >Passkey link</Button>
                      <Button v-if="can('sponsors.users.revoke')" size="sm" variant="ghost" @click="revokeSponsorAccess(sp, u)">Revoke</Button>
                    </div>
                  </div>
                  <PasskeyLinkMade v-if="madeLinks[u.email.toLowerCase()]" :link="madeLinks[u.email.toLowerCase()]" />
                </div>
                <p v-if="!sp.users?.length" style="margin:var(--space-2) 0; font:var(--type-2xs); color:var(--text-faint)">
                  Nobody can log in for this sponsor yet.
                </p>
                <div style="display:flex; gap:var(--space-2); align-items:flex-end">
                  <Field label="Add an email" style="flex:1">
                    <Input v-model="sponsorUserEmail[sp.id]" placeholder="name@company.com" />
                  </Field>
                  <Button v-if="can('sponsors.users.grant')" size="sm" @click="grantSponsorAccess(sp)">Grant</Button>
                </div>
                <p style="margin:var(--space-2) 0 0; font:var(--type-2xs); color:var(--text-faint)">
                  They sign in with that address using the usual emailed code, which is
                  what proves they control it.<template v-if="can('passkey_links')"> If our codes
                  never arrive, send them a passkey link instead.</template>
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
                <Button v-if="editingSponsor ? can('sponsors.edit') : can('sponsors.create')" :disabled="busy" @click="saveSponsor">{{ editingSponsor ? 'Save' : 'Add sponsor' }}</Button>
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
         reminder cannot advertise last week's room. The WhatsApp post is built
         from the same words and the same event details. -->
    <Dialog
      :open="Boolean(announcing)"
      :title="announcing ? `Announce ${announcing.title}` : ''"
      width="720px"
      @close="closeCompose"
    >
      <div v-if="announcing" style="display:grid; gap:var(--space-4)">
        <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
          Write what you want to say, then email it, post it on WhatsApp, or
          both. The date, venue, ticket prices and a link to the event page are
          added underneath automatically.
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
            <span class="aud__count">{{ audCount(a) }}</span>
          </label>
        </div>

        <div v-if="previewHtml && composeEmail.length" class="mail-preview" v-html="previewHtml" />
        <p v-if="error" style="margin:0; font:var(--type-small); color:var(--danger)">{{ error }}</p>

        <TraceRule />
        <WhatsAppPost
          :heading="compose.heading || compose.subject"
          :body="compose.body"
          :event-id="announcing.id"
          :open="composeWhatsApp.length > 0"
        />
      </div>

      <template #footer>
        <span style="font:var(--type-small); color:var(--text-muted); margin-right:auto">
          <template v-if="previewCount !== null">{{ audienceSummary }}</template>
          <template v-else-if="sendBlocked && compose.audiences.length">{{ sendBlocked }}</template>
        </span>
        <Button variant="ghost" @click="closeCompose">Cancel</Button>
        <Button v-if="can('email.preview')" variant="secondary" :disabled="busy || !compose.audiences.length" @click="preview">
          Preview
        </Button>
        <Button v-if="can('email.send') || can('whatsapp.post') || can('email.announce_event')" :disabled="busy || Boolean(sendBlocked)" :title="sendBlocked || undefined" @click="sendEmail">
          {{ busy ? 'Sending…' : 'Send' }}
        </Button>
      </template>
    </Dialog>

    <!-- Reviewing a draft from the approval queue. Everything on it is worked
         out by the server from the saved draft — the recipient list by
         resolving the ticked audiences, the preview by the same builder that
         sends — so what is approved here is what goes. -->
    <Dialog
      :open="Boolean(reviewing)"
      :title="reviewing ? `Review: ${reviewing.kindLabel}` : ''"
      width="760px"
      @close="closeReview"
    >
      <div v-if="reviewing" style="display:grid; gap:var(--space-4)">
        <p v-if="isScheduled" class="review-scheduled">
          Approved — it goes out <strong>{{ reviewing.sendAtText }}</strong> by itself.
          Take it off the schedule to change anything.
        </p>
        <p v-if="reviewing.note" class="review-warning">{{ reviewing.note }}</p>
        <p v-if="reviewing.warning" class="review-warning">{{ reviewing.warning }}</p>
        <p v-if="reviewing.event" style="margin:0; font:var(--type-small); color:var(--text-muted)">
          About <RouterLink :to="`/events/${reviewing.event.slug}`">{{ reviewing.event.title }}</RouterLink>.
          <template v-if="reviewing.kind === 'on_sale' && reviewing.sendAtText">Sales open {{ reviewing.sendAtText }}; this follows if the sale moves.</template>
          Nothing has been sent.
          <template v-if="!reviewing.editedAt && reviewing.kind === 'on_sale'">Until somebody edits it, it is rewritten when the event changes.</template>
        </p>

        <Field label="Subject"><Input v-model="mailDraft.subject" :disabled="isScheduled" /></Field>
        <Field label="Heading" help="The big line inside the email. Blank uses the subject.">
          <Input v-model="mailDraft.heading" :disabled="isScheduled" />
        </Field>
        <Field
          :label="reviewing.event ? 'Opening' : 'Body'"
          :help="reviewing.event
            ? 'What comes first. The date, venue, prices and booking link are added underneath from the event when it is sent, so they are always current. **bold**, - bullets, [link](https://…).'
            : 'Blank line between paragraphs. **bold**, - bullets, ## heading, [link](https://…).'"
        >
          <textarea v-model="mailDraft.body" rows="5" class="ees-textarea" :disabled="isScheduled" />
        </Field>
        <div v-if="!isScheduled" style="display:flex; gap:var(--space-3); align-items:center">
          <Button v-if="can('email.queue.edit')" size="sm" variant="secondary" :disabled="busy || !draftDirty" @click="draftAction(saveDraft)">
            Save and update the preview
          </Button>
          <span v-if="draftDirty" style="font:var(--type-2xs); color:var(--text-muted)">Unsaved changes.</span>
        </div>

        <p v-if="reviewing.previewError" style="margin:0; font:var(--type-small); color:var(--danger)">{{ reviewing.previewError }}</p>
        <div v-if="reviewing.preview" style="display:grid; gap:var(--space-2)">
          <span style="font:var(--type-label); color:var(--text-muted)">As it will arrive · “{{ reviewing.preview.subject }}”</span>
          <div class="mail-preview" v-html="reviewing.preview.html" />
        </div>

        <WhatsAppPost
          :heading="mailDraft.heading || mailDraft.subject"
          :body="mailDraft.body"
          :event-id="reviewing.event?.id ?? null"
          :kind="reviewing.kind"
          :open="draftWhatsApp.length > 0"
          :posted="reviewing.whatsappPosts || []"
        />

        <TraceRule />
        <span style="font:var(--type-label); color:var(--text-muted)">
          Who gets it — {{ reviewing.count }} recipient(s), each BCC'd<template v-if="draftWhatsApp.length">,
          and {{ draftWhatsApp.join(', ') }} on WhatsApp</template>
        </span>
        <div v-for="[group, items] in draftGroups" :key="group">
          <span style="font:var(--type-2xs); color:var(--text-faint)">{{ group }}</span>
          <label v-for="a in items" :key="a.key" class="aud">
            <input
              type="checkbox"
              :checked="mailDraft.audiences.includes(a.key)"
              :disabled="busy || isScheduled"
              @change="toggleDraftAudience(a.key)"
            />
            <span>
              <strong class="aud__label">{{ a.label }}</strong>
              <span class="aud__desc">{{ a.description }}</span>
            </span>
            <span class="aud__count">{{ audCount(a) }}</span>
          </label>
        </div>

        <form v-if="!isScheduled" class="test-list__add" @submit.prevent="addRecipients">
          <Input v-model="addAddresses" placeholder="Add addresses — name@ic.ac.uk, another@ic.ac.uk" aria-label="Add recipients" />
          <Button type="submit" size="sm" variant="secondary" :disabled="busy || !addAddresses.includes('@')">Add</Button>
        </form>
        <p v-if="reviewing.skipped.length" style="margin:0; font:var(--type-2xs); color:var(--text-muted)">
          Not added, because they have unsubscribed from everything:
          {{ reviewing.skipped.join(', ') }}
        </p>

        <div class="recipients">
          <Input v-if="reviewing.count > 10" v-model="recipientFilter" placeholder="Find an address" aria-label="Find a recipient" />
          <div v-for="r in shownRecipients" :key="r.email" class="recipients__row">
            <span style="font-family:var(--font-mono); font-size:var(--text-xs); overflow-wrap:anywhere">{{ r.email }}</span>
            <span class="recipients__source">
              {{ r.list ? 'department list' : (sourceLabels[r.source] || r.source) }}
            </span>
            <Button v-if="can('email.queue.edit')" size="sm" variant="ghost" :disabled="busy || isScheduled" @click="removeRecipient(r.email)">Remove</Button>
          </div>
          <span v-if="!reviewing.count" style="font:var(--type-small); color:var(--text-faint)">
            Nobody yet — tick an audience above or add an address.
          </span>
          <span v-else-if="!recipientFilter.trim() && shownRecipients.length < reviewing.count" style="font:var(--type-2xs); color:var(--text-faint)">
            Showing {{ shownRecipients.length }} of {{ reviewing.count }} — search to find anybody else.
          </span>
        </div>

        <div v-if="mailDraft.excluded.length" style="display:grid; gap:var(--space-2)">
          <span style="font:var(--type-2xs); color:var(--text-faint)">Taken off by hand</span>
          <div v-for="email in mailDraft.excluded" :key="email" class="recipients__row">
            <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted); text-decoration:line-through">{{ email }}</span>
            <span />
            <Button v-if="can('email.queue.edit')" size="sm" variant="ghost" :disabled="busy || isScheduled" @click="restoreRecipient(email)">Put back</Button>
          </div>
        </div>

        <p v-if="reviewNotice" style="margin:0; font:var(--type-small); color:var(--text-muted)">{{ reviewNotice }}</p>
        <p v-if="reviewError" style="margin:0; font:var(--type-small); color:var(--danger)">{{ reviewError }}</p>
      </div>

      <template #footer>
        <!-- Wraps on a phone: three buttons do not fit 390px in one row, and
             the dialog's own footer does not wrap. -->
        <div class="review-actions">
          <Button v-if="can('email.queue.dismiss')" variant="ghost" :disabled="busy" @click="dismissDraft">Dismiss</Button>
          <span class="review-actions__gap" />
          <Button v-if="can('email.queue.test')" variant="secondary" :disabled="busy" title="Send it, as it stands, to the Test list only" @click="testDraft">
            Send a test
          </Button>
          <template v-if="isScheduled">
            <Button v-if="can('email.queue.unschedule')" variant="secondary" :disabled="busy" @click="unscheduleDraft">Take off schedule</Button>
            <Button v-if="can('email.queue.send')" variant="secondary" :disabled="busy || !(reviewing?.count || draftWhatsApp.length)" @click="sendNowDraft">Send now</Button>
          </template>
          <template v-else-if="canSchedule">
            <Button v-if="can('email.queue.send')" variant="secondary" :disabled="busy || !(reviewing?.count || draftWhatsApp.length) || !mailDraft.subject.trim()" @click="sendNowDraft">
              Send now
            </Button>
            <Button v-if="can('email.queue.schedule')" :disabled="busy || !(reviewing?.count || draftWhatsApp.length) || !mailDraft.subject.trim()" @click="scheduleDraft">
              {{ busy ? 'Working…' : `Approve for ${reviewing.sendAtText}` }}
            </Button>
          </template>
          <Button v-else-if="can('email.queue.send')" :disabled="busy || !(reviewing?.count || draftWhatsApp.length) || !mailDraft.subject.trim()" @click="sendNowDraft">
            {{ busy ? 'Working…' : `Send to ${reviewing?.count ?? 0}` }}
          </Button>
        </div>
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

/* The event list previously had an implicit auto-sized grid track.
   Long event titles, badges, and the inline select forced that track over
   500px wide on phones, even though the editor itself could fit. The admin
   frame clipped the excess, making it look as though the form was spilling. */
.admin-event-list { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--space-3); min-width: 0; }
.admin-event-list__heading { min-width: 0; display: flex; justify-content: space-between; align-items: baseline; gap: var(--space-3); flex-wrap: wrap; }
.admin-event-list__heading h2 { font: var(--type-h4); margin: 0 0 var(--space-1); }
.admin-event-list__heading span { font: var(--type-2xs); color: var(--text-muted); }
.admin-event-list__empty { font: var(--type-small); color: var(--text-muted); margin: 0; }
.admin-event-list > .ees-input { max-width: 100%; min-width: 0; }
.admin-event-card { min-width: 0; max-width: 100%; }
.admin-event-card__layout { display: flex; flex-wrap: wrap; gap: var(--space-4); justify-content: space-between; align-items: center; min-width: 0; }
.admin-event-card__summary { flex: 1 1 180px; min-width: 0; overflow-wrap: anywhere; }
.admin-event-card__badges { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); min-width: 0; }
.admin-event-card__badges > * { min-width: 0; max-width: 100%; overflow-wrap: anywhere; }
.admin-event-card__controls { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); min-width: 0; max-width: 100%; }
.admin-event-card__controls > *, .admin-event-card__controls label { max-width: 100%; min-width: 0; }
.admin-event-card__controls label { flex-wrap: wrap; overflow-wrap: anywhere; }
.admin-event-card__controls .ees-select { min-width: 0; max-width: 100%; }
.admin-event-list__pages { min-width: 0; display: flex; justify-content: space-between; align-items: center; gap: var(--space-2); }
.admin-event-list__pages span { font: var(--type-2xs); color: var(--text-muted); text-align: center; }

/* The event editor sits beside the event list on desktop, but is a single
   column on phones. Explicit minmax(0, ...) tracks and shrinkable children
   prevent form controls (particularly datetime-local and ticket fields) from
   imposing a ~500px min-content width inside a ~300px card. */
.event-editor { min-width: 0; }
.event-editor__form { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--space-4); min-width: 0; }
.event-editor__form > *,
.event-editor .ees-field { min-width: 0; }
.event-editor .ees-input,
.event-editor .ees-select,
.event-editor .ees-textarea { min-width: 0; max-width: 100%; }
.event-editor__pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
.event-editor__file { min-width: 0; max-width: 100%; font-size: var(--text-xs); }
.event-editor__ticket { display: grid; min-width: 0; gap: var(--space-2); padding: var(--space-3) 0; border-top: 1px solid var(--border-hairline); }
.event-editor__ticket-fields { display: grid; grid-template-columns: minmax(0, 2fr) repeat(3, minmax(0, 1fr)); gap: var(--space-2); }
.event-editor__actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }


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

@media (max-width: 680px) {
  /* Preserve usable editing space rather than spending 64px on page gutters. */
  .admin { padding: var(--space-6) var(--space-3); }
  .event-editor.ees-card__pad { padding: var(--space-4); }
  .event-editor__pair { grid-template-columns: minmax(0, 1fr); }
  .event-editor__ticket-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .event-editor__ticket-fields > :nth-child(-n+2) { grid-column: 1 / -1; }
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
.package-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--space-3) var(--space-6);
}
.package-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--border-hairline);
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

/* One draft in the approval queue: what it is on the left, Review on the right. */
.queue-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-4);
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--border-faint);
}

.review-scheduled {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-left: 4px solid var(--action-primary);
  background: var(--surface-brand-subtle);
  font: var(--type-small);
  color: var(--text-strong);
}

.review-warning {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-left: 4px solid var(--danger);
  background: var(--surface-sunken, transparent);
  font: var(--type-small);
  color: var(--text-strong);
}

/* The resolved recipient list. Scrolls inside the dialog rather than making
   the dialog a page long; the count above it is the real total. */
.recipients {
  display: grid;
  gap: var(--space-2);
  max-height: 320px;
  overflow: auto;
  padding: var(--space-3);
  border: var(--border-width-hairline) solid var(--border-default);
  border-radius: var(--radius-sm);
}

.recipients__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: var(--space-3);
  align-items: center;
}

.recipients__source {
  font: var(--type-2xs);
  color: var(--text-faint);
  white-space: nowrap;
}

.review-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
}

.review-actions__gap {
  margin-right: auto;
}

@media (max-width: 560px) {
  .recipients__row {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .recipients__source {
    display: none;
  }
}
</style>
