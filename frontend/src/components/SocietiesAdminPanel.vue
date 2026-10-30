<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Input from '@/components/ds/Input.vue'
import Field from '@/components/ds/Field.vue'
import SocietyAccessPanel from '@/components/SocietyAccessPanel.vue'

const props = defineProps({ events: { type: Array, default: () => [] } })
const societies = ref([])
const selected = ref(null)
const committee = ref([])
const assigned = ref([])
const memberSearch = ref('')
const memberPage = ref(0)
const memberRows = ref([])
const memberTotal = ref(0)
const memberDetails = ref(null)
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
const memberPageSize = 50
async function loadMembers() {
  memberRows.value = []
  memberTotal.value = 0
  if (!selected.value || !can('societies.members.view')) return
  try {
    const result = await api.get(`/admin/societies/${selected.value}/members`, {
      search: memberSearch.value, limit: memberPageSize, offset: memberPage.value * memberPageSize,
    })
    memberRows.value = result.members || []
    memberTotal.value = result.total || 0
  } catch (e) { error.value = e.message }
}
async function searchMembers() { memberPage.value = 0; memberDetails.value = null; await loadMembers() }
async function changeMemberPage(delta) {
  memberPage.value = Math.max(0, memberPage.value + delta)
  memberDetails.value = null
  await loadMembers()
}
async function changeSociety() {
  memberPage.value = 0; memberSearch.value = ''; memberDetails.value = null
  await details()
}

async function load() {
  if (!can('societies.view')) return
  societies.value = await api.get('/admin/societies')
  if (!societies.value.some(s => s.id === selected.value)) selected.value = societies.value[0]?.id || null
  await details()
}
async function details() {
  committee.value = []
  assigned.value = []
  memberRows.value = []
  if (!selected.value) return
  try {
    if (can('societies.committee.view')) committee.value = await api.get(`/admin/societies/${selected.value}/committee`)
    assigned.value = await api.get(`/admin/societies/${selected.value}/events`)
    if (can('societies.members.view')) await loadMembers()
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
        <select v-model="selected" class="ees-select" @change="changeSociety">
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
    <Card v-if="selected && can('societies.members.view')" variant="outline">
      <h2>Synced Union member records</h2>
      <p style="font:var(--type-small);color:var(--text-muted)">Includes CID, Login, names, contact email, membership order number, member type and the original SUMS report fields. Only authorised committee users can view this information.</p>
      <div style="display:flex;gap:var(--space-2);flex-wrap:wrap;align-items:center">
        <Input v-model="memberSearch" placeholder="Search CID, Login or name" @keyup.enter="searchMembers" />
        <Button size="sm" variant="secondary" @click="searchMembers">Search</Button>
      </div>
      <p style="font:var(--type-small)">{{ memberTotal }} matching records · page {{ memberPage + 1 }}</p>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font:var(--type-small)">
          <thead><tr><th>CID</th><th>Login</th><th>Member</th><th>Union email</th><th>Type</th><th>Order no.</th><th></th></tr></thead>
          <tbody>
            <tr v-for="m in memberRows" :key="m.cid" style="border-top:1px solid var(--border-hairline)">
              <td>{{ m.cid }}</td><td>{{ m.login || '—' }}</td>
              <td>{{ [m.first_name,m.last_name].filter(Boolean).join(' ') || '—' }}</td>
              <td>{{ m.email || '—' }}</td><td>{{ m.member_type || '—' }}</td>
              <td>{{ m.order_no || '—' }}</td>
              <td><Button size="sm" variant="ghost" @click="memberDetails = memberDetails === m.cid ? null : m.cid">{{ memberDetails === m.cid ? 'Hide' : 'Details' }}</Button></td>
            </tr>
          </tbody>
        </table>
        <p v-if="!memberRows.length" style="font:var(--type-small)">No matching records in this academic year's synced roster.</p>
      </div>
      <pre v-if="memberDetails" style="overflow:auto;max-height:320px;padding:var(--space-3);background:var(--surface-sunken);font:var(--type-2xs)">{{ JSON.stringify(memberRows.find(m => m.cid === memberDetails)?.union_profile || {}, null, 2) }}</pre>
      <div style="display:flex;gap:var(--space-2)">
        <Button size="sm" variant="secondary" :disabled="memberPage === 0" @click="changeMemberPage(-1)">Previous</Button>
        <Button size="sm" variant="secondary" :disabled="(memberPage + 1) * memberPageSize >= memberTotal" @click="changeMemberPage(1)">Next</Button>
      </div>
    </Card>
    <SocietyAccessPanel v-if="selected && can('societies.committee.view')"
      :society-id="Number(selected)" eesoc-admin />
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
