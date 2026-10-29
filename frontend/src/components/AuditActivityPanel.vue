<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import Button from '@/components/ds/Button.vue'

const rows = ref([])
const busy = ref(false)
const error = ref('')
const search = ref('')
const includeRequests = ref(false)
const opened = ref({})

function value(v) {
  if (v === null || v === undefined) return '—'
  if (Array.isArray(v)) return v.length ? v.join(', ') : '(none)'
  if (typeof v === 'object') return JSON.stringify(v)
  if (v === true) return 'Yes'
  if (v === false) return 'No'
  return String(v)
}
function dataObject(raw) {
  if (!raw) return {}
  if (typeof raw === 'string') {
    try { return JSON.parse(raw) } catch { return { message: raw } }
  }
  return typeof raw === 'object' ? raw : {}
}
function changes(entry) {
  const before = dataObject(entry.before_data)
  const after = dataObject(entry.after_data)
  const keys = [...new Set([...Object.keys(before),...Object.keys(after)])]
    .filter(k => k !== 'request_id' && k !== '_request_id' && k !== 'origin' && k !== 'route' && k !== 'method' && k !== 'status')
  return keys.filter(k => JSON.stringify(before[k]) !== JSON.stringify(after[k]) || !Object.prototype.hasOwnProperty.call(before,k))
    .map(key => ({ field: key.replaceAll('_',' '), previous: before[key], current: after[key],
                   hasPrevious: Object.prototype.hasOwnProperty.call(before,key),
                   added: Array.isArray(before[key]) && Array.isArray(after[key])
                     ? after[key].filter(v => !before[key].includes(v)) : [],
                   removed: Array.isArray(before[key]) && Array.isArray(after[key])
                     ? before[key].filter(v => !after[key].includes(v)) : [] }))
}
function eventName(entry) {
  if (entry.action.startsWith('changed.')) return `Updated ${entry.entity_type}`
  const named = {
    'access.roles.edit':'Changed role defaults', 'access.roles.create':'Created role',
    'reservations.order_number.edit':'Changed booking order number',
    'reservations.order.matched':'Verified Union order',
    'reservations.order.verify':'Submitted order for verification',
    'reservations.sale_import.approve':'Linked Union purchase to booking',
    'reservations.sale_import.upload':'Imported Union sales',
    'reservations.sale_import.confirm':'Confirmed purchase from import',
    'reservations.committee.add':'Added committee ticket',
    'reservations.confirm.manual':'Manually confirmed booking',
  }
  return named[entry.action] || entry.action.replaceAll('.',' · ').replaceAll('_',' ')
}
const visible = computed(() => {
  const q = search.value.trim().toLowerCase()
  return rows.value.filter(r => !q || [r.actor_email,r.action,r.entity_type,r.entity_id,
    JSON.stringify(r.before_data),JSON.stringify(r.after_data)].some(v => String(v || '').toLowerCase().includes(q)))
})
async function load() {
  busy.value = true
  error.value = ''
  try {
    const data = await api.get('/admin/audit', { limit: 200, include_requests: includeRequests.value })
    rows.value = data.actions || []
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <section class="audit-panel">
    <header class="audit-header">
      <div><h2>Committee activity</h2><p>Who changed what, including the old and new values where recorded.</p></div>
      <Button size="sm" variant="secondary" :disabled="busy" @click="load">{{ busy ? 'Loading…' : 'Refresh' }}</Button>
    </header>
    <div class="audit-tools">
      <input v-model="search" type="search" placeholder="Filter by person, action, booking # or value" aria-label="Filter activity" />
      <label><input v-model="includeRequests" type="checkbox" @change="load" /> Also show technical API calls</label>
    </div>
    <p v-if="error" class="audit-error" role="alert">{{ error }}</p>
    <p v-if="busy" role="status">Loading activity…</p>
    <article v-for="entry in visible" :key="entry.id" class="audit-event">
      <div class="audit-title"><strong>{{ eventName(entry) }}</strong><span>{{ new Date(entry.at).toLocaleString() }}</span></div>
      <div class="audit-meta"><b>{{ entry.actor_email || (entry.actor_user_id ? `User #${entry.actor_user_id}` : 'System / automated') }}</b>
        · {{ entry.entity_type }}<template v-if="entry.entity_id && entry.entity_id !== 0"> #{{ entry.entity_id }}</template></div>
      <div v-if="changes(entry).length" class="audit-diff">
        <div v-for="item in changes(entry).slice(0,5)" :key="item.field" class="audit-diff-row">
          <b>{{ item.field }}</b>
          <template v-if="item.added.length || item.removed.length">
            <span v-if="item.added.length" class="audit-added">+ {{ item.added.join(', ') }}</span>
            <span v-if="item.removed.length" class="audit-removed">− {{ item.removed.join(', ') }}</span>
          </template>
          <template v-else>
            <span v-if="item.hasPrevious" class="audit-old">{{ value(item.previous) }}</span>
            <span v-if="item.hasPrevious">→</span>
            <span class="audit-new">{{ value(item.current) }}</span>
          </template>
        </div>
        <button v-if="changes(entry).length > 5" class="audit-more" type="button" @click="opened[entry.id] = !opened[entry.id]">
          {{ opened[entry.id] ? 'Hide remaining fields' : `Show ${changes(entry).length - 5} more changes` }}
        </button>
        <div v-if="opened[entry.id]" class="audit-diff">
          <div v-for="item in changes(entry).slice(5)" :key="item.field" class="audit-diff-row">
            <b>{{ item.field }}</b><span v-if="item.hasPrevious" class="audit-old">{{ value(item.previous) }} →</span><span class="audit-new">{{ value(item.current) }}</span>
          </div>
        </div>
      </div>
      <p v-else class="audit-meta">{{ entry.entity_type === 'api_request' || entry.entity_type === 'adapter_write'
        ? 'Technical request record (no body stored).' : 'Action recorded; this event has no field-level data.' }}</p>
    </article>
    <p v-if="!busy && !visible.length" class="audit-meta">No matching activity. Technical request records are hidden by default.</p>
  </section>
</template>

<style scoped>
.audit-panel { display:grid; gap:var(--space-4); border:2px solid var(--text); padding:var(--space-5); background:var(--surface,#fff); }
.audit-header { display:flex; gap:var(--space-4); align-items:flex-start; justify-content:space-between; }
.audit-header h2 { margin:0 0 var(--space-2); font:var(--type-h4); }
.audit-header p,.audit-meta { margin:0; color:var(--text-muted); font:var(--type-small); }
.audit-tools { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-3); }
.audit-tools input[type=search] { flex:1 1 250px; width:100%; max-width:510px; padding:var(--space-2); border:1px solid var(--text); color:var(--text); }
.audit-tools label { display:flex; align-items:center; gap:var(--space-2); font:var(--type-small); }
.audit-event { display:grid; gap:var(--space-2); padding:var(--space-3) 0; border-top:1px solid var(--border-hairline); }
.audit-title { display:flex; gap:var(--space-3); justify-content:space-between; flex-wrap:wrap; font:var(--type-small); }
.audit-title > span { color:var(--text-muted); }
.audit-diff { display:grid; gap:var(--space-2); }
.audit-diff-row { display:flex; gap:var(--space-2); flex-wrap:wrap; font:var(--type-small); }
.audit-diff-row b { min-width:160px; }
.audit-old { text-decoration:line-through; color:var(--text-muted); overflow-wrap:anywhere; }
.audit-new { font-weight:600; overflow-wrap:anywhere; }
.audit-added { color:var(--success,var(--text)); } .audit-removed,.audit-error { color:var(--danger); }
.audit-more { background:transparent; border:0; text-align:left; padding:0; color:var(--accent); text-decoration:underline; cursor:pointer; font:var(--type-small); }
@media(max-width:620px) { .audit-panel { padding:var(--space-3); } .audit-diff-row { display:grid; } }
</style>
