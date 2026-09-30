<script setup>
// Square icon-only control. Always pass `label` — it becomes the accessible name.
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  variant: { type: String, default: 'ghost' }, // ghost | outline | solid
  size: { type: String, default: 'md' }, // sm | md | lg
})

const cls = computed(() =>
  ['ees-iconbtn', `ees-iconbtn--${props.variant}`, `ees-iconbtn--${props.size}`].join(' '),
)
</script>

<template>
  <button type="button" :aria-label="label" :class="cls">
    <slot />
  </button>
</template>
