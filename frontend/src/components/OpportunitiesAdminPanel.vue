<script setup>
// This is deliberately in the committee admin, not a separate public-facing
// editor. All writes are capability-checked in the API.
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'

const emit = defineEmits(['open-email'])
const rows = ref([])
const loading = ref(true)
const busyId = ref(null)
const notes = ref({})
const error = ref('')
const notice = ref('')
const showAll = ref(false)
const yearNames = {
  1: 'Year 1', 2: 'Year 2', 3: 'Year 3', 4: 'Year 4',
  msc: 'MSc', phd: 'PhD',
}
const pending = computed(() => rows.value.filter((r) => r.status === 'pending'))
const visible = computed(() => showAll.value ? rows.value : pending.value)

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    rows.value = (await api.get('/admin/opportunities')).opportunities
    for (const row of rows.value) {
      if (!(row.id in notes.value)) notes.value[row.id] = row.reviewNotes || ''
    }
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function review(row, decision) {
  if (decision === 'approved' && !confirm(`Approve “${row.title}” and create an email announcement draft?`)) return
  if (decision === 'rejected' && !confirm(`Reject “${row.title}”? The submitter will be able to read your notes.`)) return
  busyId.value = row.id
  notice.value = ''
  error.value = ''
  try {
    const result = await api.post(`/admin/opportunities/${row.id}/review`, {
      decision,
      notes: (notes.value[row.id] || '').trim() || null,
    })
    notice.value = decision === 'approved'
      ? `“${row.title}” is now published. An announcement draft is waiting for separate email approval.`
      : `“${row.title}” was rejected. The submitter can see its status and your notes.`
    await refresh()
    if (result.emailCampaignId) {
      // Keep the email on the queue — approving an opportunity must never
      // silently approve its audience or send bulk mail.
      emit('open-email', result.emailCampaignId)
    }
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
        <p>Review submissions before they appear on the website. Approval also creates an email draft for a separate decision in the Email tab; it never sends automatically.</p>
      </div>
      <div class="opp-admin__controls">
        <Badge :tone="pending.length ? 'danger' : 'neutral'">{{ pending.length }} pending</Badge>
        <Button variant="secondary" size="sm" @click="refresh">Refresh</Button>
        <Button variant="ghost" size="sm" @click="showAll = !showAll">{{ showAll ? 'Pending only' : 'All submissions' }}</Button>
      </div>
    </header>
    <p v-if="notice" class="opp-admin__notice" role="status">{{ notice }}</p>
    <p v-if="error" class="opp-admin__error" role="alert">{{ error }}</p>
    <p v-if="loading">Loading opportunities…</p>
    <Card v-for="item in visible" :key="item.id" variant="outline">
      <div class="opp-admin__row">
        <div>
          <h3>{{ item.title }}</h3>
          <p class="opp-admin__meta">Submitted {{ date(item.createdAt) }} by {{ item.submittedBy || 'former account' }}</p>
        </div>
        <Badge :tone="item.status === 'approved' ? 'success' : item.status === 'rejected' ? 'danger' : 'accent'">{{ item.status }}</Badge>
      </div>
      <p class="opp-admin__description">{{ item.description }}</p>
      <p class="opp-admin__meta">Target: {{ item.targetYears.map((year) => yearNames[year] || year).join(', ') }}</p>
      <div v-if="item.links.length" class="opp-admin__links"><a v-for="(link, i) in item.links" :key="i" :href="link" target="_blank" rel="noopener noreferrer">{{ link }} ↗</a></div>
      <div v-if="item.contactEmail || item.contactPhone" class="opp-admin__meta">Contact: {{ item.contactEmail || '—' }} {{ item.contactPhone || '' }}</div>
      <div v-if="item.images.length" class="opp-admin__gallery">
        <a v-for="img in item.images" :key="img.id" :href="img.url" target="_blank" rel="noopener noreferrer"><img :src="img.url" :alt="`Submitted image for ${item.title}`" loading="lazy" /></a>
      </div>
      <template v-if="item.status === 'pending'">
        <label class="opp-admin__notes">
          <strong>Committee notes (optional)</strong>
          <textarea v-model="notes[item.id]" rows="3" maxlength="3000" placeholder="Notes for the submitter if rejected, or feedback on approval…" />
        </label>
        <div class="opp-admin__controls">
          <Button size="sm" :disabled="busyId !== null" @click="review(item, 'approved')">Approve & queue announcement</Button>
          <Button size="sm" variant="danger" :disabled="busyId !== null" @click="review(item, 'rejected')">Reject</Button>
        </div>
      </template>
      <template v-else>
        <p v-if="item.reviewNotes" class="opp-admin__meta">Committee notes: {{ item.reviewNotes }}</p>
        <div v-if="item.status === 'approved'" class="opp-admin__controls">
          <router-link :to="`/opportunities/${item.id}`">View published listing ↗</router-link>
          <Button v-if="item.emailCampaignId" variant="secondary" size="sm" @click="emit('open-email', item.emailCampaignId)">Open email announcement</Button>
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
.opp-admin__description { font: var(--type-small); white-space: pre-line; overflow-wrap: anywhere; line-height: 1.65; }
.opp-admin__links { display: grid; gap: var(--space-2); overflow-wrap: anywhere; }
.opp-admin__links a, .opp-admin a { color: var(--text-accent); font: var(--type-small); }
.opp-admin__gallery { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.opp-admin__gallery img { max-width: 180px; max-height: 130px; object-fit: cover; border-radius: var(--radius-sm); }
.opp-admin__notes { display: grid; gap: var(--space-2); font: var(--type-small); }
.opp-admin__notes textarea { width: 100%; padding: var(--space-3); background: var(--surface-card); color: var(--text-body); border: 1px solid var(--border-default); border-radius: var(--radius-sm); font: var(--type-small); }
.opp-admin__notice { font: var(--type-small); color: var(--text-strong); border-left: 3px solid var(--text-accent); padding: var(--space-3); }
.opp-admin__error { font: var(--type-small); color: var(--danger); }
</style>
