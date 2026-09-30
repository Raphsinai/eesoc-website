<script setup>
// Wraps content in a DIP-package outline with gold leads top and bottom.
import { computed } from 'vue'

const props = defineProps({
  pins: { type: String, default: 'both' }, // both | top | bottom
  padding: { type: String, default: 'var(--space-6)' },
})

const showTop = computed(() => props.pins === 'both' || props.pins === 'top')
const showBottom = computed(() => props.pins === 'both' || props.pins === 'bottom')
</script>

<template>
  <div style="position:relative; display:inline-block">
    <span v-if="showTop" class="ees-chip__pins ees-chip__pins--top"></span>
    <span v-if="showBottom" class="ees-chip__pins ees-chip__pins--bottom"></span>
    <div class="ees-chip" :style="{ padding }">
      <slot />
    </div>
  </div>
</template>
