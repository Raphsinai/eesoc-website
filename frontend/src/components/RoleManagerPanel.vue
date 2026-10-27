<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Input from '@/components/ds/Input.vue'
import Field from '@/components/ds/Field.vue'
import PermissionSelector from '@/components/PermissionSelector.vue'

const roles = ref([])
const permissions = ref([])
const selected = ref('')
const roleId = ref('')
const name = ref('')
const description = ref('')
const grants = ref([])
const busy = ref(false)
const error = ref('')
const notice = ref('')
const editing = computed(() => Boolean(selected.value))
const canSave = computed(() => editing.value ? can('access.roles.edit') : can('access.roles.create'))

async function load() {
  try {
    const result = await api.get('/admin/roles')
    roles.value = result.roles || []
    permissions.value = result.permissions || []
    if (!selected.value && !can('access.roles.create')) {
      choose(roles.value.find(r => r.id !== 'superuser')?.id || '')
    }
  } catch (e) { error.value = e.message }
}

function choose(value) {
  selected.value = value
  const existing = roles.value.find(r => r.id === value)
  roleId.value = value || ''
  name.value = existing?.label || ''
  description.value = existing?.blurb || ''
  grants.value = [...(existing?.capabilities || [])]
}

async function save() {
  if (!canSave.value || (editing.value && selected.value === 'superuser')) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const data = {
      label: name.value.trim(), blurb: description.value.trim(), capabilities: grants.value,
    }
    if (editing.value) await api.put(`/admin/roles/${selected.value}`, data)
    else await api.post('/admin/roles', { id: roleId.value.trim(), ...data })
    const savedId = roleId.value.trim()
    await load()
    choose(savedId)
    notice.value = 'Role defaults saved. Members using this preset will pick up the new grants on their next request.'
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <Card variant="offset">
    <h2 style="font:var(--type-h4);margin:0 0 var(--space-2)">Committee role presets</h2>
    <p style="font:var(--type-small);color:var(--text-muted)">
      Edit default permissions or create a new role. Changes affect members using that role's
      default grants; individual custom selections remain unchanged.
    </p>
    <div class="role-form">
      <Field label="Role">
        <select class="ees-select" :value="selected" @change="choose($event.target.value)">
          <option v-if="can('access.roles.create')" value="">Create new role…</option>
          <option v-for="role in roles" :key="role.id" :value="role.id" :disabled="role.id === 'superuser' || !can('access.roles.edit')">
            {{ role.label }}{{ role.id === 'superuser' ? ' (protected)' : '' }}
          </option>
        </select>
      </Field>
      <Field v-if="!editing" label="Unique role ID" help="Permanent, lowercase ID: e.g. treasurer">
        <Input v-model="roleId" placeholder="treasurer" :disabled="busy" />
      </Field>
      <Field label="Display name"><Input v-model="name" placeholder="Treasurer" :disabled="busy" /></Field>
      <Field label="Description"><Input v-model="description" placeholder="What this role is for" :disabled="busy" /></Field>
      <PermissionSelector v-model="grants" :permissions="permissions" :disabled="busy" />
      <p v-if="error" style="font:var(--type-small);color:var(--danger)" role="alert">{{ error }}</p>
      <p v-if="notice" style="font:var(--type-small)" role="status">{{ notice }}</p>
      <Button v-if="canSave" :disabled="busy || !roleId.trim() || !name.trim() || selected === 'superuser'" @click="save">
        {{ busy ? 'Saving…' : editing ? 'Save role defaults' : 'Create role' }}
      </Button>
      <p style="font:var(--type-small);color:var(--text-muted)">
        The standing superuser is intentionally protected against accidental loss of recovery access.
      </p>
    </div>
  </Card>
</template>

<style scoped>
.role-form { display:grid; gap:var(--space-4); max-width:880px; }
.role-form select { max-width:400px; }
</style>
