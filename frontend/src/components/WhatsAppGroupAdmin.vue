<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'

const inventory = ref([])
const settings = ref([])
const societies = ref([])
const people = ref([])
const invites = ref([])
const error = ref('')
const notice = ref('')
const busy = ref(false)
const working = ref({})
const groupKey = g => `${g.account}:${g.jid}`
const groups = computed(() => inventory.value.map(g => {
  const existing = settings.value.find(x => x.account_key === g.account && x.jid === g.jid)
  return { ...g, enabled: existing?.enabled || false,
    society_id: existing?.society_id || null,
    eesoc_enabled: existing?.eesoc_enabled || false,
    eesoc_user_ids: existing?.eesoc_user_ids || [],
  }
}))
const accountLabel = account => account === 'eesoc' ? 'EESoc number' :
  (societies.value.find(s => `society-${s.id}` === account)?.name || account)
async function load() {
  error.value = ''
  try {
    const [all, set, soc, eesocPeople, reqs] = await Promise.all([
      api.get('/admin/societies/whatsapp/inventory'),
      api.get('/admin/societies/whatsapp/groups'),
      api.get('/admin/societies/whatsapp/societies'),
      api.get('/admin/societies/whatsapp/eesoc-people'),
      can('societies.whatsapp.invites.view') ? api.get('/admin/societies/whatsapp/invites') : Promise.resolve([]),
    ])
    inventory.value = all; settings.value = set; societies.value = soc
    people.value = eesocPeople; invites.value = reqs
  } catch (e) { error.value = e.message }
}
async function save(g) {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await api.put('/admin/societies/whatsapp/groups', {
      account_key: g.account, jid: g.jid, enabled: Boolean(g.enabled),
      society_id: g.society_id || null, eesoc_enabled: Boolean(g.eesoc_enabled),
      eesoc_user_ids: g.eesoc_enabled ? g.eesoc_user_ids : [],
    })
    notice.value = 'Group permissions saved.'
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function decide(invite, accept) {
  if (accept && !confirm(`Join this group with the EESoc WhatsApp number for ${invite.society_name}?`)) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await api.post(`/admin/societies/whatsapp/invites/${invite.id}/${accept ? 'approve' : 'decline'}`)
    notice.value = accept ? 'EESoc joined. Group initially allows only the owning society to post.' : 'Invitation declined.'
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <div style="display:grid;gap:var(--space-4)">
    <Card variant="outline">
      <h2>Group access control</h2>
      <p style="font:var(--type-small);color:var(--text-muted)">Every group is disabled until enabled. Assign a group to a society, and separately select the EESoc committee members permitted to post into it. They also need the general WhatsApp posting permission.</p>
      <p v-if="error" style="color:var(--danger)">{{ error }}</p>
      <p v-if="notice">{{ notice }}</p>
      <Button size="sm" variant="secondary" :disabled="busy" @click="load">Refresh groups</Button>
      <p v-if="!groups.length">No groups found on the currently linked accounts.</p>
      <div v-for="g in groups" :key="groupKey(g)" style="padding:var(--space-3) 0;border-top:1px solid var(--border-hairline);display:grid;gap:var(--space-2)">
        <div><strong>{{ g.name }}</strong> · {{ accountLabel(g.account) }} · {{ g.size }} participants</div>
        <p v-if="!g.canPost" style="color:var(--danger)">Linked account cannot post in this group</p>
        <template v-if="can('societies.whatsapp.groups.edit')">
          <label><input v-model="g.enabled" type="checkbox" /> Enabled for announcements</label>
          <label style="display:grid;gap:var(--space-1)">Assigned society
            <select v-model="g.society_id" class="ees-select" :disabled="g.account !== 'eesoc'">
              <option :value="null">None</option>
              <option v-for="s in societies" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
          </label>
          <label><input v-model="g.eesoc_enabled" type="checkbox" /> Permit selected EESoc committee users</label>
          <div v-if="g.eesoc_enabled" style="display:flex;gap:var(--space-2);flex-wrap:wrap">
            <label v-for="person in people" :key="person.user_id" style="font:var(--type-small)">
              <input v-model="g.eesoc_user_ids" :value="person.user_id" type="checkbox" /> {{ person.name }}
            </label>
          </div>
          <Button :disabled="busy" size="sm" @click="save(g)">Save group settings</Button>
        </template>
        <template v-else>
          <span>{{ g.enabled ? 'Enabled' : 'Disabled' }} · {{ g.society_id ? 'Assigned to society' : 'No society assigned' }}</span>
        </template>
      </div>
    </Card>
    <Card v-if="can('societies.whatsapp.invites.view')" variant="outline">
      <h2>Societies requesting EESoc to join a WhatsApp group</h2>
      <p v-if="!invites.length">No invitations have been requested.</p>
      <div v-for="invite in invites" :key="invite.id" style="display:flex;justify-content:space-between;gap:var(--space-3);padding:var(--space-3) 0;border-top:1px solid var(--border-hairline)">
        <span>{{ invite.society_name }} · {{ invite.status }} · {{ invite.created_at }}</span>
        <div v-if="invite.status === 'pending'" style="display:flex;gap:var(--space-2)">
          <Button v-if="can('societies.whatsapp.invites.approve')" size="sm" :disabled="busy" @click="decide(invite,true)">Join</Button>
          <Button v-if="can('societies.whatsapp.invites.decline')" size="sm" variant="ghost" :disabled="busy" @click="decide(invite,false)">Decline</Button>
        </div>
      </div>
    </Card>
  </div>
</template>
