<script setup>
// Small uppercase status/metadata chip. Not clickable.
import { computed } from 'vue'

const props = defineProps({
  tone: { type: String, default: 'neutral' }, // neutral | brand | accent | success | danger | solid
})

const cls = computed(() => ['ees-badge', `ees-badge--${props.tone}`].join(' '))
</script>

<template>
  <span :class="cls"><slot /></span>
</template>
