<script setup>
// Pill for filters and keywords. Clickable when a click handler or `removable` is set.
import { computed, useAttrs } from 'vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  selected: { type: Boolean, default: false },
  removable: { type: Boolean, default: false },
})

const emit = defineEmits(['remove'])
const attrs = useAttrs()

const clickable = computed(() => Boolean(attrs.onClick) || props.removable)
const cls = computed(() =>
  [
    'ees-tag',
    props.selected ? 'ees-tag--selected' : '',
    clickable.value ? 'ees-tag--clickable' : '',
  ]
    .filter(Boolean)
    .join(' '),
)

function onRemove(e) {
  e.stopPropagation()
  emit('remove', e)
}
</script>

<template>
  <span
    :class="cls"
    v-bind="attrs"
    :role="attrs.onClick ? 'button' : undefined"
    :tabindex="attrs.onClick ? 0 : undefined"
  >
    <slot />
    <span v-if="removable" class="ees-tag__x" aria-hidden="true" @click="onRemove">×</span>
  </span>
</template>
