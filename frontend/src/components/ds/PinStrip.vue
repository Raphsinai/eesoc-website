<script setup>
// A run of gold DIP leads. Section divider / poster edge furniture.
import { computed } from 'vue'

const props = defineProps({
  length: { type: [Number, String], default: 18 },
  pitch: { type: [Number, String], default: null },
  color: { type: String, default: null },
})

const style = computed(() => ({
  height: typeof props.length === 'number' ? `${props.length}px` : props.length,
  backgroundSize: props.pitch ? `${props.pitch}px 100%` : undefined,
  backgroundImage: props.color
    ? `repeating-linear-gradient(to right, ${props.color} 0 var(--pin-width), transparent var(--pin-width) var(--pin-pitch))`
    : undefined,
}))
</script>

<template>
  <span class="ees-pinstrip" :style="style" aria-hidden="true"></span>
</template>
