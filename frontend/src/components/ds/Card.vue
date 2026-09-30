<script setup>
// Container surface. `offset` is the signature silkscreen treatment.
import { computed } from 'vue'

const props = defineProps({
  variant: { type: String, default: 'default' }, // default | outline | offset | raised
  interactive: { type: Boolean, default: false },
  padded: { type: Boolean, default: true },
  as: { type: String, default: 'div' },
})

const cls = computed(() =>
  [
    'ees-card',
    `ees-card--${props.variant}`,
    props.interactive ? 'ees-card--interactive' : '',
    props.padded ? 'ees-card__pad' : '',
  ]
    .filter(Boolean)
    .join(' '),
)
</script>

<template>
  <component :is="as" :class="cls">
    <slot />
  </component>
</template>
