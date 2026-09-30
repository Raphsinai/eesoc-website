<script setup>
import { computed, ref, watch } from 'vue'
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

const portal = ref('dashboard')
const isDash = computed(() => portal.value === 'dashboard')

const dashboard = ref(null)
const panel = ref(null)
const sponsorName = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')

// Every action on this page was a button with no handler. These are the forms
// behind them.
const showJobForm = ref(false)
const showRequestForm = ref(false)
const job = ref({ title: '', location: '', apply_url: '', closes_on: '', body: '' })
const request = ref({ format: '', body: '' })
const showCvForm = ref(false)
const cv = ref({ cv_contact_email: '', cv_contact_name: '', cv_contact_note: '' })

function flash(message) {
  notice.value = message
  error.value = ''
  setTimeout(() => (notice.value = ''), 8000)
}

// Admins belong to no sponsor, so the portal needs to be told which one to act
// for. Without this every write 400s with "you are signed in as committee".
const actingSponsorId = computed(() =>
  session.sponsorIds.length ? session.sponsorIds[0] : null,
)

function scoped(path) {
  return actingSponsorId.value ? `${path}?sponsor_id=${actingSponsorId.value}` : path
}

async function postJob() {
  if (!job.value.title.trim()) {
    error.value = 'A role needs a title.'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const res = await api.post(scoped('/portal/jobs'), {
      ...job.value,
      closes_on: job.value.closes_on || null,
    })
    flash(res.message || 'Posted.')
    job.value = { title: '', location: '', apply_url: '', closes_on: '', body: '' }
    showJobForm.value = false
    await loadPanel(portal.value)
    await loadDashboard()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function sendRequest() {
  if (!request.value.body.trim()) {
    error.value = 'Tell us what you have in mind.'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const res = await api.post(scoped('/portal/requests'), { ...request.value })
    flash(res.message || 'Sent.')
    request.value = { format: '', body: '' }
    showRequestForm.value = false
    await loadPanel(portal.value)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

/** The panel footer button does different things per panel. */
async function panelAction() {
  if (portal.value === 'jobs') showJobForm.value = true
  else if (portal.value === 'request') showRequestForm.value = true
  else if (portal.value === 'calendar') portal.value = 'dashboard'
  else if (portal.value === 'cv') {
    try {
      const res = await api.get(scoped('/portal/cv-book'))
      flash(res.message)
    } catch (e) {
      error.value = e.message
    }
  } else if (portal.value === 'merch') {
    error.value =
      'Send artwork to eesoc.vp@imperial.ac.uk for now — direct upload is not wired up.'
  }
}

/** Rows in the calendar panel are claimable events. */
const artworkInput = ref(null)

async function saveCvContact() {
  busy.value = true
  error.value = ''
  try {
    await api.put(scoped('/portal/profile'), { ...cv.value })
    flash('Saved. It now shows on your public sponsor card.')
    showCvForm.value = false
    await loadPanel(portal.value)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function uploadArtwork(event) {
  const file = event.target.files?.[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('file', file)
    form.append('label', file.name.replace(/\.[^.]+$/, ''))
    // FormData must not carry a JSON content-type, so this goes round the api
    // helper and sets only the CSRF header.
    const url = actingSponsorId.value
      ? `/api/portal/merch/upload?sponsor_id=${actingSponsorId.value}`
      : '/api/portal/merch/upload'
    const res = await fetch(url, {
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
        /* non-JSON body */
      }
      throw new Error(detail)
    }
    flash((await res.json()).message || 'Uploaded.')
    await loadPanel('merch')
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
    event.target.value = ''
  }
}

async function deleteAsset(row) {
  if (!confirm(`Delete ${row.title}?`)) return
  busy.value = true
  try {
    await api.del(scoped(`/portal/merch/${row.id}`))
    await loadPanel('merch')
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function claimRow(row) {
  return claim({ eventId: row.id, title: row.title })
}

const stats = computed(() => dashboard.value?.stats ?? [])
const claimable = computed(() => dashboard.value?.claimable ?? [])
const yourEvents = computed(() => dashboard.value?.yourEvents ?? [])
const listings = computed(() => dashboard.value?.listings ?? [])

async function loadDashboard() {
  error.value = ''
  try {
    dashboard.value = await api.get('/portal/dashboard')
    // The portal is scoped to whichever sponsor this account represents; the
    // heading should say which one rather than a hardcoded name.
    if (session.sponsorIds.length) {
      const all = await api.get('/sponsors')
      sponsorName.value = all.sponsors.find((x) => x.id)?.name ?? ''
    }
  } catch (e) {
    error.value = e.message
  }
}

async function loadPanel(id) {
  if (id === 'dashboard') return loadDashboard()
  error.value = ''
  try {
    panel.value = await api.get(`/portal/panels/${id}`)
  } catch (e) {
    error.value = e.message
    panel.value = null
  }
}

watch(portal, loadPanel, { immediate: true })

async function claim(row) {
  busy.value = true
  error.value = ''
  try {
    const res = await api.post(scoped(`/portal/events/${row.eventId ?? row.id}/claim`), {})
    flash(res.message)
    await Promise.all([loadDashboard(), loadPanel(portal.value)])
  } catch (e) {
    // 409 carries the reason the slot is not available.
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const portalTitle = computed(
  () => `${panel.value?.eyebrow || 'Dashboard'}${sponsorName.value ? ` · ${sponsorName.value}` : ''}`,
)
const portalHeading = computed(
  () => panel.value?.heading || (isDash.value ? 'Your term at a glance.' : ''),
)
</script>

<template>
  <main class="portal-layout">
    <aside class="eesoc-dark portal-aside">
      <div style="display:grid; gap:6px">
        <span class="eyebrow">Sponsor portal</span>
        <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-lg); color:var(--text-strong)">IMC Trading</span>
        <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted)">Package · 2026–27</span>
      </div>
      <div style="display:grid; gap:2px">
        <a
          v-for="p in portalNav"
          :key="p.id"
          href="#"
          class="portal-nav__link"
          :class="{ 'is-active': portal === p.id }"
          @click.prevent="portal = p.id"
        >{{ p.label }}</a>
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
        <div style="display:flex; gap:var(--space-3)">
          <Button @click="showJobForm = true">Post a job</Button>
          <Button variant="secondary" @click="showRequestForm = true">Request an event</Button>
        </div>
      </div>

      <!-- Shown on every panel, not just the dashboard: a failed claim or post
           on another tab was previously silent. -->
      <p v-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>
      <p v-if="notice" style="font:var(--type-small); color:var(--text-muted)">{{ notice }}</p>

      <!-- Dashboard -->
      <template v-if="isDash">
        <div class="grid-4" style="display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:var(--space-8)">
          <StatBlock
            v-for="(s, i) in stats"
            :key="i"
            :value="s.value"
            :label="s.label"
            :sub="s.sub"
          />
        </div>

        <div class="portal-cols" style="display:grid; grid-template-columns:minmax(0,1.4fr) minmax(0,1fr); gap:var(--space-10); align-items:start">
          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">Events you can sponsor</span>
            <Card v-for="(c, i) in claimable" :key="i" variant="default">
              <div class="row-between">
                <div style="display:grid; gap:4px">
                  <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-base); color:var(--text-strong)">{{ c.title }}</span>
                  <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted)">{{ c.meta }}</span>
                </div>
                <Button size="sm" variant="secondary" :disabled="busy" @click="claim(c)">Claim</Button>
              </div>
            </Card>

            <div style="padding-top:var(--space-2)">
              <span class="eyebrow" style="color:var(--text-brand)">Your events</span>
            </div>
            <p v-if="!yourEvents.length" style="margin:0; font:var(--type-small); color:var(--text-faint)">
              Nothing claimed yet. Claim a slot above and we'll agree the details by email.
            </p>
            <Card v-for="e in yourEvents" :key="e.id" variant="offset">
              <div class="row-between">
                <div style="display:grid; gap:4px">
                  <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-base); color:var(--text-strong)">{{ e.title }}</span>
                  <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted)">{{ e.meta }}</span>
                </div>
                <Button size="sm" variant="secondary" :href="`/events/${e.id}`">View</Button>
              </div>
            </Card>
          </div>

          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">Your live listings</span>
            <div v-for="(j, i) in listings" :key="i" style="display:grid; gap:6px; padding:var(--space-4) 0; border-bottom:1px solid var(--border-hairline)">
              <div class="row-between">
                <span style="font-family:var(--font-mono); font-size:var(--text-sm); color:var(--text-strong)">{{ j.title }}</span>
                <Badge :tone="j.tone">{{ j.status }}</Badge>
              </div>
              <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted)">{{ j.meta }}</span>
            </div>
          </div>
        </div>
      </template>

      <!-- Panel views -->
      <template v-else>
        <div class="portal-cols" style="display:grid; grid-template-columns:minmax(0,1.5fr) minmax(0,1fr); gap:var(--space-10); align-items:start">
          <div style="display:grid; gap:var(--space-4)">
            <span class="eyebrow">{{ panel.label }}</span>
            <Card v-for="(r, i) in panel.rows" :key="i" variant="default">
              <div class="row-between">
                <div style="display:grid; gap:4px">
                  <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-base); color:var(--text-strong)">{{ r.title }}</span>
                  <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted)">{{ r.meta }}</span>
                </div>
                <Button
                  v-if="r.action === 'Claim'"
                  size="sm"
                  variant="secondary"
                  :disabled="busy"
                  @click="claimRow(r)"
                >Claim</Button>
                <Button
                  v-else-if="r.action === 'Request'"
                  size="sm"
                  variant="secondary"
                  @click="() => { request.format = r.title; showRequestForm = true }"
                >Request</Button>
                <Button
                  v-else-if="r.action === 'Delete'"
                  size="sm"
                  variant="danger"
                  :disabled="busy"
                  @click="deleteAsset(r)"
                >Delete</Button>
                <Button
                  v-else-if="r.action === 'Edit' && portal === 'cv'"
                  size="sm"
                  variant="secondary"
                  @click="panelAction"
                >Edit</Button>
                <Button
                  v-else-if="r.action === 'Edit' || r.action === 'Repost'"
                  size="sm"
                  variant="secondary"
                  @click="() => { showJobForm = true; Object.assign(job, { title: r.title }) }"
                >{{ r.action }}</Button>
                <span v-else style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">{{ r.action }}</span>
              </div>
            </Card>
          </div>
          <Card variant="offset">
            <div style="display:grid; gap:var(--space-4)">
              <span class="eyebrow" style="letter-spacing:var(--tracking-label)">{{ panel.asideLabel }}</span>
              <p style="margin:0; font:var(--type-small); color:var(--text-body)">{{ panel.aside }}</p>
              <Button block :disabled="busy" @click="panelAction">{{ panel.cta }}</Button>
            </div>
          </Card>
        </div>
      </template>
    </section>
  </main>
</template>

<style scoped>
.eyebrow {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--text-accent);
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
}
.row-between {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-6);
  align-items: center;
}
</style>
