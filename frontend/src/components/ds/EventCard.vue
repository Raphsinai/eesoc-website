<script setup>
// Standard listing card for an EESoc event.
import { computed, useAttrs } from 'vue'
import Card from './Card.vue'
import Badge from './Badge.vue'
import Icon from './Icon.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  category: { type: String, default: '' },
  title: { type: String, default: '' },
  date: { type: String, default: '' },
  time: { type: String, default: '' },
  venue: { type: String, default: '' },
  status: { type: String, default: '' },
  statusTone: { type: String, default: 'success' },
  href: { type: String, default: null },
})

const attrs = useAttrs()
const interactive = computed(() => Boolean(props.href || attrs.onClick))
</script>

<template>
  <Card
    variant="offset"
    :interactive="interactive"
    :as="href ? 'a' : 'div'"
    :href="href || undefined"
    v-bind="attrs"
    style="display:grid; gap:var(--space-4); text-decoration:none"
  >
    <div style="display:flex; align-items:center; justify-content:space-between; gap:var(--space-3)">
      <span style="font:var(--type-eyebrow); letter-spacing:var(--tracking-eyebrow); text-transform:uppercase; color:var(--text-accent)">{{ category }}</span>
      <Badge v-if="status" :tone="statusTone">{{ status }}</Badge>
    </div>
    <h3 style="margin:0; font:var(--type-h2); font-size:var(--text-xl); letter-spacing:var(--tracking-heading); color:var(--text-strong)">{{ title }}</h3>
    <div class="ees-eventcard__meta">
      <span v-if="date" style="display:inline-flex; gap:6px; align-items:center"><Icon name="calendar" :size="14" />{{ date }}</span>
      <span v-if="time" style="display:inline-flex; gap:6px; align-items:center"><Icon name="clock" :size="14" />{{ time }}</span>
      <span v-if="venue" style="display:inline-flex; gap:6px; align-items:center"><Icon name="map-pin" :size="14" />{{ venue }}</span>
    </div>
  </Card>
</template>
