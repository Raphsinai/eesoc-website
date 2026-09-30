<script setup>
// Single-line text input. Supports v-model.
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  invalid: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const cls = computed(() => ['ees-input'].join(' '))
</script>

<template>
  <input
    :class="cls"
    :value="modelValue"
    :aria-invalid="invalid || undefined"
    @input="emit('update:modelValue', $event.target.value)"
  />
</template>
