<script setup>
// The door. Any committee member can open it on a phone, pick tonight's event,
// and either scan tickets or find a name and let them in.
//
// Several phones work the same door at once, so every screen polls the
// event's check-in state every couple of seconds: somebody let in on one phone
// shows as in on the others almost immediately, and a ticket scanned twice is
// reported as already in, with who let it in.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePageTitle } from '@/utils/pageTitle'

import api from '@/api'
import { can } from '@/stores/session'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Input from '@/components/ds/Input.vue'

const route = useRoute()
const router = useRouter()
const eventId = computed(() => (route.params.eventId ? Number(route.params.eventId) : null))

const events = ref([])
const event = ref(null)
const attendees = ref([])
const bookingsFingerprint = ref(null)
const error = ref('')
const loading = ref(false)
const busy = ref(false)

// ------------------------------------------------------------ live sync
const online = ref(true)
const lastSync = ref(null)
const now = ref(Date.now())
let pollTimer = null
let clockTimer = null

const syncedAgo = computed(() => {
  if (!lastSync.value) return ''
  const s = Math.max(0, Math.round((now.value - lastSync.value) / 1000))
  return s < 2 ? 'just now' : `${s}s ago`
})

async function loadEvents() {
  loading.value = true
  try {
    events.value = (await api.get('/checkin/events')).events
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadEvent() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/checkin/events/${eventId.value}`)
    event.value = data.event
    attendees.value = data.attendees
    bookingsFingerprint.value = data.bookings
    online.value = true
    lastSync.value = Date.now()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

/** Fold the latest check-in state into the list we already have. */
function apply(state) {
  const byId = new Map(state.checkins.map((c) => [c.id, c]))
  for (const a of attendees.value) {
    const c = byId.get(a.id)
    a.checkedInAt = c ? c.at : null
    a.checkedInBy = c ? c.by : null
    a.method = c ? c.method : null
  }
}

async function poll() {
  clearTimeout(pollTimer)
  if (!eventId.value) return
  try {
    const state = await api.get(`/checkin/events/${eventId.value}/state`)
    if (state.bookings !== bookingsFingerprint.value) {
      // Somebody booked, cancelled or was confirmed: the list itself changed.
      await loadEvent()
    }
    apply(state)
    online.value = true
    lastSync.value = Date.now()
  } catch {
    online.value = false
  } finally {
    // Slower when the tab is in the background, so a forgotten phone in a
    // pocket is not hammering the server all night.
    pollTimer = setTimeout(poll, document.hidden ? 10000 : 2000)
  }
}

// ------------------------------------------------------------ the list
const query = ref('')
const show = ref('out')

const counts = computed(() => {
  const live = attendees.value
  const people = (list) => list.reduce((n, a) => n + (a.quantity || 1), 0)
  return {
    in: people(live.filter((a) => a.checkedInAt)),
    booked: people(live.filter((a) => a.status === 'confirmed' || a.status === 'held')),
    out: live.filter((a) => !a.checkedInAt).length,
    inRows: live.filter((a) => a.checkedInAt).length,
    all: live.length,
  }
})

const visible = computed(() => {
  const term = query.value.trim().toLowerCase()
  let list = attendees.value
  if (show.value === 'out') list = list.filter((a) => !a.checkedInAt)
  if (show.value === 'in') list = list.filter((a) => a.checkedInAt)
  if (term) {
    list = list.filter((a) =>
      [a.name, a.email, a.shortcode, a.orderNumber, a.ticketType]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(term)),
    )
  }
  return list
})

function time(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

function upsert(attendee) {
  const i = attendees.value.findIndex((a) => a.id === attendee.id)
  if (i >= 0) attendees.value[i] = { ...attendees.value[i], ...attendee }
  else attendees.value.push(attendee)
}

async function checkIn(a, override = false) {
  if (!override && !a.paid) {
    if (!can('events.checkin.override')) {
      error.value = 'Your access does not include overriding unpaid tickets.'
      return
    }
    if (!confirm(`${a.name}'s ${a.ticketType} booking is not paid. Let them in anyway?`)) return
    override = true
  }
  busy.value = true
  error.value = ''
  try {
    const res = await api.post(`/checkin/reservations/${a.id}`, { event_id: eventId.value, override })
    upsert(res.attendee)
    // Every check-in from the list flashes the panel too, camera or not.
    flashResult(res)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
    poll()
  }
}

async function undo(a) {
  if (!confirm(`Undo ${a.name}'s check-in?`)) return
  busy.value = true
  try {
    await api.del(`/checkin/reservations/${a.id}`)
    upsert({ id: a.id, checkedInAt: null, checkedInBy: null, method: null })
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
    poll()
  }
}

// ------------------------------------------------------------ the scanner
// The browser's own BarcodeDetector where there is one (Chrome on Android is
// quick and battery-friendly with it); jsQR on a canvas everywhere else —
// notably iPhones. jsQR is loaded only when needed, so it never weighs on the
// rest of the site.
const scanning = ref(false)
const video = ref(null)
const canvas = document.createElement('canvas')
let stream = null
let detector = null
let jsQR = null
let paused = false
let lastText = ''
let lastAt = 0
let scanTimer = null
let wakeLock = null

const result = ref(null)
let resultTimer = null

async function startScan() {
  error.value = ''
  result.value = null
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' } },
      audio: false,
    })
  } catch (e) {
    error.value =
      e?.name === 'NotAllowedError'
        ? 'Camera access was refused. Allow it in the browser settings, or use the list below.'
        : 'No camera available. Use the list below.'
    return
  }
  scanning.value = true
  await new Promise((r) => requestAnimationFrame(r))
  video.value.srcObject = stream
  await video.value.play().catch(() => {})
  if ('BarcodeDetector' in window) {
    try {
      detector = new window.BarcodeDetector({ formats: ['qr_code'] })
    } catch {
      detector = null
    }
  }
  if (!detector && !jsQR) jsQR = (await import('jsqr')).default
  try {
    wakeLock = await navigator.wakeLock?.request('screen')
  } catch {
    /* not available — the screen may dim, nothing worse */
  }
  paused = false
  tick()
}

function stopScan() {
  scanning.value = false
  clearTimeout(scanTimer)
  stream?.getTracks().forEach((t) => t.stop())
  stream = null
  wakeLock?.release?.().catch(() => {})
  wakeLock = null
}

async function decode() {
  const v = video.value
  if (!v || v.readyState < 2) return null
  if (detector) {
    try {
      const codes = await detector.detect(v)
      return codes[0]?.rawValue ?? null
    } catch {
      detector = null
      jsQR = jsQR || (await import('jsqr')).default
    }
  }
  // Scaled down before decoding: a phone camera frame is far more pixels than
  // a QR needs, and jsQR's cost grows with every one of them.
  const scale = Math.min(1, 640 / (v.videoWidth || 640))
  canvas.width = Math.round((v.videoWidth || 640) * scale)
  canvas.height = Math.round((v.videoHeight || 480) * scale)
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(v, 0, 0, canvas.width, canvas.height)
  const img = ctx.getImageData(0, 0, canvas.width, canvas.height)
  return jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' })?.data ?? null
}

async function tick() {
  if (!scanning.value) return
  if (!paused) {
    const text = await decode()
    // The same code stays in frame for a while after it is read; ignore it
    // until it has been out of sight for a few seconds.
    if (text && !(text === lastText && Date.now() - lastAt < 4000)) {
      lastText = text
      lastAt = Date.now()
      await handleScan(text)
    } else if (text) {
      lastAt = Date.now()
    }
  }
  scanTimer = setTimeout(tick, 120)
}

async function handleScan(text) {
  paused = true
  try {
    const res = await api.post('/checkin/scan', { token: text, event_id: eventId.value })
    // Somebody else's event's ticket must not join this event's list.
    if (res.attendee?.eventId === eventId.value) upsert(res.attendee)
    flashResult(res)
  } catch (e) {
    flashResult({ outcome: 'error', message: e.message })
  } finally {
    poll()
  }
}

const TONES = {
  checked_in: 'ok',
  already: 'warn',
  unpaid: 'bad',
  invalid: 'bad',
  wrong_event: 'bad',
  error: 'bad',
}

function flashResult(res) {
  result.value = res
  navigator.vibrate?.(res.outcome === 'checked_in' ? 80 : [60, 60, 60])
  clearTimeout(resultTimer)
  // A good result clears itself — long enough to read the name and type — so
  // the line keeps moving; anything that needs a decision stays until
  // somebody makes it.
  if (res.outcome === 'checked_in' || res.outcome === 'already') {
    resultTimer = setTimeout(dismissResult, 3500)
  }
}

function dismissResult() {
  result.value = null
  paused = false
}

async function letInAnyway() {
  const a = result.value?.attendee
  if (!a) return
  busy.value = true
  try {
    const res = await api.post(`/checkin/reservations/${a.id}`, { event_id: eventId.value, override: true })
    upsert(res.attendee)
    flashResult(res.outcome === 'checked_in' ? { ...res, message: 'Let in by override.' } : res)
  } catch (e) {
    flashResult({ outcome: 'error', message: e.message })
  } finally {
    busy.value = false
    poll()
  }
}

// ------------------------------------------------------------ lifecycle
async function enter() {
  stopScan()
  clearTimeout(pollTimer)
  event.value = null
  attendees.value = []
  if (eventId.value) {
    await loadEvent()
    poll()
  } else {
    await loadEvents()
  }
}

watch(eventId, enter)
onMounted(() => {
  enter()
  clockTimer = setInterval(() => (now.value = Date.now()), 1000)
  document.addEventListener('visibilitychange', poll)
})
onBeforeUnmount(() => {
  stopScan()
  clearTimeout(pollTimer)
  clearInterval(clockTimer)
  clearTimeout(resultTimer)
  document.removeEventListener('visibilitychange', poll)
})
usePageTitle(computed(() => event.value?.title ? `${event.value.title} · Check-in` : 'Door check-in'))
</script>

<template>
  <main class="door">
    <!-- ======================== pick an event ======================== -->
    <template v-if="!eventId">
      <header class="door__head">
        <h1 class="door__title">Door check-in</h1>
        <p class="muted">Pick the event you are letting people into.</p>
      </header>
      <p v-if="loading" class="muted">Loading…</p>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="!loading && !events.length" class="muted">No events in the next two months.</p>
      <div class="event-list">
        <Card
          v-for="e in events"
          :key="e.id"
          as="button"
          type="button"
          variant="outline"
          interactive
          class="event-card"
          @click="router.push(`/checkin/${e.id}`)"
        >
          <div style="display:grid; gap:4px; text-align:left">
            <div style="display:flex; gap:var(--space-2); align-items:center; flex-wrap:wrap">
              <strong style="font:var(--type-h5)">{{ e.title }}</strong>
              <Badge v-if="e.today" tone="success">Today</Badge>
              <Badge v-if="!e.published" tone="neutral">Draft</Badge>
            </div>
            <span class="muted">{{ e.date }} · {{ e.time }}<template v-if="e.venue"> · {{ e.venue }}</template></span>
            <span class="count-line">{{ e.checkedIn }} in · {{ e.booked }} booked</span>
          </div>
        </Card>
      </div>
    </template>

    <!-- ======================== one event ======================== -->
    <template v-else>
      <RouterLink to="/checkin" class="back">← Events</RouterLink>
      <header v-if="event" class="door__head">
        <h1 class="door__title">{{ event.title }}</h1>
        <p class="muted">{{ event.date }} · {{ event.time }}<template v-if="event.venue"> · {{ event.venue }}</template></p>
        <div class="tally">
          <span class="tally__in">{{ counts.in }}</span>
          <span class="tally__of">in of {{ counts.booked }} booked</span>
          <span class="live" :class="{ 'is-off': !online }">
            <span class="live__dot" />
            {{ online ? `Live · ${syncedAgo}` : 'Offline — retrying' }}
          </span>
        </div>
      </header>
      <p v-if="error" class="error">{{ error }}</p>

      <!-- Scanner -->
      <div class="scan">
        <Button v-if="(!scanning) && (can('events.checkin.scan'))" block size="lg" @click="startScan">Scan tickets</Button>
        <template v-else>
          <div class="scan__frame">
            <video ref="video" class="scan__video" playsinline muted />
            <div class="scan__target" />
          </div>
          <Button block variant="secondary" @click="stopScan">Stop scanning</Button>
        </template>
        <!-- The one result panel: an overlay covering the camera while scanning,
             a standalone panel above the list the rest of the time. -->
        <div
          v-if="result"
          class="scan__result"
          :class="[`is-${TONES[result.outcome]}`, { 'is-overlay': scanning }]"
          @click="dismissResult"
        >
          <strong class="scan__headline">
            {{ {
              checked_in: 'Checked in',
              already: 'Already in',
              unpaid: 'Not paid',
              invalid: 'Not valid',
              wrong_event: 'Wrong event',
              error: 'Not a ticket',
            }[result.outcome] }}
          </strong>
          <template v-if="result.attendee">
            <span class="scan__name">{{ result.attendee.name }}</span>
            <span class="scan__ticket">
              {{ result.attendee.ticketType }}<template v-if="result.attendee.quantity > 1"> · × {{ result.attendee.quantity }} people</template>
            </span>
          </template>
          <span v-if="result.message" class="scan__msg">{{ result.message }}</span>
          <div v-if="result.outcome === 'unpaid' || result.outcome === 'invalid'" class="scan__actions" @click.stop>
            <Button v-if="can('events.checkin.override')" size="sm" variant="secondary" :disabled="busy" @click="letInAnyway">Let in anyway</Button>
            <Button size="sm" variant="ghost" @click="dismissResult">Don't</Button>
          </div>
          <span v-else class="scan__hint">Tap to scan the next one</span>
        </div>
      </div>

      <!-- List -->
      <div class="list-tools">
        <Input v-model="query" placeholder="Search name, email, shortcode…" />
        <div class="chips">
          <button type="button" class="chip" :class="{ 'is-active': show === 'out' }" @click="show = 'out'">Not in <span>{{ counts.out }}</span></button>
          <button type="button" class="chip" :class="{ 'is-active': show === 'in' }" @click="show = 'in'">In <span>{{ counts.inRows }}</span></button>
          <button type="button" class="chip" :class="{ 'is-active': show === 'all' }" @click="show = 'all'">All <span>{{ counts.all }}</span></button>
        </div>
      </div>

      <p v-if="loading && !attendees.length" class="muted">Loading…</p>
      <ul class="people">
        <li v-for="a in visible" :key="a.id" class="person" :class="{ 'is-in': a.checkedInAt }">
          <div class="person__who">
            <span class="person__name">{{ a.name }}</span>
            <span v-if="a.shortcode || (a.email && a.email !== a.name)" class="person__meta">
              <template v-if="a.shortcode">{{ a.shortcode }}</template>
              <template v-else>{{ a.email }}</template>
            </span>
            <span class="person__tags">
              <Badge v-if="a.ticketType" tone="accent">{{ a.ticketType }}</Badge>
              <Badge v-if="a.quantity > 1" tone="neutral">× {{ a.quantity }}</Badge>
              <Badge v-if="!a.paid" tone="danger">Not paid</Badge>
              <Badge v-if="a.status === 'cancelled' || a.status === 'expired'" tone="neutral">{{ a.status }}</Badge>
              <Badge v-if="a.guest" tone="neutral">Guest</Badge>
              <Badge v-if="a.committeeAdded" tone="accent">Added by committee</Badge>
            </span>
          </div>
          <div class="person__act">
            <template v-if="a.checkedInAt">
              <span class="person__in">In {{ time(a.checkedInAt) }}<template v-if="a.checkedInBy"> · {{ a.checkedInBy }}</template></span>
              <Button v-if="can('events.checkin.undo')" size="sm" variant="ghost" :disabled="busy" @click="undo(a)">Undo</Button>
            </template>
            <Button v-else-if="can('events.checkin.manual')" size="sm" :variant="a.paid ? 'primary' : 'secondary'" :disabled="busy || (!a.paid && !can('events.checkin.override'))" @click="checkIn(a)">Check in</Button>
          </div>
        </li>
        <li v-if="!loading && !visible.length" class="muted" style="padding:var(--space-5) 0">
          {{ query ? 'Nobody matches that.' : show === 'out' ? 'Everybody booked is in.' : 'Nobody here yet.' }}
        </li>
      </ul>
    </template>
  </main>
</template>

<style scoped>
.door {
  max-width: 720px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-4) var(--space-24);
  display: grid;
  gap: var(--space-5);
  min-height: 80vh;
  align-content: start;
}
.door__head {
  display: grid;
  gap: var(--space-2);
}
.door__title {
  margin: 0;
  font: var(--type-h2);
  letter-spacing: var(--tracking-heading);
  color: var(--text-strong);
}
.muted {
  margin: 0;
  font: var(--type-small);
  color: var(--text-muted);
}
.error {
  margin: 0;
  font: var(--type-small);
  color: var(--danger);
}
.back {
  font: var(--type-small);
}
.event-list {
  display: grid;
  gap: var(--space-3);
}
.event-card {
  width: 100%;
  font: inherit;
  color: inherit;
  cursor: pointer;
}
.count-line {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-brand);
}
.tally {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-2);
}
.tally__in {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 44px;
  line-height: 1;
  color: var(--text-strong);
}
.tally__of {
  font: var(--type-small);
  color: var(--text-muted);
}
.live {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: var(--text-muted);
}
.live__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #1f9d55;
  box-shadow: 0 0 0 3px rgba(31, 157, 85, 0.18);
}
.live.is-off .live__dot {
  background: var(--danger);
  box-shadow: 0 0 0 3px rgba(200, 40, 40, 0.18);
}
.live.is-off {
  color: var(--danger);
}
.scan {
  position: relative;
  display: grid;
  gap: var(--space-3);
}
.scan__frame {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-md);
  background: #000;
  aspect-ratio: 1 / 1;
  max-height: 60vh;
}
.scan__video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.scan__target {
  position: absolute;
  inset: 18%;
  border: 3px solid rgba(255, 255, 255, 0.85);
  border-radius: 14px;
  pointer-events: none;
}
/* `safe center` falls back to start when the content is taller than the
   panel, so a very long name is never clipped — the panel scrolls instead. */
.scan__result {
  display: grid;
  align-content: safe center;
  justify-items: center;
  gap: var(--space-2);
  padding: var(--space-6);
  text-align: center;
  color: #fff;
  cursor: pointer;
  border-radius: var(--radius-md);
  overflow-y: auto;
}
/* Over the camera the panel exactly covers the frame — the first element
   of .scan, with these same metrics. */
.scan__result.is-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  aspect-ratio: 1 / 1;
  max-height: 60vh;
}
.scan__result.is-ok {
  background: rgba(22, 128, 61, 0.94);
}
.scan__result.is-warn {
  background: rgba(196, 120, 0, 0.95);
}
.scan__result.is-bad {
  background: rgba(176, 32, 32, 0.95);
}
.scan__headline {
  font-family: var(--font-display);
  font-size: 34px;
  line-height: 1.1;
}
/* Big enough to read at arm's length: the name wraps anywhere rather than
   truncating, the ticket type sits in a high-contrast pill under it. */
.scan__name {
  max-width: 100%;
  font-size: 32px;
  font-weight: 700;
  line-height: 1.15;
  overflow-wrap: anywhere;
}
.scan__ticket {
  max-width: 100%;
  padding: var(--space-2) var(--space-5);
  border-radius: 999px;
  background: #fff;
  color: var(--ink-900, #0b0d12);
  font-size: 20px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.scan__msg {
  font-size: 16px;
}
.scan__hint {
  margin-top: var(--space-3);
  font-size: 13px;
  opacity: 0.8;
}
.scan__actions {
  display: flex;
  gap: var(--space-3);
  margin-top: var(--space-3);
}
.list-tools {
  display: grid;
  gap: var(--space-3);
  position: sticky;
  top: 0;
  z-index: 1;
  padding: var(--space-3) 0;
  background: var(--surface-page);
}
.chips {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.chip {
  border: 1px solid var(--border-default);
  background: var(--surface-card);
  border-radius: 999px;
  padding: 6px 14px;
  font: var(--type-small);
  cursor: pointer;
  color: var(--text-body);
}
.chip span {
  color: var(--text-faint);
  margin-left: 4px;
}
.chip.is-active {
  background: var(--ink-900, #0b0d12);
  border-color: var(--ink-900, #0b0d12);
  color: #fff;
}
.chip.is-active span {
  color: rgba(255, 255, 255, 0.7);
}
.people {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
}
.person {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-3);
  align-items: center;
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--border-hairline);
}
.person.is-in .person__name {
  color: var(--text-muted);
}
.person__who {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.person__name {
  font-weight: 700;
  color: var(--text-strong);
  overflow-wrap: anywhere;
}
.person__meta {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-muted);
  overflow-wrap: anywhere;
}
.person__tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.person__act {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}
.person__in {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: #1f7a45;
  text-align: right;
}
</style>
