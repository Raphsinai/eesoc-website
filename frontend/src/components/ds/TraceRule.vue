<script setup>
// Horizontal circuit trace with solder pads on the ends. Replaces <hr>.
import { computed } from 'vue'

const props = defineProps({
  pads: { type: String, default: 'both' }, // both | start | end
  color: { type: String, default: null },
})

const showStart = computed(() => props.pads === 'both' || props.pads === 'start')
const showEnd = computed(() => props.pads === 'both' || props.pads === 'end')
</script>

<template>
  <div class="ees-trace" :style="color ? { color } : undefined" aria-hidden="true">
    <span v-if="showStart" class="ees-trace__pad"></span>
    <span class="ees-trace__line"></span>
    <span v-if="showEnd" class="ees-trace__pad"></span>
  </div>
</template>
