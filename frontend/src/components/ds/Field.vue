<script setup>
// Label + control + help/error wrapper.
defineProps({
  label: { type: String, default: '' },
  htmlFor: { type: String, default: null },
  help: { type: String, default: '' },
  error: { type: String, default: '' },
  required: { type: Boolean, default: false },
})
</script>

<template>
  <div class="ees-field">
    <label v-if="label" class="ees-label" :for="htmlFor || undefined">
      {{ label }}<span v-if="required" style="color:var(--status-danger)"> *</span>
    </label>
    <slot />
    <span v-if="error || help" :class="error ? 'ees-help ees-help--error' : 'ees-help'">
      {{ error || help }}
    </span>
  </div>
</template>
