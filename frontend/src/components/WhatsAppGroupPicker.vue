<script setup>
// "Also post on WhatsApp", for a send that has no "Who gets it" list of its
// own — the roles digest, a sponsor's marketing. Where there is such a list,
// WhatsApp groups are in it instead, beside the email audiences.
//
// Every group the linked number can post in, live; none ticked until somebody
// ticks it. Shows nothing at all where WhatsApp posting isn't switched on.
import { computed, onMounted, ref } from 'vue'

import api from '@/api'

const props = defineProps({
  // The ticked groups' ids.
  modelValue: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const data = ref(null) // { configured, environment, groups }

onMounted(async () => {
  try {
    data.value = await api.get('/admin/whatsapp/targets')
  } catch {
    data.value = null // not worth an error on a screen about something else
  }
})

const groups = computed(() => data.value?.groups ?? [])

function toggle(jid) {
  const now = props.modelValue
  emit('update:modelValue', now.includes(jid) ? now.filter((j) => j !== jid) : [...now, jid])
}
</script>

<template>
  <div v-if="data?.configured" class="wgp">
    <span class="wgp__label">Also post on WhatsApp</span>
    <label v-for="g in groups" :key="g.jid" class="wgp__row">
      <input type="checkbox" :checked="modelValue.includes(g.jid)" :disabled="disabled" @change="toggle(g.jid)" />
      <span>{{ g.name }}<span class="wgp__meta"> · {{ g.size }} people</span></span>
    </label>
    <p v-if="!groups.length" class="wgp__meta">
      No WhatsApp groups to post into — link the society's number on the WhatsApp tab.
    </p>
    <p v-else-if="modelValue.length && data.environment === 'staging'" class="wgp__warn">
      This is staging, but WhatsApp is real: these groups get the post.
    </p>
  </div>
</template>

<style scoped>
.wgp {
  display: grid;
  gap: var(--space-2);
}

.wgp__label {
  font: var(--type-label);
  color: var(--text-muted);
}

.wgp__row {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-3);
  align-items: start;
  font: var(--type-small);
  cursor: pointer;
}

.wgp__row input {
  margin-top: 3px;
  width: 15px;
  height: 15px;
  accent-color: var(--action-primary);
}

.wgp__meta {
  margin: 0;
  font: var(--type-2xs);
  color: var(--text-muted);
}

.wgp__warn {
  margin: 0;
  font: var(--type-2xs);
  color: var(--danger);
}
</style>
