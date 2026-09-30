<script setup>
// Big mono number with an uppercase caption.
defineProps({
  value: { type: [String, Number], default: '' },
  label: { type: String, default: '' },
  sub: { type: String, default: '' },
  align: { type: String, default: 'left' },
})
</script>

<template>
  <div style="display:grid; gap:var(--space-2)" :style="{ textAlign: align }">
    <span class="ees-stat__value">{{ value }}</span>
    <span class="ees-stat__label">{{ label }}</span>
    <span v-if="sub" style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-faint)">{{ sub }}</span>
  </div>
</template>
