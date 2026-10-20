<script setup>
import { computed } from 'vue'

const props = defineProps({
  permissions: { type: Array, required: true },
  modelValue: { type: Array, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const groups = computed(() => {
  const result = new Map()
  for (const permission of props.permissions) {
    if (!result.has(permission.group)) result.set(permission.group, [])
    result.get(permission.group).push(permission)
  }
  return [...result.entries()].map(([label, items]) => ({ label, items }))
})

function toggle(permission, checked) {
  const next = new Set(props.modelValue)
  if (checked) next.add(permission)
  else next.delete(permission)
  emit('update:modelValue', [...next].sort())
}
</script>

<template>
  <div class="permission-groups">
    <fieldset v-for="group in groups" :key="group.label" class="permission-group" :disabled="disabled">
      <legend>{{ group.label }}</legend>
      <div class="permission-items">
        <label v-for="permission in group.items" :key="permission.id" class="permission-item">
          <input type="checkbox" :checked="modelValue.includes(permission.id)" :disabled="disabled"
            @change="toggle(permission.id, $event.target.checked)" />
          <span>
            <strong>{{ permission.label }}</strong>
            <small>{{ permission.description }}</small>
          </span>
        </label>
      </div>
    </fieldset>
  </div>
</template>

<style scoped>
.permission-groups { display:grid; gap:var(--space-4); }
.permission-group { margin:0; padding:var(--space-3); border:1px solid var(--border-hairline); border-radius:var(--radius-sm); }
.permission-group legend { padding:0 var(--space-2); font:var(--type-label); color:var(--text-strong); }
.permission-items { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr)); gap:var(--space-3); }
.permission-item { display:flex; gap:var(--space-2); align-items:flex-start; font:var(--type-small); color:var(--text-body); cursor:pointer; }
.permission-item input { flex-shrink:0; margin-top:4px; accent-color:var(--brand); }
.permission-item span { display:grid; gap:2px; }
.permission-item strong { font:inherit; font-weight:600; }
.permission-item small { font:var(--type-2xs); color:var(--text-muted); line-height:1.5; }
</style>
