<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Card from '@/components/ds/Card.vue'
import Button from '@/components/ds/Button.vue'
import Badge from '@/components/ds/Badge.vue'
import StatBlock from '@/components/ds/StatBlock.vue'
import Dialog from '@/components/ds/Dialog.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'
import api from '@/api'
import session from '@/stores/session'
import { portalNav } from '@/data/site'

const route = useRoute()

const portal = ref('dashboard')
const isDash = computed(() => portal.value === 'dashboard')

const dashboard = ref(null)
const panel = ref(null)
// Which panel `panel` holds. Switching tabs changes `portal` at once but the
// data a moment later, and rendering the new tab against the old tab's data is
// how the page threw on every switch.
const panelFor = ref(null)
const showing = (id) => portal.value === id && panelFor.value === id && panel.value
const sponsorName = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')

function flash(message) {
  notice.value = message
  error.value = ''
  setTimeout(() => (notice.value = ''), 8000)
}

// Admins belong to no sponsor, so the portal needs to be told which one to act
// for. Without this every read and write 400s with "you are signed in as
// committee" — which is what the whole page did for a committee account.
//
// A sponsor account is pinned to its own sponsor and never sees the picker.
// The committee's sponsor page links here with ?sponsor=<id> to open the portal
// already acting for that sponsor.
const chosenSponsorId = ref(Number(route.query.sponsor) || null)
const sponsorOptions = ref([])
const canChooseSponsor = computed(
  () => !session.sponsorIds.length && (session.capabilities ?? []).includes('sponsors'),
)
const actingSponsorId = computed(() =>
  session.sponsorIds.length ? session.sponsorIds[0] : chosenSponsorId.value,
)
const needsSponsor = computed(() => !actingSponsorId.value && canChooseSponsor.value)

async function loadSponsorOptions() {
  if (!canChooseSponsor.value) return
  try {
    // The admin list, not the public one: the portal scopes by numeric id and
    // /sponsors publishes the slug as its id.
    const data = await api.get('/admin/sponsors')
    sponsorOptions.value = (data.sponsors ?? []).map((x) => ({ id: x.id, name: x.name }))
  } catch (e) {
    error.value = e.message
  }
}

function scoped(path) {
  return actingSponsorId.value ? `${path}?sponsor_id=${actingSponsorId.value}` : path
}

/** Multipart upload. FormData must not carry a JSON content-type, so this goes
 *  round the api helper and sets only the CSRF header. */
async function upload(path, file, fields = {}) {
  const form = new FormData()
  form.append('file', file)
  for (const [k, v] of Object.entries(fields)) form.append(k, v)
  const res = await fetch(`/api${scoped(path)}`, {
    method: 'POST',
    body: form,
    credentials: 'same-origin',
    headers: { 'X-CSRF-Token': document.cookie.match(/(?:^|;\s*)csrf=([^;]*)/)?.[1] || '' },
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || 'Upload failed')
  return body
}

/** Run an action with the shared busy/error handling every button here needs. */
async function act(fn) {
  busy.value = true
  error.value = ''
  try {
    return await fn()
  } catch (e) {
    error.value = e.message
    return null
  } finally {
    busy.value = false
  }
}

// ------------------------------------------------------------- loading

async function loadDashboard() {
  error.value = ''
  // Nothing to ask for until a committee account has picked a sponsor. Asking
  // anyway is what produced the 400 that made the page look broken.
  if (needsSponsor.value) {
    dashboard.value = null
    sponsorName.value = ''
    return
  }
  try {
    dashboard.value = await api.get(scoped('/portal/dashboard'))
    // Named by the response, since that is the one thing that knows which
    // sponsor it was scoped to.
    sponsorName.value =
      dashboard.value?.sponsor?.name ??
      sponsorOptions.value.find((o) => o.id === actingSponsorId.value)?.name ??
      ''
  } catch (e) {
    error.value = e.message
  }
}

async function loadPanel(id) {
  if (id === 'dashboard') return loadDashboard()
  if (id === 'profile') {
    panel.value = null
    return loadProfile()
  }
  error.value = ''
  if (needsSponsor.value) {
    panel.value = null
    return
  }
  try {
    const data = await api.get(scoped(`/portal/panels/${id}`))
    // A slow response for a tab the user has already left is dropped.
    if (portal.value !== id) return
    panel.value = data
    panelFor.value = id
    if (id === 'contacts') contacts.value = (panel.value.contacts ?? []).map((c) => ({ ...c }))
    // Opening Messages marks the replies read, so the badge should go too.
    if (id === 'messages' && dashboard.value) dashboard.value.unreadReplies = 0
  } catch (e) {
    error.value = e.message
    panel.value = null
  }
}

const reload = () => Promise.all([loadDashboard(), isDash.value ? null : loadPanel(portal.value)])

const stats = computed(() => dashboard.value?.stats ?? [])
const sponsorable = computed(() => dashboard.value?.sponsorable ?? [])
const yourEvents = computed(() => dashboard.value?.yourEvents ?? [])
const listings = computed(() => dashboard.value?.listings ?? [])
const pkg = computed(() => dashboard.value?.package ?? [])

const portalTitle = computed(() => {
  const label = portalNav.find((p) => p.id === portal.value)?.label || 'Dashboard'
  return `${label}${sponsorName.value ? ` · ${sponsorName.value}` : ''}`
})
// Sector and agreement year when we have them, so the sidebar says something
// true rather than a fixed "Package · 2026-27".
const sponsorStrapline = computed(() => {
  const sp = dashboard.value?.sponsor
  return [sp?.sector, sp?.agreementYear].filter(Boolean).join(' · ')
})
const portalHeading = computed(() => {
  if (isDash.value) return 'Your year at a glance.'
  if (portal.value === 'profile') return 'How you appear on our sponsors page.'
  return panel.value?.heading || ''
})

// ------------------------------------------------------------- your card
// Everything on the public sponsor card except where it sits and whether it
// shows — those are the committee's (the API enforces it too).
const card = ref(null)
const withUsText = ref('')

async function loadProfile() {
  error.value = ''
  if (needsSponsor.value) {
    card.value = null
    return
  }
  try {
    const res = await api.get(scoped('/portal/profile'))
    card.value = { ...res.sponsor }
    withUsText.value = (res.sponsor.with_us || []).join('\n')
  } catch (e) {
    error.value = e.message
    card.value = null
  }
}

async function saveCard() {
  if (!card.value) return
  const { id, slug, ...fields } = card.value
  const ok = await act(() =>
    api.put(scoped('/portal/profile'), {
      ...fields,
      with_us: withUsText.value.split('\n').map((l) => l.trim()).filter(Boolean),
      roles: Math.max(0, Number(fields.roles) || 0),
    }),
  )
  if (ok) {
    flash('Saved. Your card on the sponsors page is updated.')
    await Promise.all([loadProfile(), loadDashboard()])
  }
}

async function uploadLogo(event) {
  const file = event.target.files?.[0]
  if (!file) return
  const res = await act(() => upload('/portal/profile/logo', file))
  if (res) {
    card.value.logo_url = res.url
    flash(res.message)
  }
  event.target.value = ''
}

// ------------------------------------------------------------- job listings
// The dialog that "Post a job" and every Edit button open. It used to set a
// flag nothing rendered, so neither did anything.
const blankJob = () => ({ title: '', location: '', apply_url: '', closes_on: '', body: '', status: 'live' })
const jobOpen = ref(false)
const jobId = ref(null)
const job = reactive(blankJob())

function openJob(row = null) {
  jobId.value = row?.id ?? null
  Object.assign(job, blankJob(), row?.listing ?? {})
  jobOpen.value = true
}

async function saveJob() {
  if (!job.title.trim()) {
    error.value = 'A role needs a title.'
    return
  }
  const payload = { ...job, closes_on: job.closes_on || null }
  const res = await act(() =>
    jobId.value
      ? api.put(`/portal/jobs/${jobId.value}`, payload)
      : api.post(scoped('/portal/jobs'), payload),
  )
  if (res) {
    jobOpen.value = false
    flash(res.message || 'Saved.')
    await reload()
  }
}

// ------------------------------------------------------------- events
async function registerInterest(row) {
  const res = await act(() => api.post(scoped(`/portal/events/${row.eventId}/interest`), {}))
  if (res) {
    flash(res.message)
    await reload()
  }
}

async function withdrawInterest(row) {
  if (!confirm(`Withdraw your interest in ${row.title}?`)) return
  const res = await act(() => api.del(scoped(`/portal/events/${row.eventId}/interest`)))
  if (res) {
    flash(res.message)
    await reload()
  }
}

const STATUS_TONE = {
  interested: 'accent',
  confirmed: 'success',
  declined: 'neutral',
  open: 'accent',
  in_progress: 'brand',
  closed: 'neutral',
  pending: 'accent',
  shared: 'success',
  withdrawn: 'neutral',
}

// ------------------------------------------------------------- requests
const requestOpen = ref(false)
const request = reactive({ format: 'workshop_talk', body: '', timing: '' })

function openRequest(format) {
  Object.assign(request, { format: format?.id ?? 'workshop_talk', body: '', timing: '' })
  requestOpen.value = true
}

const requestFormat = computed(() => panel.value?.formats?.find((f) => f.id === request.format))

async function sendRequest() {
  if (!request.body.trim()) {
    error.value = 'Tell us what you have in mind.'
    return
  }
  const res = await act(() => api.post(scoped('/portal/requests'), { ...request }))
  if (res) {
    requestOpen.value = false
    flash(res.message || 'Sent.')
    await loadPanel('request')
  }
}

// ------------------------------------------------------------- marketing
const blankShare = () => ({
  subject: '', body: '', media: [], audiences: ['members'], channels: ['email', 'social'],
  preferred_date: '',
})
const shareOpen = ref(false)
const share = reactive(blankShare())

function openShare() {
  Object.assign(share, blankShare())
  shareOpen.value = true
}

async function addMedia(event) {
  const files = [...(event.target.files || [])]
  for (const file of files) {
    const res = await act(() => upload('/portal/marketing/media', file))
    if (res) share.media.push({ url: res.url, kind: res.kind, name: res.name })
  }
  event.target.value = ''
}

function toggle(list, key) {
  const i = list.indexOf(key)
  if (i >= 0) list.splice(i, 1)
  else list.push(key)
}

async function sendShare() {
  if (!share.subject.trim() || !share.body.trim()) {
    error.value = 'It needs a subject and a message.'
    return
  }
  const res = await act(() =>
    api.post(scoped('/portal/marketing'), {
      ...share,
      media: share.media.map((m) => m.url),
      preferred_date: share.preferred_date || null,
    }),
  )
  if (res) {
    shareOpen.value = false
    flash(res.message)
    await reload()
  }
}

async function withdrawShare(item) {
  if (!confirm(`Withdraw "${item.subject}"? The share goes back into your allowance.`)) return
  const res = await act(() => api.post(scoped(`/portal/marketing/${item.id}/withdraw`), {}))
  if (res) {
    flash(res.message)
    await reload()
  }
}

// ------------------------------------------------------------- contacts
// Any number of contact points, emails or links. This used to be one CV
// address behind an Edit button that called an endpoint that did not exist.
const contacts = ref([])

function addContact(kind) {
  contacts.value.push({ kind, value: '', label: kind === 'email' ? 'Send a CV' : 'Careers page', note: '' })
}

function moveContact(i, delta) {
  const j = i + delta
  if (j < 0 || j >= contacts.value.length) return
  const list = contacts.value
  ;[list[i], list[j]] = [list[j], list[i]]
}

async function saveContacts() {
  const res = await act(() =>
    api.put(scoped('/portal/contacts'), { contacts: contacts.value.filter((c) => c.value.trim()) }),
  )
  if (res) {
    flash(res.message)
    await loadPanel('contacts')
  }
}

function contactHref(c) {
  return c.kind === 'email' ? `mailto:${c.value}` : c.value
}

// ------------------------------------------------------------- brand & merch
async function uploadArtwork(event) {
  const file = event.target.files?.[0]
  if (!file) return
  const res = await act(() =>
    upload('/portal/merch/upload', file, { label: file.name.replace(/\.[^.]+$/, '') }),
  )
  if (res) {
    flash(res.message || 'Uploaded.')
    await loadPanel('merch')
  }
  event.target.value = ''
}

async function deleteAsset(row) {
  if (!confirm(`Delete ${row.title}?`)) return
  if (await act(() => api.del(scoped(`/portal/merch/${row.id}`)))) await loadPanel('merch')
}

// ------------------------------------------------------------- notes
// A question or note on anything — the agreement, an event, a request, a
// share, a role — or a general one. The committee is emailed each one and
// replies in the same thread.
const notesOpen = ref(false)
const thread = ref(null)
const noteText = ref('')

async function openNotes(topic) {
  noteText.value = ''
  thread.value = { topic, label: '', notes: [] }
  notesOpen.value = true
  try {
    thread.value = await api.get(scoped('/portal/notes'), { topic })
  } catch (e) {
    notesOpen.value = false
    error.value = e.message
  }
}

async function sendNote(topic, text, after) {
  if (!text.trim()) return
  const res = await act(() => api.post(scoped('/portal/notes'), { topic, body: text }))
  if (res) {
    flash(res.message)
    if (after) await after()
  }
  return res
}

async function sendThreadNote() {
  const res = await sendNote(thread.value.topic, noteText.value, () => openNotes(thread.value.topic))
  if (res) noteText.value = ''
}

// The Messages panel: every thread, a reply box on each, and a new question.
const replies = reactive({})
const newNote = reactive({ topic: 'general', body: '' })

async function sendReply(t) {
  const res = await sendNote(t.topic, replies[t.topic] || '', () => loadPanel('messages'))
  if (res) replies[t.topic] = ''
}

async function sendNewNote() {
  const res = await sendNote(newNote.topic, newNote.body, () => loadPanel('messages'))
  if (res) Object.assign(newNote, { topic: 'general', body: '' })
}

const topicGroups = computed(() => {
  const groups = {}
  for (const t of panel.value?.topics ?? []) (groups[t.group] ||= []).push(t)
  return groups
})

function when(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

// Last, so every ref the first load touches has been declared.
watch(portal, loadPanel, { immediate: true })
// Switching sponsor reloads whatever panel is on screen, dashboard included.
// The dashboard always reloads: it is where the sidebar's name comes from.
watch(actingSponsorId, async () => {
  await loadDashboard()
  if (!isDash.value) await loadPanel(portal.value)
})
loadSponsorOptions()
</script>

<template>
  <main class="portal-layout">
    <aside class="eesoc-dark portal-aside">
      <div style="display:grid; gap:6px">
        <span class="eyebrow">Sponsor portal</span>
        <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-lg); color:var(--text-strong)">{{ sponsorName || 'No sponsor selected' }}</span>
        <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted)">{{ sponsorStrapline }}</span>
      </div>
      <div style="display:grid; gap:2px">
        <a
          v-for="p in portalNav"
          :key="p.id"
          href="#"
          class="portal-nav__link"
          :class="{ 'is-active': portal === p.id }"
          @click.prevent="portal = p.id"
        >{{ p.label }}<span v-if="p.id === 'messages' && dashboard?.unreadReplies" class="nav-badge">{{ dashboard.unreadReplies }}</span></a>
      </div>
      <div style="display:grid; gap:var(--space-3); border-top:1px solid var(--border-hairline); padding-top:var(--space-6)">
        <span style="font-family:var(--font-mono); font-size:var(--text-2xs); line-height:1.6; color:var(--text-muted)">Your contact: eesoc.vp@imperial.ac.uk</span>
      </div>
    </aside>

    <section class="portal-main">
      <div class="portal-main__head">
        <div style="display:grid; gap:var(--space-3)">
          <span class="eyebrow">{{ portalTitle }}</span>
          <h1 style="margin:0; font:var(--type-h2); letter-spacing:var(--tracking-heading); color:var(--text-strong)">{{ portalHeading }}</h1>
        </div>
        <div style="display:flex; gap:var(--space-3); align-items:center; flex-wrap:wrap">
          <!-- Committee only. A sponsor account is pinned to its own sponsor and
               never sees this. -->
          <label v-if="canChooseSponsor" class="portal-acting">
            <span class="portal-acting__label">Acting for</span>
            <select v-model="chosenSponsorId" class="portal-acting__select">
              <option :value="null">Choose a sponsor…</option>
              <option v-for="o in sponsorOptions" :key="o.id" :value="o.id">{{ o.name }}</option>
            </select>
          </label>
          <Button :disabled="!actingSponsorId" @click="openJob()">Post a job</Button>
        </div>
      </div>

      <!-- A committee account belongs to no sponsor, so there is nothing to show
           until one is picked. -->
      <Card v-if="needsSponsor" variant="outline">
        <p style="margin:0; font:var(--type-small); color:var(--text-body)">
          You are signed in as committee, so this portal has no sponsor of its own.
          Pick one above to see and edit what that sponsor sees.
        </p>
      </Card>

      <p v-if="error" style="margin:0; font:var(--type-small); color:var(--danger)">{{ error }}</p>
      <p v-if="notice" style="margin:0; font:var(--type-small); color:var(--text-muted)">{{ notice }}</p>

      <!-- ============================ Dashboard ============================ -->
      <template v-if="isDash && dashboard">
        <div class="grid-4">
          <StatBlock v-for="(s, i) in stats" :key="i" :value="s.value" :label="s.label" :sub="s.sub" />
        </div>

        <div class="portal-cols">
          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">Events you can sponsor</span>
            <p v-if="!sponsorable.length" class="muted">
              Nothing open right now. New events appear here as the committee plans them.
            </p>
            <Card v-for="c in sponsorable" :key="c.eventId" variant="default">
              <div class="row-between">
                <div class="stack-4">
                  <span class="row-title">{{ c.title }}</span>
                  <span class="row-meta">{{ c.meta }}</span>
                </div>
                <div class="actions">
                  <Button size="sm" variant="ghost" @click="openNotes(`event:${c.eventId}`)">Ask</Button>
                  <Button v-if="c.interested" size="sm" variant="ghost" :disabled="busy" @click="withdrawInterest(c)">Interest registered · withdraw</Button>
                  <Button v-else size="sm" variant="secondary" :disabled="busy" @click="registerInterest(c)">Register interest</Button>
                </div>
              </div>
            </Card>

            <span class="eyebrow" style="color:var(--text-brand); padding-top:var(--space-2)">Your events</span>
            <p v-if="!yourEvents.length" class="muted">
              Nothing yet. Register interest above, or ask for something under Request an event.
            </p>
            <Card v-for="e in yourEvents" :key="e.eventId" variant="offset">
              <div class="row-between">
                <div class="stack-4">
                  <span class="row-title">{{ e.title }}</span>
                  <span class="row-meta">{{ e.meta }}</span>
                </div>
                <div class="actions">
                  <Badge :tone="STATUS_TONE[e.status]">{{ e.statusLabel }}</Badge>
                  <Button size="sm" variant="ghost" @click="openNotes(`event:${e.eventId}`)">Ask</Button>
                  <Button v-if="e.published" size="sm" variant="secondary" :href="`/events/${e.id}`">View</Button>
                </div>
              </div>
            </Card>
          </div>

          <div style="display:grid; gap:var(--space-8)">
            <div style="display:grid; gap:var(--space-3)">
              <span class="eyebrow">Your package</span>
              <div v-for="t in pkg" :key="t.key" class="pkg-row" :class="{ 'is-struck': !t.agreed }">
                <div class="row-between">
                  <span style="font-family:var(--font-mono); font-size:var(--text-sm); color:var(--text-strong)">{{ t.label }}</span>
                  <span class="row-meta">{{ t.status }}</span>
                </div>
                <span v-if="t.note" class="row-meta">{{ t.note }}</span>
                <details class="pkg-text">
                  <summary>What we agreed</summary>
                  <p>{{ t.text }}</p>
                  <a v-if="t.agreed" href="#" @click.prevent="openNotes(`clause:${t.key}`)">Ask about this</a>
                </details>
              </div>
              <a href="#" style="font:var(--type-small)" @click.prevent="openNotes('general')">Ask the committee a question</a>
            </div>

            <div style="display:grid; gap:var(--space-3)">
              <span class="eyebrow">Your listings</span>
              <p v-if="!listings.length" class="muted">No roles posted yet.</p>
              <div v-for="j in listings" :key="j.id" class="pkg-row">
                <div class="row-between">
                  <span style="font-family:var(--font-mono); font-size:var(--text-sm); color:var(--text-strong)">{{ j.title }}</span>
                  <Badge :tone="j.tone">{{ j.status }}</Badge>
                </div>
                <span class="row-meta">{{ j.meta }}</span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- ============================ Your card ============================ -->
      <template v-else-if="portal === 'profile' && card">
        <form class="card-form" @submit.prevent="saveCard">
          <Field label="Logo" help="JPEG, PNG, WebP or GIF. Square-ish works best.">
            <div style="display:flex; gap:var(--space-4); align-items:center; flex-wrap:wrap">
              <img v-if="card.logo_url" :src="card.logo_url" :alt="card.name" class="logo-preview" />
              <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" :disabled="busy" @change="uploadLogo" />
            </div>
          </Field>
          <Field label="Company name"><Input v-model="card.name" required /></Field>
          <div class="fields-2">
            <Field label="Sector" help="e.g. Energy, Semiconductors."><Input v-model="card.sector" /></Field>
            <Field label="Strand" help="Which part of EEE you hire from."><Input v-model="card.strand" /></Field>
          </div>
          <Field label="One line" help="Shown on the card before it is opened."><Input v-model="card.one_line" /></Field>
          <Field label="Description" help="Shown when the card is opened.">
            <textarea v-model="card.blurb" rows="6" class="ees-textarea" />
          </Field>
          <Field label="What you do with EESoc" help="One per line, e.g. Careers fair, Tech talk.">
            <textarea v-model="withUsText" rows="3" class="ees-textarea" />
          </Field>
          <div class="fields-2">
            <Field label="Website"><Input v-model="card.site" placeholder="example.com/careers" /></Field>
            <Field label="Open roles" help="The count on your card."><Input v-model.number="card.roles" type="number" min="0" /></Field>
          </div>
          <p class="muted">Where members send CVs and how they reach you is under Contacts.</p>
          <div style="display:flex; gap:var(--space-4); align-items:center; flex-wrap:wrap">
            <Button type="submit" :disabled="busy">{{ busy ? 'Saving…' : 'Save your card' }}</Button>
            <router-link to="/sponsors" style="font:var(--type-small)">See the sponsors page</router-link>
          </div>
        </form>
      </template>

      <!-- ============================ Job listings ========================= -->
      <template v-else-if="showing('jobs')">
        <div class="portal-cols">
          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">Your listings</span>
            <p v-if="!panel.rows.length" class="muted">No roles yet. Post one and it appears on the site straight away.</p>
            <Card v-for="r in panel.rows" :key="r.id" variant="default">
              <div class="row-between">
                <div class="stack-4">
                  <span class="row-title">{{ r.title }}</span>
                  <span class="row-meta">{{ r.meta }}</span>
                </div>
                <div class="actions">
                  <Badge :tone="r.tone">{{ r.status }}</Badge>
                  <Button size="sm" variant="ghost" @click="openNotes(`listing:${r.id}`)">Ask</Button>
                  <Button size="sm" variant="secondary" @click="openJob(r)">Edit</Button>
                </div>
              </div>
            </Card>
          </div>
          <Card variant="offset">
            <div class="aside-stack">
              <span class="eyebrow">Post a role</span>
              <p class="aside-text">{{ panel.aside }}</p>
              <Button block :disabled="busy" @click="openJob()">Post a job</Button>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================ Event calendar ======================= -->
      <template v-else-if="showing('calendar')">
        <div class="portal-cols">
          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">Open for sponsorship</span>
            <p v-if="!panel.sponsorable.length" class="muted">Nothing open right now.</p>
            <Card v-for="r in panel.sponsorable" :key="r.eventId" variant="default">
              <div class="row-between">
                <div class="stack-4">
                  <span class="row-title">{{ r.title }}</span>
                  <span class="row-meta">{{ r.meta }}</span>
                </div>
                <div class="actions">
                  <Button size="sm" variant="ghost" @click="openNotes(`event:${r.eventId}`)">Ask</Button>
                  <Button v-if="r.interested" size="sm" variant="ghost" :disabled="busy" @click="withdrawInterest(r)">Interest registered · withdraw</Button>
                  <Button v-else size="sm" variant="secondary" :disabled="busy" @click="registerInterest(r)">Register interest</Button>
                </div>
              </div>
            </Card>

            <span class="eyebrow" style="color:var(--text-brand); padding-top:var(--space-2)">Your events</span>
            <p v-if="!panel.yourEvents.length" class="muted">Nothing yet.</p>
            <Card v-for="e in panel.yourEvents" :key="e.eventId" variant="offset">
              <div class="row-between">
                <div class="stack-4">
                  <span class="row-title">{{ e.title }}</span>
                  <span class="row-meta">{{ e.meta }}</span>
                </div>
                <div class="actions">
                  <Badge :tone="STATUS_TONE[e.status]">{{ e.statusLabel }}</Badge>
                  <Button size="sm" variant="ghost" @click="openNotes(`event:${e.eventId}`)">Ask</Button>
                  <Button v-if="e.published" size="sm" variant="secondary" :href="`/events/${e.id}`">View</Button>
                </div>
              </div>
            </Card>
          </div>
          <Card variant="offset">
            <div class="aside-stack">
              <span class="eyebrow">How it works</span>
              <p class="aside-text">{{ panel.aside }}</p>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================ Request an event ===================== -->
      <template v-else-if="showing('request')">
        <div class="portal-cols">
          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">What you can ask for</span>
            <Card v-for="f in panel.formats" :key="f.id" variant="default">
              <div class="row-between">
                <div class="stack-4">
                  <span class="row-title">{{ f.title }}</span>
                  <span class="row-meta">{{ f.meta }}</span>
                  <span class="row-meta" :style="{ color: f.included ? 'var(--text-brand)' : 'var(--text-faint)' }">{{ f.coverage }}</span>
                </div>
                <Button size="sm" variant="secondary" @click="openRequest(f)">Request</Button>
              </div>
            </Card>

            <span class="eyebrow" style="color:var(--text-brand); padding-top:var(--space-2)">Your requests</span>
            <p v-if="!panel.requests.length" class="muted">Nothing sent yet.</p>
            <Card v-for="r in panel.requests" :key="r.id" variant="offset">
              <div style="display:grid; gap:var(--space-2)">
                <div class="row-between">
                  <span class="row-title">{{ r.format }}</span>
                  <Badge :tone="STATUS_TONE[r.status]">{{ r.statusLabel }}</Badge>
                </div>
                <span class="row-meta">Sent {{ r.sent }}<template v-if="r.timing"> · {{ r.timing }}</template></span>
                <p style="margin:0; font:var(--type-small); color:var(--text-body); white-space:pre-line">{{ r.body }}</p>
                <div v-if="r.response" class="reply">
                  <span class="label-muted">EESoc replied</span>
                  <p style="margin:0; font:var(--type-small); white-space:pre-line">{{ r.response }}</p>
                </div>
                <div><Button size="sm" variant="ghost" @click="openNotes(`request:${r.id}`)">Add a note</Button></div>
              </div>
            </Card>
          </div>
          <Card variant="offset">
            <div class="aside-stack">
              <span class="eyebrow">What happens next</span>
              <p class="aside-text">{{ panel.aside }}</p>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================ Marketing ============================ -->
      <template v-else-if="showing('marketing')">
        <Card v-if="!panel.agreed" variant="outline">
          <p style="margin:0; font:var(--type-small)">
            Marketing to our members isn't part of your agreement this year. Talk to
            eesoc.vp@imperial.ac.uk if you think it should be.
          </p>
        </Card>
        <div v-else class="portal-cols">
          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">Your requests</span>
            <p v-if="!panel.items.length" class="muted">Nothing sent yet.</p>
            <Card v-for="m in panel.items" :key="m.id" variant="default">
              <div style="display:grid; gap:var(--space-3)">
                <div class="row-between">
                  <span class="row-title">{{ m.subject }}</span>
                  <Badge :tone="STATUS_TONE[m.status]">{{ m.statusLabel }}</Badge>
                </div>
                <span class="row-meta">
                  Sent {{ m.sent }} · {{ m.channels.join(' + ') }} · {{ m.audiences.join(', ') }}
                  <template v-if="m.preferredDate"> · hoping for {{ m.preferredDate }}</template>
                </span>
                <p style="margin:0; font:var(--type-small); color:var(--text-body); white-space:pre-line">{{ m.body }}</p>
                <div v-if="m.media.length" class="media-strip">
                  <a v-for="x in m.media" :key="x.url" :href="x.url" target="_blank" rel="noopener" class="media-thumb">
                    <img v-if="x.kind === 'image'" :src="x.url" alt="" />
                    <span v-else>PDF</span>
                  </a>
                </div>
                <span v-if="m.emailed || m.posted" class="row-meta">
                  <template v-if="m.emailed">Emailed {{ m.emailed }}</template>
                  <template v-if="m.emailed && m.posted"> · </template>
                  <template v-if="m.posted">Posted on social media {{ m.posted }}</template>
                </span>
                <div v-if="m.response" class="reply">
                  <span class="label-muted">EESoc replied</span>
                  <p style="margin:0; font:var(--type-small); white-space:pre-line">{{ m.response }}</p>
                </div>
                <div class="actions" style="justify-content:flex-start">
                  <Button size="sm" variant="ghost" @click="openNotes(`marketing:${m.id}`)">Add a note</Button>
                  <Button v-if="m.status === 'pending'" size="sm" variant="ghost" :disabled="busy" @click="withdrawShare(m)">Withdraw</Button>
                </div>
              </div>
            </Card>
          </div>
          <Card variant="offset">
            <div class="aside-stack">
              <span class="eyebrow">{{ panel.remaining }} of {{ panel.allowance }} left for {{ panel.year }}</span>
              <p class="aside-text">{{ panel.aside }}</p>
              <Button block :disabled="busy || panel.remaining <= 0" @click="openShare">New marketing request</Button>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================ Contacts ============================= -->
      <template v-else-if="showing('contacts')">
        <Card v-if="!panel.agreed" variant="outline">
          <p style="margin:0; font:var(--type-small)">
            Sharing a contact with members isn't part of your agreement this year. Talk
            to eesoc.vp@imperial.ac.uk if you think it should be.
          </p>
        </Card>
        <div v-else class="portal-cols">
          <form style="display:grid; gap:var(--space-4)" @submit.prevent="saveContacts">
            <span class="eyebrow">Contact points</span>
            <p v-if="!contacts.length" class="muted">None yet. Add an email address or a link.</p>
            <Card v-for="(c, i) in contacts" :key="i" variant="default">
              <div style="display:grid; gap:var(--space-3)">
                <div class="fields-2">
                  <Field label="Type">
                    <select v-model="c.kind" class="ees-select">
                      <option value="email">Email address</option>
                      <option value="link">Link</option>
                    </select>
                  </Field>
                  <Field :label="c.kind === 'email' ? 'Email address' : 'Web address'">
                    <Input v-model="c.value" :type="c.kind === 'email' ? 'email' : 'url'" :placeholder="c.kind === 'email' ? 'graduates@company.com' : 'https://company.com/careers'" />
                  </Field>
                </div>
                <div class="fields-2">
                  <Field label="What it's for" help="e.g. Send a CV, Book a chat."><Input v-model="c.label" /></Field>
                  <Field label="Note" help="e.g. PDF only, put EESoc in the subject."><Input v-model="c.note" /></Field>
                </div>
                <div style="display:flex; gap:var(--space-2)">
                  <Button size="sm" variant="ghost" type="button" :disabled="i === 0" @click="moveContact(i, -1)">↑</Button>
                  <Button size="sm" variant="ghost" type="button" :disabled="i === contacts.length - 1" @click="moveContact(i, 1)">↓</Button>
                  <Button size="sm" variant="danger" type="button" @click="contacts.splice(i, 1)">Remove</Button>
                </div>
              </div>
            </Card>
            <div style="display:flex; gap:var(--space-3); flex-wrap:wrap">
              <Button variant="secondary" type="button" @click="addContact('email')">Add an email</Button>
              <Button variant="secondary" type="button" @click="addContact('link')">Add a link</Button>
              <Button type="submit" :disabled="busy">{{ busy ? 'Saving…' : 'Save contacts' }}</Button>
            </div>
          </form>
          <Card variant="offset">
            <div class="aside-stack">
              <span class="eyebrow">On your card</span>
              <p class="aside-text">{{ panel.aside }}</p>
              <div v-for="(c, i) in contacts.filter((x) => x.value.trim())" :key="i" style="display:grid; gap:2px">
                <a :href="contactHref(c)" target="_blank" rel="noopener" style="font-family:var(--font-mono); font-size:var(--text-sm)">{{ c.label || c.value }}</a>
                <span v-if="c.note" class="row-meta">{{ c.note }}</span>
              </div>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================ Brand & merch ======================== -->
      <template v-else-if="showing('merch')">
        <div class="portal-cols">
          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">Your files</span>
            <p v-if="!panel.rows.length" class="muted">Nothing uploaded yet.</p>
            <Card v-for="r in panel.rows" :key="r.id" variant="default">
              <div class="row-between">
                <div class="stack-4">
                  <span class="row-title">{{ r.title }}</span>
                  <a :href="r.url" target="_blank" rel="noopener" class="row-meta">Open file</a>
                </div>
                <Button size="sm" variant="danger" :disabled="busy" @click="deleteAsset(r)">Delete</Button>
              </div>
            </Card>
            <p class="muted">
              <template v-if="panel.merchAgreed">
                Your agreement includes us handing your merchandise to members and, by
                agreement, your logo on ours.
              </template>
              <template v-else>Merchandise isn't part of your agreement this year.</template>
            </p>
          </div>
          <Card variant="offset">
            <div class="aside-stack">
              <span class="eyebrow">What we need</span>
              <p class="aside-text">{{ panel.aside }}</p>
              <label class="upload-label">
                <input type="file" accept="image/jpeg,image/png,image/webp,image/gif,application/pdf" :disabled="busy" @change="uploadArtwork" />
                <span>{{ busy ? 'Uploading…' : 'Upload artwork' }}</span>
              </label>
            </div>
          </Card>
        </div>
      </template>

      <!-- ============================ Messages ============================= -->
      <template v-else-if="showing('messages')">
        <div class="portal-cols">
          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">Conversations</span>
            <p v-if="!panel.threads.length" class="muted">No questions yet.</p>
            <Card v-for="t in panel.threads" :key="t.topic" variant="default">
              <div style="display:grid; gap:var(--space-3)">
                <span class="row-title">{{ t.label }}</span>
                <div v-for="n in t.notes" :key="n.id" class="note" :class="{ 'note--theirs': n.fromCommittee }">
                  <span class="row-meta">{{ n.author }} · {{ when(n.at) }}</span>
                  <p>{{ n.body }}</p>
                </div>
                <textarea v-model="replies[t.topic]" rows="2" class="ees-textarea" placeholder="Add to this conversation" />
                <div><Button size="sm" :disabled="busy || !(replies[t.topic] || '').trim()" @click="sendReply(t)">Send</Button></div>
              </div>
            </Card>
          </div>
          <Card variant="offset">
            <form class="aside-stack" @submit.prevent="sendNewNote">
              <span class="eyebrow">New question</span>
              <p class="aside-text">{{ panel.aside }}</p>
              <Field label="About">
                <select v-model="newNote.topic" class="ees-select">
                  <optgroup v-for="(items, group) in topicGroups" :key="group" :label="group">
                    <option v-for="t in items" :key="t.topic" :value="t.topic">{{ t.label }}</option>
                  </optgroup>
                </select>
              </Field>
              <Field label="Your question or note">
                <textarea v-model="newNote.body" rows="5" class="ees-textarea" required />
              </Field>
              <Button type="submit" block :disabled="busy || !newNote.body.trim()">Send to the committee</Button>
            </form>
          </Card>
        </div>
      </template>
    </section>

    <!-- ============================ Dialogs ============================== -->
    <Dialog :open="notesOpen" :title="thread?.label || 'Ask the committee'" width="min(640px, 94vw)" @close="notesOpen = false">
      <div v-if="thread" style="display:grid; gap:var(--space-4)">
        <p v-if="!thread.notes.length" class="muted">
          Ask a question or leave a note. The committee is emailed and replies here —
          you'll find the conversation under Messages too.
        </p>
        <div v-for="n in thread.notes" :key="n.id" class="note" :class="{ 'note--theirs': n.fromCommittee }">
          <span class="row-meta">{{ n.author }} · {{ when(n.at) }}</span>
          <p>{{ n.body }}</p>
        </div>
        <form style="display:grid; gap:var(--space-3)" @submit.prevent="sendThreadNote">
          <textarea v-model="noteText" rows="4" class="ees-textarea" placeholder="Your question or note" required />
          <div style="display:flex; gap:var(--space-3)">
            <Button type="submit" :disabled="busy || !noteText.trim()">{{ busy ? 'Sending…' : 'Send' }}</Button>
            <Button variant="ghost" type="button" @click="notesOpen = false">Close</Button>
          </div>
        </form>
      </div>
    </Dialog>

    <Dialog :open="jobOpen" :title="jobId ? 'Edit listing' : 'Post a job'" width="min(640px, 94vw)" @close="jobOpen = false">
      <form style="display:grid; gap:var(--space-4)" @submit.prevent="saveJob">
        <Field label="Role title"><Input v-model="job.title" required /></Field>
        <div class="fields-2">
          <Field label="Location"><Input v-model="job.location" placeholder="London" /></Field>
          <Field label="Closes on"><Input v-model="job.closes_on" type="date" /></Field>
        </div>
        <Field label="Where to apply" help="The page members apply on."><Input v-model="job.apply_url" placeholder="https://company.com/careers/role" /></Field>
        <Field label="Description">
          <textarea v-model="job.body" rows="5" class="ees-textarea" />
        </Field>
        <Field label="Status">
          <select v-model="job.status" class="ees-select">
            <option value="live">Live — on the site now</option>
            <option value="draft">Draft — only you can see it</option>
            <option value="closed">Closed</option>
          </select>
        </Field>
        <div style="display:flex; gap:var(--space-3)">
          <Button type="submit" :disabled="busy">{{ busy ? 'Saving…' : jobId ? 'Save listing' : 'Post' }}</Button>
          <Button variant="ghost" type="button" @click="jobOpen = false">Cancel</Button>
        </div>
      </form>
    </Dialog>

    <Dialog :open="requestOpen" title="Request an event" width="min(640px, 94vw)" @close="requestOpen = false">
      <form style="display:grid; gap:var(--space-4)" @submit.prevent="sendRequest">
        <Field label="What kind of event">
          <select v-model="request.format" class="ees-select">
            <option v-for="f in panel?.formats ?? []" :key="f.id" :value="f.id">{{ f.title }}</option>
          </select>
        </Field>
        <p v-if="requestFormat" class="muted">{{ requestFormat.coverage }}</p>
        <Field label="What you have in mind" help="The topic, who it is for, what you want students to come away with.">
          <textarea v-model="request.body" rows="6" class="ees-textarea" required />
        </Field>
        <Field label="When" help="A term, a week or a date — optional."><Input v-model="request.timing" placeholder="Spring term, ideally February" /></Field>
        <div style="display:flex; gap:var(--space-3)">
          <Button type="submit" :disabled="busy">{{ busy ? 'Sending…' : 'Send request' }}</Button>
          <Button variant="ghost" type="button" @click="requestOpen = false">Cancel</Button>
        </div>
      </form>
    </Dialog>

    <Dialog :open="shareOpen" title="New marketing request" width="min(720px, 94vw)" @close="shareOpen = false">
      <form style="display:grid; gap:var(--space-4)" @submit.prevent="sendShare">
        <Field label="Subject" help="The email subject line, and the headline on social media."><Input v-model="share.subject" required /></Field>
        <Field label="Message" help="Plain text. **bold**, [link text](https://…) and lines starting “- ” for a list all work.">
          <textarea v-model="share.body" rows="8" class="ees-textarea" required />
        </Field>
        <Field label="Images or PDF" help="Up to six. Images appear in the email; a PDF is linked.">
          <div style="display:grid; gap:var(--space-3)">
            <div v-if="share.media.length" class="media-strip">
              <div v-for="(x, i) in share.media" :key="x.url" class="media-thumb">
                <img v-if="x.kind === 'image'" :src="x.url" alt="" />
                <span v-else>PDF</span>
                <button type="button" class="media-remove" :aria-label="`Remove ${x.name}`" @click="share.media.splice(i, 1)">×</button>
              </div>
            </div>
            <input type="file" multiple accept="image/jpeg,image/png,image/webp,image/gif,application/pdf" :disabled="busy || share.media.length >= 6" @change="addMedia" />
          </div>
        </Field>
        <Field label="Who it's for">
          <div class="check-grid">
            <label v-for="a in panel?.audiences ?? []" :key="a.key" class="check">
              <input type="checkbox" :checked="share.audiences.includes(a.key)" @change="toggle(share.audiences, a.key)" />
              {{ a.label }}
            </label>
          </div>
        </Field>
        <div class="fields-2">
          <Field label="Where">
            <div style="display:flex; gap:var(--space-4); flex-wrap:wrap">
              <label v-for="c in panel?.channels ?? []" :key="c.key" class="check">
                <input type="checkbox" :checked="share.channels.includes(c.key)" @change="toggle(share.channels, c.key)" />
                {{ c.label }}
              </label>
            </div>
          </Field>
          <Field label="Preferred date" help="Optional."><Input v-model="share.preferred_date" type="date" /></Field>
        </div>
        <p class="muted">This uses one of your {{ panel?.allowance }} shares for {{ panel?.year }}. The committee checks every request before it goes out.</p>
        <div style="display:flex; gap:var(--space-3)">
          <Button type="submit" :disabled="busy || !share.audiences.length || !share.channels.length">{{ busy ? 'Sending…' : 'Send to the committee' }}</Button>
          <Button variant="ghost" type="button" @click="shareOpen = false">Cancel</Button>
        </div>
      </form>
    </Dialog>
  </main>
</template>

<style scoped>
.eyebrow {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--text-accent);
}
.label-muted {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--text-muted);
}
.muted {
  margin: 0;
  font: var(--type-small);
  color: var(--text-faint);
}
.portal-layout {
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  min-height: calc(100vh - 72px);
}
.portal-aside {
  background: var(--surface-page);
  padding: var(--space-8) var(--space-6);
  display: grid;
  gap: var(--space-8);
  align-content: start;
}
.portal-nav__link {
  padding: 11px 12px;
  border-radius: 4px;
  text-decoration: none;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  transition: var(--transition-control);
}
.portal-nav__link:hover {
  color: var(--text-strong);
  background: var(--surface-brand-subtle);
  text-decoration: none;
}
.portal-nav__link.is-active {
  background: var(--surface-brand);
  color: var(--white);
}
.portal-main {
  padding: var(--space-12) var(--space-12) var(--space-24);
  display: grid;
  gap: var(--space-10);
  align-content: start;
  background: var(--surface-card);
}
.portal-main__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-8);
  flex-wrap: wrap;
}
.grid-4 {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-8);
}
.portal-cols {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
  gap: var(--space-10);
  align-items: start;
}
.row-between {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-6);
  align-items: center;
}
.stack-4 {
  display: grid;
  gap: 4px;
}
.row-title {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: var(--text-base);
  color: var(--text-strong);
}
.row-meta {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.pkg-row {
  display: grid;
  gap: 4px;
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--border-hairline);
}
.pkg-row.is-struck span:first-child {
  text-decoration: line-through;
  color: var(--text-faint);
}
.aside-stack {
  display: grid;
  gap: var(--space-4);
}
.aside-text {
  margin: 0;
  font: var(--type-small);
  color: var(--text-body);
}
.card-form {
  display: grid;
  gap: var(--space-4);
  max-width: 760px;
}
.fields-2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-4);
}
.logo-preview {
  width: 96px;
  height: 96px;
  object-fit: contain;
  background: #fff;
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-sm);
}
.reply {
  display: grid;
  gap: var(--space-1, 4px);
  padding: var(--space-3);
  background: var(--surface-brand-subtle);
  border-radius: var(--radius-sm);
}
.media-strip {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.media-thumb {
  position: relative;
  width: 72px;
  height: 72px;
  display: grid;
  place-items: center;
  overflow: hidden;
  background: var(--white);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-muted);
  text-decoration: none;
}
.media-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.media-remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  cursor: pointer;
  line-height: 20px;
  padding: 0;
}
.check-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--space-2) var(--space-4);
}
.check {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  font: var(--type-small);
  color: var(--text-body);
}
.upload-label {
  display: grid;
  gap: var(--space-2);
  font: var(--type-small);
}
.portal-acting {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.portal-acting__label {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: var(--text-muted);
}
.portal-acting__select {
  font: var(--type-small);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  background: var(--surface-card);
  color: var(--text-strong);
}
.actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.nav-badge {
  display: inline-block;
  margin-left: var(--space-2);
  min-width: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--danger);
  color: var(--white);
  font-size: var(--text-2xs);
  line-height: 18px;
  text-align: center;
}
.pkg-text {
  font: var(--type-2xs);
  color: var(--text-muted);
}
.pkg-text summary {
  cursor: pointer;
}
.pkg-text p {
  margin: var(--space-2) 0;
}
.note {
  display: grid;
  gap: 4px;
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--surface-page);
}
.note--theirs {
  background: var(--surface-brand-subtle);
}
.note p {
  margin: 0;
  font: var(--type-small);
  color: var(--text-body);
  white-space: pre-line;
}
/* The column collapses come from app.css, shared with the other two-pane pages. */
@media (max-width: 960px) {
  .portal-main {
    padding: var(--space-8) var(--space-5) var(--space-16);
  }
}
</style>
