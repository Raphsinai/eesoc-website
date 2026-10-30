<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'

const props = defineProps({
  societyId: { type: Number, required: true },
  eesocAdmin: { type: Boolean, default: false },
  permissions: { type: Array, default: () => [] },
  managing: { type: Boolean, default: false },
})
const route = computed(() => props.eesocAdmin ? '/admin/societies' : '/society-portal')
const base = computed(() => `${route.value}/${props.societyId}`)
const canDo = (action) => props.eesocAdmin
  ? can(({ 'access.view':'societies.committee.view', 'access.grant':'societies.committee.edit',
           'access.revoke':'societies.committee.delete', 'access.roles.create':'societies.committee.edit',
           'access.roles.edit':'societies.committee.edit' })[action])
  : props.managing && props.permissions.includes(action)
const canView = computed(() => props.eesocAdmin ? can('societies.committee.view') : props.permissions.includes('access.view'))
const catalogue = ref([])
const members = ref([])
const roles = ref([])
const busy = ref(false)
const error = ref('')
const notice = ref('')
const login = ref('')
const position = ref('Committee')
const managing = ref(false)
const roleId = ref('')
const roleName = ref('')
const inherit = ref(false)
const selected = ref([])
const existingRole = ref('')
const newRoleId = ref('')
const roleTitle = ref('')
const rolePermissions = ref([])
const editingRole = ref(false)
const actions = computed(() => catalogue.value.flatMap(group =>
  group.sections.flatMap(sub => sub.actions)))
const grantable = computed(() => props.eesocAdmin
  ? new Set(actions.value.map(a => a.id))
  : new Set(props.permissions))
const roleChoices = computed(() => roles.value.filter(role => props.eesocAdmin ||
  role.permissions.every(p => grantable.value.has(p))))
const currentRole = computed(() => roles.value.find(r => r.role_id === roleId.value))
function clearMember() {
  login.value = ''; position.value = 'Committee'; managing.value = false
  roleId.value = ''; selected.value = []; inherit.value = false
}
function selectMember(person) {
  login.value = person.login
  position.value = person.title
  managing.value = person.managing
  roleId.value = person.role_id || ''
  inherit.value = !!person.role_id && person.permissions == null
  selected.value = [...(person.permissions ?? person.effective_permissions ?? [])]
}
function startRole(role = null) {
  editingRole.value = true
  existingRole.value = role?.role_id || ''
  newRoleId.value = role?.role_id || ''
  roleTitle.value = role?.title || ''
  rolePermissions.value = [...(role?.permissions || [])]
}
function rolePreview() { if (currentRole.value && inherit.value) selected.value = [...currentRole.value.permissions] }

async function load() {
  if (!canView.value || !props.societyId) return
  error.value = ''
  try {
    const [list, people, presets] = await Promise.all([
      api.get(props.eesocAdmin ? '/admin/societies/permission-catalogue' : '/society-portal/permissions'),
      api.get(`${base.value}/committee`), api.get(`${base.value}/roles`),
    ])
    catalogue.value = list
    members.value = people
    roles.value = presets
  } catch (e) { error.value = e.message }
}
async function perform(task, message) {
  busy.value = true; error.value = ''; notice.value = ''
  try { await task(); notice.value = message; await load() }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function saveMember() {
  const shortcode = login.value.trim().toLowerCase()
  if (!/^[a-z][a-z0-9._-]{1,63}$/.test(shortcode)) {
    error.value = 'Enter a valid Imperial Login'; return
  }
  if (inherit.value && !roleId.value) { error.value = 'Choose a role to inherit'; return }
  const permissions = inherit.value ? null : selected.value.filter(p => grantable.value.has(p))
  perform(() => api.put(`${base.value}/committee/${encodeURIComponent(shortcode)}`, {
    title: position.value, managing: managing.value, role_id: roleId.value || null,
    permissions,
  }), 'Committee access updated.')
}
function removeMember(person) {
  if (!confirm(`Remove ${person.login} from this society committee?`)) return
  perform(() => api.del(`${base.value}/committee/${encodeURIComponent(person.login)}`), 'Committee access revoked.')
}
function saveRole() {
  const id = newRoleId.value.trim().toLowerCase()
  if (!/^[a-z0-9_-]{2,50}$/.test(id)) { error.value = 'Use a role ID with letters, numbers or hyphens'; return }
  if (!roleTitle.value.trim()) { error.value = 'Give this role a name'; return }
  perform(async () => {
    await api.put(`${base.value}/roles/${id}`, {
      title: roleTitle.value.trim(),
      permissions: rolePermissions.value.filter(p => grantable.value.has(p)),
    })
    editingRole.value = false
  }, 'Role defaults saved. Members inheriting the role receive the updated permissions.')
}
function deleteRole(role) {
  if (!confirm(`Delete the ${role.title} role? Reassign any committee using it first.`)) return
  perform(() => api.del(`${base.value}/roles/${role.role_id}`), 'Role removed.')
}
onMounted(load)
watch(() => [props.societyId, props.permissions.join(','), props.managing], () => {
  clearMember(); editingRole.value = false; load()
})
</script>

<template>
  <div style="display:grid;gap:var(--space-4)">
    <p v-if="error" style="color:var(--danger)">{{ error }}</p>
    <p v-if="notice">{{ notice }}</p>
    <Card variant="outline">
      <h2>Society committee access</h2>
      <p style="font:var(--type-small);color:var(--text-muted)">The same per-action event permissions as EESoc, but limited to events assigned to this society, its own mailing list and its WhatsApp groups. Assign a role preset or customise a member's permissions.</p>
      <div v-for="person in members" :key="person.login" style="padding:var(--space-2) 0;border-top:1px solid var(--border-hairline);display:flex;justify-content:space-between;gap:var(--space-2);flex-wrap:wrap">
        <span>{{ person.login }} · {{ person.title }} · {{ person.managing ? 'Managing committee' : 'Committee' }}<template v-if="person.role_id"> · {{ roles.find(r => r.role_id === person.role_id)?.title || person.role_id }}{{ person.permissions == null ? ' (inherited)' : ' (custom)' }}</template></span>
        <div style="display:flex;gap:var(--space-2)">
          <Button v-if="canDo('access.grant')" size="sm" variant="secondary" @click="selectMember(person)">Edit permissions</Button>
          <Button v-if="canDo('access.revoke')" size="sm" variant="ghost" @click="removeMember(person)">Revoke</Button>
        </div>
      </div>
      <p v-if="!members.length">No committee members have been assigned.</p>
    </Card>
    <Card v-if="canDo('access.grant')" variant="outline" style="display:grid;gap:var(--space-3)">
      <h3>Add or edit committee access</h3>
      <Field label="Imperial Login"><Input v-model="login" placeholder="ab123" /></Field>
      <Field label="Position/title"><Input v-model="position" placeholder="President" /></Field>
      <label><input v-model="managing" type="checkbox" /> Managing committee (may use granted write permissions)</label>
      <Field label="Role preset (optional)">
        <select v-model="roleId" class="ees-select" @change="rolePreview"><option value="">No preset</option><option v-for="role in roleChoices" :key="role.role_id" :value="role.role_id">{{ role.title }}</option></select>
      </Field>
      <label v-if="roleId"><input v-model="inherit" type="checkbox" @change="rolePreview" /> Follow this society role's defaults when they change</label>
      <template v-if="!inherit">
        <div style="display:flex;gap:var(--space-2);flex-wrap:wrap">
          <Button size="sm" variant="secondary" @click="selected = [...grantable]">Select all grantable actions</Button>
          <Button size="sm" variant="ghost" @click="selected = []">Clear selection</Button>
        </div>
        <div v-for="group in catalogue" :key="group.group">
          <details v-if="group.sections.some(section => section.actions.some(a => grantable.has(a.id)))" style="border-top:1px solid var(--border-hairline);padding:var(--space-2) 0">
            <summary style="font:var(--type-label);cursor:pointer">{{ group.group }} · {{ group.sections.reduce((n,section) => n + section.actions.filter(a => selected.includes(a.id)).length,0) }} selected</summary>
            <div v-for="section in group.sections" :key="section.name" style="display:grid;gap:var(--space-2);margin:var(--space-3) 0">
              <strong v-if="section.actions.some(a => grantable.has(a.id))" style="font:var(--type-small)">{{ section.name }}</strong>
              <label v-for="action in section.actions.filter(a => grantable.has(a.id))" :key="action.id" style="display:flex;gap:var(--space-2);font:var(--type-small)"><input v-model="selected" type="checkbox" :value="action.id" /> {{ action.label }}</label>
            </div>
          </details>
        </div>
      </template>
      <Button :disabled="busy || !login.trim()" @click="saveMember">Save committee permissions</Button>
      <Button size="sm" variant="ghost" @click="clearMember">Clear editor</Button>
    </Card>
    <Card v-if="canView" variant="outline" style="display:grid;gap:var(--space-3)">
      <h3>Society role defaults</h3>
      <div v-for="role in roles" :key="role.role_id" style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid var(--border-hairline);gap:var(--space-2);padding:var(--space-2) 0">
        <span>{{ role.title }} · {{ role.permissions.length }} permissions</span>
        <div style="display:flex;gap:var(--space-2)"><Button v-if="canDo('access.roles.edit')" size="sm" variant="secondary" @click="startRole(role)">Edit</Button><Button v-if="canDo('access.roles.edit')" size="sm" variant="ghost" @click="deleteRole(role)">Delete</Button></div>
      </div>
      <Button v-if="canDo('access.roles.create')" size="sm" variant="secondary" @click="startRole()">Create society role</Button>
      <div v-if="editingRole" style="display:grid;gap:var(--space-3)">
        <Field label="Role ID"><Input v-model="newRoleId" :disabled="!!existingRole" placeholder="events-officer" /></Field>
        <Field label="Role name"><Input v-model="roleTitle" placeholder="Events Officer" /></Field>
        <div v-for="group in catalogue" :key="group.group">
          <details v-if="group.sections.some(section => section.actions.some(a => grantable.has(a.id)))" style="border-top:1px solid var(--border-hairline);padding:var(--space-2) 0">
            <summary style="font:var(--type-label);cursor:pointer">{{ group.group }}</summary>
            <div v-for="section in group.sections" :key="section.name" style="display:grid;gap:var(--space-2)">
              <strong v-if="section.actions.some(a => grantable.has(a.id))">{{ section.name }}</strong>
              <label v-for="action in section.actions.filter(a => grantable.has(a.id))" :key="action.id" style="font:var(--type-small)"><input v-model="rolePermissions" type="checkbox" :value="action.id" /> {{ action.label }}</label>
            </div>
          </details>
        </div>
        <Button :disabled="busy || !newRoleId.trim() || !roleTitle.trim()" @click="saveRole">Save role defaults</Button>
        <Button size="sm" variant="ghost" @click="editingRole = false">Cancel</Button>
      </div>
    </Card>
  </div>
</template>
