<script setup>
// This is deliberately in the committee admin, not a separate public-facing
// editor. All writes are capability-checked in the API.
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'

const emit = defineEmits(['open-email', 'changed'])
const rows = ref([])
const loading = ref(true)
const busyId = ref(null)
const notes = ref({})
const allowEdit = ref({})
const error = ref('')
const notice = ref('')
const showAll = ref(false)
const yearNames = {
  1: 'Year 1', 2: 'Year 2', 3: 'Year 3', 4: 'Year 4',
  msc: 'MSc', phd: 'PhD',
}
const pending = computed(() => rows.value.filter((r) => r.status === 'pending' || r.pendingRevision))
const proposed = (item) => item.pendingRevision || item
const visible = computed(() => showAll.value ? rows.value : pending.value)

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    rows.value = (await api.get('/admin/opportunities')).opportunities
    for (const row of rows.value) {
      if (!(row.id in notes.value)) notes.value[row.id] = ''
      if (!(row.id in allowEdit.value)) allowEdit.value[row.id] = false
    }
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function review(row, decision) {
  if (decision === 'approved' && !confirm(row.pendingRevision ? `Approve proposed changes to “${row.title}”? The updated card will replace the live one.` : `Approve and publish “${row.title}”? The submitter will then draft and approve the announcement before admins review it for sending.`)) return
  if (decision === 'rejected' && !confirm(row.pendingRevision ? `Reject only the proposed changes to “${row.title}”? The last approved version will stay online.` : `Reject “${row.title}”? The submitter will be able to read your notes.`)) return
  busyId.value = row.id
  notice.value = ''
  error.value = ''
  try {
    const result = await api.post(`/admin/opportunities/${row.id}/review`, {
      decision,
      notes: (notes.value[row.id] || '').trim() || null,
      allow_edit: decision === 'rejected' && !row.pendingRevision && Boolean(allowEdit.value[row.id]),
    })
    notice.value = row.pendingRevision
      ? (decision === 'approved' ? `Changes to “${row.title}” are now live.` : `Proposed changes to “${row.title}” were rejected. The approved listing remains public.`)
      : decision === 'approved'
        ? `“${row.title}” is published. The submitter must now draft and approve the email before it reaches the committee queue.`
        : `“${row.title}” was rejected. The submitter has been notified of the decision and notes.`
    await refresh()
    emit('changed')
    if (result.emailCampaignId) {
      // Keep the email on the queue — approving an opportunity must never
      // silently approve its audience or send bulk mail.
      emit('open-email', result.emailCampaignId)
    }
  } catch (e) { error.value = e.message; await refresh() }
  finally { busyId.value = null }
}

async function takedown(item, allow_edit) {
  const action = allow_edit ? 'Send back for changes' : 'Take down'
  const feedback = (notes.value[item.id] || '').trim()
  if (allow_edit && !feedback) { error.value = 'Add committee notes explaining the changes required before sending back.'; return }
  if (!confirm(`${action} “${item.title}”? This will immediately remove the public listing, including any changes awaiting review.`)) return
  busyId.value = item.id
  error.value = ''
  notice.value = ''
  try {
    await api.post(`/admin/opportunities/${item.id}/takedown`, { allow_edit, notes: feedback || null })
    notice.value = allow_edit
      ? `“${item.title}” has been taken down and returned to the submitter for changes.`
      : `“${item.title}” has been taken down.`
    await refresh()
    emit('changed')
  } catch (e) { error.value = e.message; await refresh() }
  finally { busyId.value = null }
}

function date(value) {
  if (!value) return ''
  return new Date(value).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

onMounted(refresh)
</script>

<template>
  <section class="opp-admin">
    <header class="opp-admin__header">
      <div>
        <h2>Opportunity submissions</h2>
        <p>Accounts with opportunity-review permissions can act on these submissions. Approval publishes the listing and invites the submitter to draft an email. Only after their sign-off does the announcement enter the separate committee Email queue.</p>
      </div>
      <div class="opp-admin__controls">
        <Badge :tone="pending.length ? 'danger' : 'neutral'">{{ pending.length }} pending</Badge>
        <Button variant="secondary" size="sm" @click="refresh">Refresh</Button>
        <Button variant="ghost" size="sm" @click="showAll = !showAll">{{ showAll ? 'Review queue' : 'Manage all listings' }}</Button>
      </div>
    </header>
    <p v-if="notice" class="opp-admin__notice" role="status">{{ notice }}</p>
    <p v-if="error" class="opp-admin__error" role="alert">{{ error }}</p>
    <p v-if="loading">Loading opportunities…</p>
    <Card v-for="item in visible" :key="item.id" variant="outline">
      <div class="opp-admin__row">
        <div>
          <h3>{{ proposed(item).title }}</h3>
          <p v-if="item.pendingRevision" class="opp-admin__meta">Proposed revision to “{{ item.title }}” · the approved version stays live during review.</p>
          <p class="opp-admin__meta">{{ item.pendingRevision ? 'Changes submitted ' + date(item.pendingRevisionAt) : 'Submitted ' + date(item.createdAt) }} by {{ item.submittedBy || 'former account' }}</p>
        </div>
        <Badge :tone="item.pendingRevision ? 'accent' : item.status === 'approved' ? 'success' : item.status === 'rejected' ? 'danger' : 'accent'">{{ item.pendingRevision ? 'pending changes' : item.status }}</Badge>
      </div>
      <p class="opp-admin__description">{{ proposed(item).description }}</p>
      <p v-if="proposed(item).postedUntil" class="opp-admin__meta">Posted until {{ date(proposed(item).postedUntil) }}</p>
      <p v-else class="opp-admin__meta">No posting deadline</p>
      <p class="opp-admin__meta">Target: {{ proposed(item).targetYears.map((year) => yearNames[year] || year).join(', ') }}</p>
      <div v-if="proposed(item).links.length" class="opp-admin__links"><a v-for="(link, i) in proposed(item).links" :key="i" :href="link" target="_blank" rel="noopener noreferrer">{{ link }} ↗</a></div>
      <div v-if="proposed(item).contactEmail || proposed(item).contactPhone" class="opp-admin__meta">Contact: {{ proposed(item).contactEmail || '—' }} {{ proposed(item).contactPhone || '' }}</div>
      <div v-if="proposed(item).images.length" class="opp-admin__gallery">
        <a v-for="img in proposed(item).images" :key="img.id" :href="img.url" target="_blank" rel="noopener noreferrer"><img :src="img.url" :alt="`Submitted image for ${item.title}`" loading="lazy" /></a>
      </div>
      <div v-if="item.pendingRevision" class="opp-admin__live">
        <strong>Currently approved listing (unchanged)</strong>
        <p class="opp-admin__meta"><strong>{{ item.title }}</strong> · {{ item.postedUntil ? 'Posted until ' + date(item.postedUntil) : 'No deadline' }}</p>
        <p class="opp-admin__description">{{ item.description }}</p>
        <router-link :to="`/opportunities/${item.id}`">View currently approved version ↗</router-link>
      </div>
      <template v-if="item.status === 'pending' || item.pendingRevision">
        <label class="opp-admin__notes">
          <strong>Committee notes (optional)</strong>
          <textarea v-model="notes[item.id]" rows="3" maxlength="3000" placeholder="Notes for the submitter if rejected, or feedback on approval…" />
        </label>
        <label v-if="!item.pendingRevision" class="opp-admin__check">
          <input v-model="allowEdit[item.id]" type="checkbox" />
          Let the submitter edit and resubmit this opportunity after rejection
        </label>
        <div class="opp-admin__controls">
          <Button v-if="can('opportunities.approve')" size="sm" :disabled="busyId !== null" @click="review(item, 'approved')">{{ item.pendingRevision ? 'Approve changes' : 'Approve & publish' }}</Button>
          <Button v-if="can('opportunities.reject')" size="sm" variant="danger" :disabled="busyId !== null" @click="review(item, 'rejected')">{{ item.pendingRevision ? 'Reject changes' : 'Reject' }}</Button>
        </div>
      </template>
      <template v-else>
        <p v-if="item.reviewNotes" class="opp-admin__meta">Committee notes: {{ item.reviewNotes }}</p>
        <p v-if="item.status === 'rejected' && item.allowEdit" class="opp-admin__meta">The submitter may edit and resubmit this opportunity.</p>
        <p v-if="item.status === 'approved' && !item.emailCampaignId" class="opp-admin__meta">Awaiting submitter's announcement draft and sign-off.</p>
        <div v-if="item.status === 'approved'" class="opp-admin__controls">
          <router-link :to="`/opportunities/${item.id}`">View published listing ↗</router-link>
          <Button v-if="item.emailCampaignId && can('email.queue.view')" variant="secondary" size="sm" @click="emit('open-email', item.emailCampaignId)">Open email announcement</Button>
        </div>
      </template>
      <template v-if="item.status === 'approved' && (can('opportunities.unpublish') || can('opportunities.return_for_changes'))">
        <label v-if="!item.pendingRevision" class="opp-admin__notes">
          <strong>Reason / feedback for taking this listing down</strong>
          <textarea v-model="notes[item.id]" rows="3" maxlength="3000" placeholder="Explain what needs fixing, particularly when sending it back…" />
        </label>
        <div class="opp-admin__controls">
          <Button v-if="can('opportunities.return_for_changes')" size="sm" variant="secondary" :disabled="busyId !== null" @click="takedown(item, true)">Take down & send back for changes</Button>
          <Button v-if="can('opportunities.unpublish')" size="sm" variant="danger" :disabled="busyId !== null" @click="takedown(item, false)">Take down</Button>
        </div>
      </template>
    </Card>
    <Card v-if="!loading && !visible.length" variant="outline"><p>{{ showAll ? 'No submissions yet.' : 'No opportunities waiting for approval.' }}</p></Card>
  </section>
</template>

<style scoped>
.opp-admin { display: grid; gap: var(--space-5); }
.opp-admin__header, .opp-admin__row { display: flex; justify-content: space-between; align-items: start; flex-wrap: wrap; gap: var(--space-4); }
.opp-admin h2 { margin: 0; font: var(--type-h3); }
.opp-admin h3 { margin: 0 0 var(--space-2); font: var(--type-h4); }
.opp-admin__header p { font: var(--type-small); color: var(--text-muted); max-width: 80ch; }
.opp-admin__controls { display: flex; gap: var(--space-3); align-items: center; flex-wrap: wrap; }
.opp-admin__meta { font: var(--type-small); color: var(--text-muted); white-space: pre-line; overflow-wrap: anywhere; }
.opp-admin__live { border: 1px solid var(--border-default); border-radius: var(--radius-sm); padding: var(--space-3); display: grid; gap: var(--space-2); }
.opp-admin__description { font: var(--type-small); white-space: pre-line; overflow-wrap: anywhere; line-height: 1.65; }
.opp-admin__links { display: grid; gap: var(--space-2); overflow-wrap: anywhere; }
.opp-admin__links a, .opp-admin a { color: var(--text-accent); font: var(--type-small); }
.opp-admin__gallery { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.opp-admin__gallery img { max-width: 180px; max-height: 130px; object-fit: cover; border-radius: var(--radius-sm); }
.opp-admin__check { display: flex; gap: var(--space-2); align-items: center; font: var(--type-small); cursor: pointer; }
.opp-admin__notes { display: grid; gap: var(--space-2); font: var(--type-small); }
.opp-admin__notes textarea { width: 100%; padding: var(--space-3); background: var(--surface-card); color: var(--text-body); border: 1px solid var(--border-default); border-radius: var(--radius-sm); font: var(--type-small); }
.opp-admin__notice { font: var(--type-small); color: var(--text-strong); border-left: 3px solid var(--text-accent); padding: var(--space-3); }
.opp-admin__error { font: var(--type-small); color: var(--danger); }
</style>
