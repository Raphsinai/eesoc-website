<script setup>
// One sponsor's agreement and everything that flows from it: which clauses they
// signed and how much of each is used, the events linked to them, what they
// have asked for, and the marketing waiting to go out.
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'

const route = useRoute()
const id = computed(() => Number(route.params.id))

const data = ref(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')

// Editable copies, keyed by clause / request / marketing id, so typing in one
// row does not wait on a save in another.
const termEdits = reactive({})
const requestEdits = reactive({})
const marketingEdits = reactive({})
const previews = reactive({})
const link = reactive({ eventId: '', clause: '' })
const replies = reactive({})
const newThread = reactive({ body: '' })
const confirmClause = reactive({})

function flash(message) {
  notice.value = message
  error.value = ''
  setTimeout(() => (notice.value = ''), 6000)
}

async function load() {
  error.value = ''
  try {
    data.value = await api.get(`/admin/sponsors/${id.value}/detail`)
    for (const t of data.value.terms) {
      termEdits[t.key] = {
        agreed: t.agreed,
        // Their own figure only. Empty means the standard package's, so a
        // later change to the standard reaches them — prefilling the number
        // would pin them to today's standard the first time a note is saved.
        allowance: t.allowanceOverride ?? '',
        note: t.note,
        done: Boolean(t.doneAt),
      }
    }
    for (const r of data.value.requests) {
      requestEdits[r.id] = { status: r.status, response: r.response || '' }
    }
    for (const m of data.value.marketing) {
      marketingEdits[m.id] = { subject: m.subject, body: m.body, response: '' }
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  // Arriving from the inbox with #marketing and the like.
  if (route.hash) {
    await nextTick()
    document.querySelector(route.hash)?.scrollIntoView({ behavior: 'smooth' })
  }
})

async function act(fn, message) {
  busy.value = true
  error.value = ''
  try {
    const res = await fn()
    if (message || res?.message) flash(res?.message || message)
    await load()
    return res
  } catch (e) {
    error.value = e.message
    return null
  } finally {
    busy.value = false
  }
}

const eesocTerms = computed(() => (data.value?.terms ?? []).filter((t) => t.side === 'eesoc'))
const sponsorTerms = computed(() => (data.value?.terms ?? []).filter((t) => t.side === 'sponsor'))
const clauseLabel = (key) => data.value?.eventClauses.find((c) => c.key === key)?.label ?? 'Outside the package'
const audienceLabel = (key) => data.value?.audiences.find((a) => a.key === key)?.label ?? key

const linked = computed(() => (data.value?.claims ?? []).filter((c) => c.status === 'confirmed'))
const interested = computed(() => (data.value?.claims ?? []).filter((c) => c.status === 'interested'))
const declined = computed(() => (data.value?.claims ?? []).filter((c) => c.status === 'declined'))

function saveTerm(t) {
  const e = termEdits[t.key]
  return act(
    () =>
      api.put(`/admin/sponsors/${id.value}/terms/${t.key}`, {
        agreed: e.agreed,
        allowance: t.countable && e.allowance !== '' && e.allowance !== null ? Number(e.allowance) : null,
        note: e.note || '',
        done: Boolean(e.done),
      }),
    `${t.label} saved.`,
  )
}

function linkEvent() {
  if (!link.eventId) return
  return act(
    () =>
      api.put(`/admin/sponsors/${id.value}/events/${link.eventId}`, {
        clause: link.clause || null,
      }),
    'Event linked.',
  ).then(() => Object.assign(link, { eventId: '', clause: '' }))
}

function confirmInterest(c) {
  return act(
    () =>
      api.put(`/admin/sponsors/${id.value}/events/${c.eventId}`, {
        clause: confirmClause[c.eventId] || null,
      }),
    `${c.title} confirmed for ${data.value.sponsor.name}.`,
  )
}

function declineInterest(c) {
  if (!confirm(`Decline ${data.value.sponsor.name}'s interest in ${c.title}?`)) return
  return act(() => api.post(`/admin/sponsors/${id.value}/events/${c.eventId}/decline`), 'Declined.')
}

function unlink(c) {
  if (!confirm(`Unlink ${c.title} from ${data.value.sponsor.name}?`)) return
  return act(() => api.del(`/admin/sponsors/${id.value}/events/${c.eventId}`), 'Unlinked.')
}

function relink(c, clause) {
  return act(
    () => api.put(`/admin/sponsors/${id.value}/events/${c.eventId}`, { clause: clause || null }),
    'Saved.',
  )
}

function saveRequest(r) {
  const e = requestEdits[r.id]
  return act(
    () => api.patch(`/admin/sponsor-requests/${r.id}`, { status: e.status, response: e.response }),
    'Request saved. The sponsor sees your reply in their portal.',
  )
}

function saveMarketing(m) {
  const e = marketingEdits[m.id]
  return act(
    () => api.patch(`/admin/sponsor-marketing/${m.id}`, { subject: e.subject, body: e.body }),
    'Saved.',
  )
}

async function previewMarketing(m) {
  try {
    previews[m.id] = await api.post(`/admin/sponsor-marketing/${m.id}/preview`)
  } catch (e) {
    error.value = e.message
  }
}

function sendMarketing(m) {
  const p = previews[m.id]
  const who = p ? `${p.recipients} recipient(s)` : m.audiences.map(audienceLabel).join(', ')
  if (!confirm(`Email "${m.subject}" to ${who}? This cannot be undone.`)) return
  return act(() => api.post(`/admin/sponsor-marketing/${m.id}/send`))
}

function markPosted(m) {
  return act(() => api.post(`/admin/sponsor-marketing/${m.id}/posted`), 'Marked as posted on social media.')
}

function completeMarketing(m) {
  return act(
    () => api.post(`/admin/sponsor-marketing/${m.id}/complete`, { response: marketingEdits[m.id].response }),
    'Marked as shared.',
  )
}

function declineMarketing(m) {
  if (!marketingEdits[m.id].response.trim()) {
    error.value = 'Write the reason in the reply box first — the sponsor will see it.'
    return
  }
  return act(
    () => api.post(`/admin/sponsor-marketing/${m.id}/decline`, { response: marketingEdits[m.id].response }),
    'Declined. The share goes back into their allowance.',
  )
}

const TONE = {
  open: 'accent', in_progress: 'brand', closed: 'neutral',
  pending: 'accent', shared: 'success', declined: 'neutral', withdrawn: 'neutral',
}

function fmt(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

const isPdf = (url) => url.toLowerCase().endsWith('.pdf')

function when(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

async function reply(topic, text) {
  if (!text?.trim()) return null
  return act(() => api.post(`/admin/sponsors/${id.value}/notes`, { topic, body: text }))
}

async function sendReply(t) {
  if (await reply(t.topic, replies[t.topic])) replies[t.topic] = ''
}

async function startThread() {
  if (await reply('general', newThread.body)) newThread.body = ''
}
</script>

<template>
  <main class="sponsor-admin">
    <RouterLink to="/admin?tab=sponsors" style="font:var(--type-small)">← All sponsors</RouterLink>

    <p v-if="loading" style="font:var(--type-small); color:var(--text-faint)">Loading…</p>
    <p v-if="error" class="sticky-msg" style="color:var(--danger)">{{ error }}</p>
    <p v-if="notice" class="sticky-msg" style="color:var(--text-muted)">{{ notice }}</p>

    <template v-if="data">
      <header class="head">
        <div style="display:grid; gap:var(--space-2)">
          <h1 style="margin:0; font:var(--type-h2)">
            {{ data.sponsor.name }}
            <Badge :tone="data.sponsor.active ? 'success' : 'neutral'">{{ data.sponsor.active ? 'Live' : 'Draft' }}</Badge>
          </h1>
          <span style="font:var(--type-small); color:var(--text-muted)">
            {{ data.sponsor.sector || 'No sector' }} · {{ data.sponsor.amount }}
            <template v-if="data.sponsor.agreement_year"> · agreement {{ data.sponsor.agreement_year }}</template>
            · usage counted for {{ data.year }}
          </span>
        </div>
        <Button variant="secondary" :href="`/portal?sponsor=${data.sponsor.id}`">Open their portal</Button>
      </header>

      <!-- ======================= Questions and notes ====================== -->
      <section id="notes" class="section">
        <h2 class="section__title">Questions and notes</h2>
        <p class="section__help">
          Everything {{ data.sponsor.name }} has asked or noted, by what it is about.
          Each one emailed the committee when it arrived. Replies show in their portal,
          and their portal users are emailed to say there is one.
        </p>
        <p v-if="!data.notes.length" class="meta">Nothing yet.</p>
        <Card v-for="t in data.notes" :key="t.topic" variant="outline">
          <div style="display:grid; gap:var(--space-3)">
            <strong>{{ t.label }} <Badge v-if="t.unread" tone="danger">{{ t.unread }} new</Badge></strong>
            <div v-for="n in t.notes" :key="n.id" class="note" :class="{ 'note--ours': n.fromCommittee }">
              <span class="meta">{{ n.author }}<template v-if="n.fromCommittee"> (committee)</template> · {{ when(n.at) }}</span>
              <p>{{ n.body }}</p>
            </div>
            <textarea v-model="replies[t.topic]" rows="2" class="ees-textarea" placeholder="Reply" />
            <div><Button size="sm" :disabled="busy || !(replies[t.topic] || '').trim()" @click="sendReply(t)">Reply</Button></div>
          </div>
        </Card>
        <details>
          <summary style="font:var(--type-small); cursor:pointer">Write to {{ data.sponsor.name }} about something new</summary>
          <div style="display:grid; gap:var(--space-2); margin-top:var(--space-3)">
            <textarea v-model="newThread.body" rows="3" class="ees-textarea" placeholder="Filed under General" />
            <div><Button size="sm" :disabled="busy || !newThread.body.trim()" @click="startThread">Send</Button></div>
          </div>
        </details>
      </section>

      <!-- ======================= What EESoc provides ======================= -->
      <section class="section">
        <h2 class="section__title">Agreement — what EESoc provides</h2>
        <p class="section__help">
          Every sponsor starts on the standard 2026–27 package. Untick a clause they
          did not sign and the portal stops offering it; give them their own
          allowance and the portal counts against that instead of the standard
          (leave it empty to follow the standard, which is set under Sponsors in
          the dashboard). Used counts come from the events linked below and the
          marketing shared this year. The sponsor sees each clause's name and
          usage, never the wording.
        </p>
        <Card v-for="t in eesocTerms" :key="t.key" variant="outline" :class="{ struck: !termEdits[t.key]?.agreed }">
          <div class="term">
            <label class="term__agreed">
              <input v-model="termEdits[t.key].agreed" type="checkbox" />
              <strong>{{ t.label }}</strong>
            </label>
            <div class="term__usage">
              <template v-if="t.countable">
                <span :class="{ over: t.used > t.allowance }">{{ t.used }} of {{ t.allowance }} used</span>
                <label class="term__allow">
                  Allowance
                  <input
                    v-model.number="termEdits[t.key].allowance"
                    type="number"
                    min="0"
                    class="ees-input term__num"
                    :placeholder="String(t.standardAllowance)"
                    :title="`Leave empty for the standard package (${t.standardAllowance})`"
                  />
                </label>
                <span v-if="t.allowanceOverride === null" style="color:var(--text-faint)">standard</span>
              </template>
              <label v-else class="term__allow">
                <input v-model="termEdits[t.key].done" type="checkbox" /> Delivered
                <span v-if="t.doneAt" style="color:var(--text-faint)">{{ fmt(t.doneAt) }}</span>
              </label>
            </div>
            <Input v-model="termEdits[t.key].note" placeholder="Note — shown to the sponsor too" />
            <div class="term__save">
              <Button size="sm" :disabled="busy" @click="saveTerm(t)">Save</Button>
              <span v-if="!t.customised" style="font:var(--type-2xs); color:var(--text-faint)">Standard</span>
            </div>
            <details class="term__text"><summary>Agreement wording</summary>{{ t.text }}</details>
          </div>
        </Card>
      </section>

      <!-- ===================== What the sponsor provides ==================== -->
      <section class="section">
        <h2 class="section__title">Agreement — what {{ data.sponsor.name }} provides</h2>
        <p class="section__help">Tick each one off as it arrives. The sponsor sees what is still outstanding.</p>
        <Card v-for="t in sponsorTerms" :key="t.key" variant="outline">
          <div class="term">
            <label class="term__agreed">
              <input v-model="termEdits[t.key].done" type="checkbox" />
              <strong>{{ t.label }}</strong>
            </label>
            <span class="term__usage">{{ t.doneAt ? `Received ${fmt(t.doneAt)}` : 'Outstanding' }}</span>
            <Input v-model="termEdits[t.key].note" placeholder="Note" />
            <div class="term__save"><Button size="sm" :disabled="busy" @click="saveTerm(t)">Save</Button></div>
            <details class="term__text"><summary>Agreement wording</summary>{{ t.text }}</details>
          </div>
        </Card>
      </section>

      <!-- ============================== Events ============================= -->
      <section id="events" class="section">
        <h2 class="section__title">Events</h2>

        <Card variant="offset">
          <div style="display:grid; gap:var(--space-3)">
            <strong style="font:var(--type-h5)">Link an existing event</strong>
            <div class="link-form">
              <Field label="Event">
                <select v-model="link.eventId" class="ees-select">
                  <option value="">Choose an event…</option>
                  <option v-for="e in data.events" :key="e.id" :value="e.id">
                    {{ e.title }} · {{ e.date }}{{ e.published ? '' : ' (draft)' }}
                  </option>
                </select>
              </Field>
              <Field label="Delivers">
                <select v-model="link.clause" class="ees-select">
                  <option value="">Outside the package</option>
                  <option v-for="c in data.eventClauses" :key="c.key" :value="c.key">{{ c.label }}</option>
                </select>
              </Field>
              <Button :disabled="busy || !link.eventId" @click="linkEvent">Link</Button>
            </div>
            <span style="font:var(--type-2xs); color:var(--text-faint)">
              A linked event counts against the clause it delivers, shows on the sponsor's
              portal and — once both are published — on the event page and their sponsor
              card. It also comes off every other sponsor's list.
            </span>
          </div>
        </Card>

        <div v-if="interested.length" style="display:grid; gap:var(--space-3)">
          <span class="label">Interest registered — waiting on you</span>
          <Card v-for="c in interested" :key="c.eventId" variant="outline">
            <div class="row">
              <div style="display:grid; gap:2px">
                <strong>{{ c.title }}</strong>
                <span class="meta">{{ c.date }}<template v-if="c.note"> · “{{ c.note }}”</template></span>
              </div>
              <div class="row__actions">
                <select v-model="confirmClause[c.eventId]" class="ees-select" style="width:auto">
                  <option :value="undefined">Outside the package</option>
                  <option v-for="cl in data.eventClauses" :key="cl.key" :value="cl.key">{{ cl.label }}</option>
                </select>
                <Button size="sm" :disabled="busy" @click="confirmInterest(c)">Confirm</Button>
                <Button size="sm" variant="ghost" :disabled="busy" @click="declineInterest(c)">Decline</Button>
              </div>
            </div>
          </Card>
        </div>

        <span class="label">Linked</span>
        <p v-if="!linked.length" class="meta">No events linked yet.</p>
        <Card v-for="c in linked" :key="c.eventId" variant="outline">
          <div class="row">
            <div style="display:grid; gap:2px">
              <strong>
                <RouterLink :to="`/events/${c.slug}`">{{ c.title }}</RouterLink>
                <Badge v-if="!c.published" tone="neutral">Draft</Badge>
              </strong>
              <span class="meta">{{ c.date }}<template v-if="!c.thisYear"> · not counted this year</template></span>
            </div>
            <div class="row__actions">
              <select :value="c.clause || ''" class="ees-select" style="width:auto" @change="relink(c, $event.target.value)">
                <option value="">Outside the package</option>
                <option v-for="cl in data.eventClauses" :key="cl.key" :value="cl.key">{{ cl.label }}</option>
              </select>
              <Button size="sm" variant="ghost" :disabled="busy" @click="unlink(c)">Unlink</Button>
            </div>
          </div>
        </Card>
        <p v-if="declined.length" class="meta">
          Declined: {{ declined.map((c) => c.title).join(', ') }}
        </p>
      </section>

      <!-- ============================= Requests ============================ -->
      <section id="requests" class="section">
        <h2 class="section__title">Event requests</h2>
        <p v-if="!data.requests.length" class="meta">They haven't asked for anything yet.</p>
        <Card v-for="r in data.requests" :key="r.id" variant="outline">
          <div style="display:grid; gap:var(--space-3)">
            <div class="row">
              <strong>{{ r.format || 'Request' }}</strong>
              <span class="meta">{{ fmt(r.created_at) }}<template v-if="r.timing"> · {{ r.timing }}</template></span>
            </div>
            <p style="margin:0; font:var(--type-small); white-space:pre-line">{{ r.body }}</p>
            <div class="link-form">
              <Field label="Status">
                <select v-model="requestEdits[r.id].status" class="ees-select">
                  <option value="open">Not answered</option>
                  <option value="in_progress">In discussion</option>
                  <option value="closed">Closed</option>
                </select>
              </Field>
              <Field label="Reply" help="The sponsor sees this in their portal." style="grid-column: span 2">
                <textarea v-model="requestEdits[r.id].response" rows="2" class="ees-textarea" />
              </Field>
            </div>
            <div><Button size="sm" :disabled="busy" @click="saveRequest(r)">Save</Button></div>
          </div>
        </Card>
      </section>

      <!-- ============================ Marketing ============================ -->
      <section id="marketing" class="section">
        <h2 class="section__title">Marketing</h2>
        <p class="section__help">
          {{ data.terms.find((t) => t.key === 'marketing')?.used }} of
          {{ data.terms.find((t) => t.key === 'marketing')?.allowance }} shares used for {{ data.year }}.
          Change the allowance under the agreement above.
        </p>
        <p v-if="!data.marketing.length" class="meta">Nothing submitted yet.</p>
        <Card v-for="m in data.marketing" :key="m.id" variant="outline">
          <div style="display:grid; gap:var(--space-3)">
            <div class="row">
              <strong>{{ m.subject }}</strong>
              <Badge :tone="TONE[m.status]">{{ m.status }}</Badge>
            </div>
            <span class="meta">
              Submitted {{ fmt(m.created_at) }} · {{ m.channels.join(' + ') }} ·
              {{ m.audiences.map(audienceLabel).join(', ') }}
              <template v-if="m.preferred_date"> · hoping for {{ fmt(m.preferred_date) }}</template>
              <template v-if="m.emailed_at"> · emailed {{ fmt(m.emailed_at) }}</template>
              <template v-if="m.posted_at"> · posted {{ fmt(m.posted_at) }}</template>
            </span>

            <template v-if="m.status === 'pending'">
              <Field label="Subject"><Input v-model="marketingEdits[m.id].subject" /></Field>
              <Field label="Message"><textarea v-model="marketingEdits[m.id].body" rows="6" class="ees-textarea" /></Field>
            </template>
            <p v-else style="margin:0; font:var(--type-small); white-space:pre-line">{{ m.body }}</p>

            <div v-if="m.media.length" class="media-strip">
              <a v-for="u in m.media" :key="u" :href="u" target="_blank" rel="noopener" class="media-thumb">
                <span v-if="isPdf(u)">PDF</span>
                <img v-else :src="u" alt="" />
              </a>
            </div>
            <p v-if="m.response" class="meta">Reply: {{ m.response }}</p>

            <template v-if="m.status === 'pending'">
              <div class="row__actions" style="justify-content:flex-start">
                <Button size="sm" variant="ghost" :disabled="busy" @click="saveMarketing(m)">Save edits</Button>
                <Button size="sm" variant="secondary" :disabled="busy" @click="previewMarketing(m)">Preview email</Button>
                <Button
                  v-if="m.channels.includes('email') && !m.emailed_at"
                  size="sm"
                  :disabled="busy || !data.canEmail"
                  :title="data.canEmail ? '' : 'Sending needs a committee member with email access'"
                  @click="sendMarketing(m)"
                >Send email</Button>
                <Button
                  v-if="m.channels.includes('social') && !m.posted_at"
                  size="sm"
                  variant="secondary"
                  :disabled="busy"
                  @click="markPosted(m)"
                >Posted on social</Button>
              </div>
              <span v-if="!data.canEmail && m.channels.includes('email')" class="meta">
                Sending the email needs a committee member with email access.
              </span>
              <div v-if="previews[m.id]" style="display:grid; gap:var(--space-2)">
                <span class="meta">
                  {{ previews[m.id].recipients }} recipient(s)<template v-if="previews[m.id].lists.length">, including the list(s) {{ previews[m.id].lists.join(', ') }}</template>.
                  Save edits before previewing to see them.
                </span>
                <div class="mail-preview" v-html="previews[m.id].html" />
              </div>
              <Field label="Reply to the sponsor" help="Needed to decline; optional when marking it shared some other way.">
                <textarea v-model="marketingEdits[m.id].response" rows="2" class="ees-textarea" />
              </Field>
              <div class="row__actions" style="justify-content:flex-start">
                <Button size="sm" variant="ghost" :disabled="busy" @click="completeMarketing(m)">Mark shared</Button>
                <Button v-if="!m.emailed_at" size="sm" variant="danger" :disabled="busy" @click="declineMarketing(m)">Decline</Button>
              </div>
            </template>
          </div>
        </Card>
      </section>

      <!-- ============================= Contacts ============================ -->
      <section class="section">
        <h2 class="section__title">Contacts and access</h2>
        <div class="two">
          <Card variant="outline">
            <div style="display:grid; gap:var(--space-2)">
              <span class="label">Shown on their card</span>
              <p v-if="!data.contacts.length" class="meta">None yet.</p>
              <span v-for="c in data.contacts" :key="c.id" style="font:var(--type-small)">
                {{ c.label || (c.kind === 'email' ? 'Email' : 'Link') }} — {{ c.value }}
              </span>
              <RouterLink :to="`/portal?sponsor=${data.sponsor.id}`" style="font:var(--type-2xs)">Edit in their portal →</RouterLink>
            </div>
          </Card>
          <Card variant="outline">
            <div style="display:grid; gap:var(--space-2)">
              <span class="label">Portal access</span>
              <p v-if="!data.users.length" class="meta">Nobody yet — grant access on the Sponsors tab.</p>
              <span v-for="u in data.users" :key="u.id" style="font:var(--type-small)">{{ u.email }}</span>
            </div>
          </Card>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.sponsor-admin {
  max-width: var(--container-xl, 1200px);
  margin: 0 auto;
  padding: var(--space-10) var(--space-6) var(--space-24);
  display: grid;
  gap: var(--space-8);
  min-height: 70vh;
}
.sticky-msg {
  position: sticky;
  top: var(--space-4);
  z-index: 2;
  margin: 0;
  padding: var(--space-3) var(--space-4);
  background: var(--surface-card);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-sm);
  font: var(--type-small);
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: var(--space-6);
  flex-wrap: wrap;
}
.section {
  display: grid;
  gap: var(--space-3);
  scroll-margin-top: var(--space-16);
}
.section__title {
  margin: 0;
  font: var(--type-h4);
}
.section__help {
  margin: 0;
  font: var(--type-small);
  color: var(--text-muted);
  max-width: 72ch;
}
.label {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: var(--text-muted);
}
.meta {
  margin: 0;
  font: var(--type-2xs);
  color: var(--text-faint);
}
/* One row per clause on a wide screen: what it is, how much is used, a note,
   save — with the agreement's own wording folded underneath. */
.term {
  display: grid;
  grid-template-columns: minmax(200px, 1.3fr) minmax(220px, auto) minmax(180px, 2fr) auto;
  gap: var(--space-2) var(--space-4);
  align-items: center;
}
.term__save {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}
.term__text {
  grid-column: 1 / -1;
}
.term__agreed {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  font: var(--type-small);
}
.term__usage {
  display: flex;
  gap: var(--space-5);
  align-items: center;
  flex-wrap: wrap;
  font: var(--type-small);
}
.term__usage .over {
  color: var(--danger);
}
.term__allow {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  font: var(--type-small);
}
.term__num {
  width: 80px;
}
.term__text {
  font: var(--type-2xs);
  color: var(--text-muted);
}
.term__text summary {
  cursor: pointer;
}
.struck strong {
  text-decoration: line-through;
  color: var(--text-faint);
}
.row {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: center;
  flex-wrap: wrap;
}
.row__actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.link-form {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) auto;
  gap: var(--space-3);
  align-items: end;
}
.two {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-4);
}
.media-strip {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.media-thumb {
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
.mail-preview {
  padding: var(--space-5);
  background: var(--white);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-sm);
  max-width: 640px;
}
.note {
  display: grid;
  gap: 4px;
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--surface-page);
}
.note--ours {
  background: var(--surface-brand-subtle);
}
.note p {
  margin: 0;
  font: var(--type-small);
  white-space: pre-line;
}
@media (max-width: 900px) {
  .term {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 720px) {
  .link-form {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
