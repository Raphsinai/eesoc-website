<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Input from '@/components/ds/Input.vue'
import Field from '@/components/ds/Field.vue'

const props = defineProps({ events: { type: Array, default: () => [] } })
const societies = ref([])
const selected = ref(null)
const committee = ref([])
const assigned = ref([])
const name = ref('')
const centre = ref('')
const credential = ref('')
const replacementKey = ref('')
const login = ref('')
const title = ref('Committee')
const managing = ref(true)
const permissions = ref([])
const assignEventId = ref('')
const busy = ref(false)
const message = ref('')
const error = ref('')
const ACTIONS = [
  ['events.view', 'View assigned events'], ['events.edit', 'Edit event details'],
  ['events.publish', 'Publish or unpublish assigned events'],
  ['events.tickets.create', 'Create ticket types'],
  ['events.tickets.edit', 'Edit ticket types'], ['events.tickets.delete', 'Delete ticket types'],
  ['events.bookings.view', 'View event bookings'],
  ['announcements.preview', 'Preview announcements'],
  ['announcements.send', 'Send announcements'], ['whatsapp.view', 'View society WhatsApp'],
  ['whatsapp.link', 'Link/unlink society WhatsApp'],
  ['whatsapp.groups.manage', 'Enable or disable own WhatsApp groups'],
  ['whatsapp.group.request', 'Ask EESoc to join society WhatsApp groups'],
  ['whatsapp.post', 'Post to assigned WhatsApp groups'],
]
const current = computed(() => societies.value.find(s => s.id === selected.value))
const availableEvents = computed(() => props.events.filter(e => !assigned.value.includes(e.id)))
const defaultPermissions = () => ACTIONS.map(([key]) => key)

async function load() {
  if (!can('societies.view')) return
  societies.value = await api.get('/admin/societies')
  if (!societies.value.some(s => s.id === selected.value)) selected.value = societies.value[0]?.id || null
  await details()
}
async function details() {
  committee.value = []
  assigned.value = []
  if (!selected.value) return
  try {
    if (can('societies.committee.view')) committee.value = await api.get(`/admin/societies/${selected.value}/committee`)
    assigned.value = await api.get(`/admin/societies/${selected.value}/events`)
  } catch (e) { error.value = e.message }
}
async function act(job, success) {
  busy.value = true; error.value = ''; message.value = ''
  try { await job(); message.value = success; await load() }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function add() {
  act(async () => {
    const created = await api.post('/admin/societies', { name: name.value, centre_code: centre.value, api_key: credential.value })
    credential.value = ''; name.value = ''; centre.value = ''; selected.value = created.id
  }, 'Society connected. Synchronise its members before using the mailing list.')
}
function rotateKey() {
  act(async () => { await api.put(`/admin/societies/${selected.value}/credential`,{api_key:replacementKey.value}); replacementKey.value='' }, 'SUMS key updated; synchronise members to confirm it works.')
}
function sync() { act(() => api.post(`/admin/societies/${selected.value}/sync`), 'Membership roster synchronised.') }
function remove() {
  if (!confirm(`Remove ${current.value?.name} and all imported member records, committee assignments and event links?`)) return
  act(() => api.del(`/admin/societies/${selected.value}`), 'Society removed.')
}
function editMember(member) {
  login.value = member.login; title.value = member.title
  managing.value = member.managing; permissions.value = [...member.permissions]
}
function saveMember() {
  const shortcode = login.value.trim().toLowerCase()
  if (!/^[a-z][a-z0-9._-]{1,63}$/.test(shortcode)) { error.value = 'Enter a valid Imperial Login.'; return }
  act(() => api.put(`/admin/societies/${selected.value}/committee/${encodeURIComponent(shortcode)}`, {
    title: title.value, managing: managing.value, permissions: permissions.value,
  }), 'Committee member saved.')
}
function removeMember(member) {
  if (!confirm(`Remove committee access for ${member.login}?`)) return
  act(() => api.del(`/admin/societies/${selected.value}/committee/${encodeURIComponent(member.login)}`), 'Committee access removed.')
}
function assign() {
  if (!assignEventId.value) return
  act(() => api.put(`/admin/societies/${selected.value}/events/${assignEventId.value}`, {}), 'Event assigned.')
}
function unassign(id) {
  if (!confirm('Remove this society’s ability to manage this event?')) return
  act(() => api.del(`/admin/societies/${selected.value}/events/${id}`), 'Event access revoked.')
}
onMounted(load)
</script>

<template>
  <div style="display:grid; gap:var(--space-5)">
    <p v-if="error" style="color:var(--danger)">{{ error }}</p>
    <p v-if="message">{{ message }}</p>
    <Card v-if="can('societies.create')" variant="outline">
      <h2>Connect a society</h2>
      <p style="font:var(--type-small);color:var(--text-muted)">The key is stored encrypted, never shown again. Use a key issued with the society’s authorisation.</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:var(--space-3)">
        <Field label="Society name"><Input v-model="name" placeholder="Society name" /></Field>
        <Field label="SUMS centre code"><Input v-model="centre" placeholder="Centre code" /></Field>
        <Field label="SUMS API key"><input v-model="credential" class="ees-input" type="password" autocomplete="off" placeholder="API key" /></Field>
      </div>
      <Button :disabled="busy || !name.trim() || !centre.trim() || credential.length < 8" @click="add">Connect society</Button>
    </Card>
    <Card v-if="can('societies.view')" variant="outline">
      <Field label="Connected society">
        <select v-model="selected" class="ees-select" @change="details">
          <option v-for="s in societies" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
      </Field>
      <p v-if="current" style="font:var(--type-small);color:var(--text-muted)">Centre {{ current.centre_code }} · Last sync: {{ current.synced_at || 'Never' }} · Year {{ current.synced_year || '—' }}</p>
      <p v-if="current?.sync_error" style="color:var(--danger)">{{ current.sync_error }}</p>
      <div v-if="current && can('societies.credential.rotate')" style="display:flex;gap:var(--space-2);flex-wrap:wrap">
        <input v-model="replacementKey" class="ees-input" type="password" placeholder="Replace SUMS API key" autocomplete="off" />
        <Button :disabled="busy || replacementKey.length < 8" size="sm" variant="secondary" @click="rotateKey">Replace key</Button>
      </div>
      <div v-if="current" style="display:flex;gap:var(--space-2);flex-wrap:wrap">
        <Button v-if="can('societies.sync')" :disabled="busy" @click="sync">Sync members now</Button>
        <a v-if="can('societies.members.export')" class="ees-btn" :href="`/api/admin/societies/${selected}/mailing-list.csv`" download>Export member emails (CSV)</a>
        <Button v-if="can('societies.delete')" variant="ghost" :disabled="busy" @click="remove">Remove society</Button>
      </div>
    </Card>
    <Card v-if="selected && (can('societies.committee.view') || can('societies.committee.edit'))" variant="outline">
      <h2>Society committee</h2>
      <p style="font:var(--type-small);color:var(--text-muted)">Only verified Imperial logins listed here can open the society portal. A managing committee member must also have each individual action enabled.</p>
      <div v-for="person in committee" :key="person.login" style="display:flex;align-items:center;justify-content:space-between;gap:var(--space-2);border-top:1px solid var(--border-hairline);padding:var(--space-2) 0">
        <span>{{ person.login }} · {{ person.title }} · {{ person.managing ? 'Manager' : 'Committee' }}</span>
        <div style="display:flex;gap:var(--space-2)">
          <Button v-if="can('societies.committee.edit')" size="sm" variant="ghost" @click="editMember(person)">Edit</Button>
          <Button v-if="can('societies.committee.delete')" size="sm" variant="ghost" @click="removeMember(person)">Remove</Button>
        </div>
      </div>
      <div v-if="can('societies.committee.edit')" style="display:grid;gap:var(--space-3)">
        <Field label="Imperial Login"><Input v-model="login" placeholder="abc123" /></Field>
        <Field label="Position"><Input v-model="title" placeholder="President" /></Field>
        <label><input v-model="managing" type="checkbox" /> Managing committee (eligible to manage assigned events)</label>
        <div style="display:flex;gap:var(--space-2)">
          <Button size="sm" variant="secondary" @click="permissions = defaultPermissions()">Select all permissions</Button>
          <Button size="sm" variant="ghost" @click="permissions = []">Clear permissions</Button>
        </div>
        <label v-for="[key, label] in ACTIONS" :key="key" style="font:var(--type-small)"><input v-model="permissions" type="checkbox" :value="key" /> {{ label }}</label>
        <Button :disabled="busy || !login.trim()" @click="saveMember">Save committee member</Button>
      </div>
    </Card>
    <Card v-if="selected && (can('societies.events.assign') || can('societies.events.unassign'))" variant="outline">
      <h2>Events this society may manage</h2>
      <div v-for="id in assigned" :key="id" style="display:flex;justify-content:space-between;border-top:1px solid var(--border-hairline);padding:var(--space-2) 0">
        <span>{{ events.find(e => e.id === id)?.title || `Event #${id}` }}</span>
        <Button v-if="can('societies.events.unassign')" size="sm" variant="ghost" @click="unassign(id)">Unassign</Button>
      </div>
      <div v-if="can('societies.events.assign')" style="display:flex;gap:var(--space-3);flex-wrap:wrap">
        <select v-model="assignEventId" class="ees-select"><option value="">Select event</option><option v-for="ev in availableEvents" :key="ev.id" :value="ev.id">{{ ev.title }}</option></select>
        <Button :disabled="busy || !assignEventId" @click="assign">Assign event</Button>
      </div>
    </Card>
  </div>
</template>
