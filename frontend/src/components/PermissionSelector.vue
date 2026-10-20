<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  permissions: { type: Array, required: true },
  modelValue: { type: Array, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const search = ref('')
const selected = computed(() => new Set(props.modelValue))
const groups = computed(() => {
  const map = new Map()
  for (const permission of props.permissions) {
    if (!map.has(permission.group)) map.set(permission.group, new Map())
    const subgroups = map.get(permission.group)
    const subgroup = permission.subgroup || 'Other'
    if (!subgroups.has(subgroup)) subgroups.set(subgroup, [])
    subgroups.get(subgroup).push(permission)
  }
  return [...map].map(([label, subgroups]) => ({
    label,
    items: [...subgroups.values()].flat(),
    subgroups: [...subgroups].map(([name, items]) => ({ name, items })),
  }))
})
const filteredGroups = computed(() => {
  const needle = search.value.trim().toLowerCase()
  if (!needle) return groups.value
  return groups.value
    .map((group) => ({
      ...group,
      subgroups: group.subgroups.map((subgroup) => ({
        ...subgroup,
        items: subgroup.items.filter((p) =>
          [p.label, p.id, p.description, p.group, p.subgroup]
            .some((v) => String(v || '').toLowerCase().includes(needle))),
      })).filter((subgroup) => subgroup.items.length),
    }))
    .filter((group) => group.subgroups.length)
})
const count = (items) => items.filter((item) => selected.value.has(item.id)).length
const full = (items) => items.length > 0 && count(items) === items.length
const partial = (items) => count(items) > 0 && !full(items)

function setMany(items, checked) {
  const result = new Set(props.modelValue)
  // Bulk selects the *whole* category or subcategory, including items hidden by
  // a search. No category/wildcard is written to the backend as a permission.
  for (const item of items) {
    if (checked) result.add(item.id)
    else result.delete(item.id)
  }
  emit('update:modelValue', [...result].sort())
}
function selectAll(checked) { setMany(props.permissions, checked) }
</script>

<template>
  <section class="permissions" :aria-label="'Individual permissions'">
    <div class="permissions__toolbar">
      <input v-model="search" class="permissions__search" type="search"
        placeholder="Search permissions or actions…" aria-label="Search permissions" />
      <span class="permissions__count">{{ modelValue.length }} of {{ permissions.length }} selected</span>
      <button type="button" class="permissions__bulk" :disabled="disabled" @click="selectAll(true)">Select all</button>
      <button type="button" class="permissions__bulk" :disabled="disabled" @click="selectAll(false)">Clear all</button>
    </div>
    <p class="permissions__help">Choose categories, subcategories or individual actions. A category is a shortcut for ticking every action inside it; you can still untick any button-level permission.</p>
    <p v-if="!filteredGroups.length" class="permissions__help">No permissions match your search.</p>
    <details v-for="group in filteredGroups" :key="group.label" class="permissions__group" :open="Boolean(search.trim()) || undefined">
      <summary>
        <span class="permissions__name">{{ group.label }}</span>
        <span class="permissions__fraction">{{ count(group.items) }}/{{ group.items.length }}</span>
      </summary>
      <div class="permissions__group-body">
        <div class="permissions__group-actions">
          <label class="permissions__toggle">
            <input type="checkbox" :disabled="disabled" :checked="full(group.items)"
              :indeterminate.prop="partial(group.items)"
              @change="setMany(group.items, $event.target.checked)" />
            All {{ group.label }} permissions
          </label>
        </div>
        <fieldset v-for="subgroup in group.subgroups" :key="subgroup.name" class="permissions__subgroup" :disabled="disabled">
          <legend>
            <label class="permissions__toggle">
              <input type="checkbox" :checked="full(subgroup.items)"
                :indeterminate.prop="partial(subgroup.items)"
                @change="setMany(subgroup.items, $event.target.checked)" />
              {{ subgroup.name }} <span class="permissions__fraction">({{ count(subgroup.items) }}/{{ subgroup.items.length }})</span>
            </label>
          </legend>
          <div class="permissions__grid">
            <label v-for="permission in subgroup.items" :key="permission.id" class="permissions__item">
              <input type="checkbox" :checked="selected.has(permission.id)"
                @change="setMany([permission], $event.target.checked)" />
              <span>
                <strong>{{ permission.label }}</strong>
                <small>{{ permission.id }}</small>
              </span>
            </label>
          </div>
        </fieldset>
      </div>
    </details>
  </section>
</template>

<style scoped>
.permissions { display:grid; gap:var(--space-3); min-width:0; }
.permissions__toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2); }
.permissions__search { flex:1; min-width:min(100%,210px); padding:var(--space-2) var(--space-3); border:1px solid var(--border-hairline); border-radius:var(--radius-sm); background:var(--surface); color:var(--text-strong); font:var(--type-small); }
.permissions__count, .permissions__fraction { font:var(--type-2xs); color:var(--text-muted); }
.permissions__bulk { background:transparent; border:0; cursor:pointer; color:var(--text-brand); font:var(--type-small); text-decoration:underline; padding:var(--space-1); }
.permissions__bulk:disabled { opacity:.4; cursor:default; }
.permissions__help { margin:0; font:var(--type-2xs); color:var(--text-muted); }
.permissions__group { border:1px solid var(--border-hairline); border-radius:var(--radius-sm); overflow:hidden; }
.permissions__group summary { display:flex; align-items:center; gap:var(--space-3); cursor:pointer; padding:var(--space-3); background:var(--surface); }
.permissions__name { font-weight:600; flex:1; }
.permissions__group-body { padding:var(--space-3); display:grid; gap:var(--space-3); }
.permissions__group-actions { display:flex; gap:var(--space-2); }
.permissions__subgroup { border:1px solid var(--border-hairline); border-radius:var(--radius-sm); padding:var(--space-3); margin:0; min-width:0; }
.permissions__subgroup legend { padding:0 var(--space-2); }
.permissions__toggle { font:var(--type-small); display:inline-flex; align-items:center; gap:var(--space-2); cursor:pointer; }
.permissions__grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,250px),1fr)); gap:var(--space-3); }
.permissions__item { display:flex; align-items:flex-start; gap:var(--space-2); cursor:pointer; font:var(--type-small); min-width:0; }
.permissions__item span { display:grid; gap:2px; min-width:0; }
.permissions__item strong { font:inherit; font-weight:500; }
.permissions__item small { color:var(--text-muted); font:var(--type-2xs); overflow-wrap:anywhere; }
.permissions input[type='checkbox'] { margin-top:2px; flex:0 0 auto; accent-color:var(--action-primary); }
</style>
