<script setup>
// Primary action control. Always uppercase mono with wide tracking.
import { computed, useSlots } from 'vue'

const props = defineProps({
  variant: { type: String, default: 'primary' }, // primary | secondary | accent | ghost | danger
  size: { type: String, default: 'md' }, // sm | md | lg
  block: { type: Boolean, default: false },
  href: { type: String, default: null },
  disabled: { type: Boolean, default: false },
})

const slots = useSlots()

const tag = computed(() => (props.href ? 'a' : 'button'))
const cls = computed(() =>
  [
    'ees-btn',
    `ees-btn--${props.variant}`,
    `ees-btn--${props.size}`,
    props.block ? 'ees-btn--block' : '',
  ]
    .filter(Boolean)
    .join(' '),
)
</script>

<template>
  <component
    :is="tag"
    :class="cls"
    :href="href || undefined"
    :disabled="tag === 'button' ? disabled : undefined"
    :aria-disabled="tag !== 'button' && disabled ? 'true' : undefined"
  >
    <slot name="iconLeft" />
    <slot />
    <slot name="iconRight" />
  </component>
</template>
