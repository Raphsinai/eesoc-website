<script setup>
// The EESoc logo. `variant="pins"` is the full chip-package wordmark;
// `variant="mark"` is the standalone routed-trace S.
import { computed } from 'vue'

const props = defineProps({
  variant: { type: String, default: 'pins' }, // pins | mark
  height: { type: [Number, String], default: 40 },
})

const SRC = {
  pins: 'assets/logo-wordmark-pins.svg',
  mark: 'assets/logo-mark-trace.png',
}

const src = computed(() => (import.meta.env.BASE_URL || '/') + SRC[props.variant])
const h = computed(() => (typeof props.height === 'number' ? `${props.height}px` : props.height))
</script>

<template>
  <img :src="src" alt="EESoc" :style="{ height: h, width: 'auto', display: 'block' }" />
</template>
