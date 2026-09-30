<script setup>
// Placeholder for imagery that hasn't been supplied yet. The original mockup used
// a drag-to-fill web component; here it renders the empty state — a dashed ring
// with a centered caption — filling its (position:relative) parent. Swap for a
// real <img> once photography/logos exist.
import { computed } from 'vue'

const props = defineProps({
  placeholder: { type: String, default: 'Image' },
  shape: { type: String, default: 'rect' }, // rect | rounded | circle | pill
  radius: { type: [Number, String], default: 12 },
})

const borderRadius = computed(() => {
  switch (props.shape) {
    case 'circle':
      return '50%'
    case 'pill':
      return '999px'
    case 'rounded':
      return typeof props.radius === 'number' ? `${props.radius}px` : props.radius
    default:
      return '0'
  }
})
</script>

<template>
  <div class="ees-imageslot" :style="{ borderRadius }">
    <div class="ees-imageslot__ring" :style="{ borderRadius }"></div>
    <span class="ees-imageslot__cap">{{ placeholder }}</span>
  </div>
</template>

<style scoped>
.ees-imageslot {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  text-align: center;
  color: var(--text-faint);
  overflow: hidden;
}
.ees-imageslot__ring {
  position: absolute;
  inset: 0;
  border: 1.5px dashed currentColor;
  opacity: 0.35;
  pointer-events: none;
}
.ees-imageslot__cap {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-wide);
  line-height: 1.3;
  max-width: 90%;
  color: var(--text-muted);
}
</style>
