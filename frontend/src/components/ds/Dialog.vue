<script setup>
// Modal. Renders nothing when `open` is false.
import { watch, onBeforeUnmount } from 'vue'
import IconButton from './IconButton.vue'
import Icon from './Icon.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  width: { type: String, default: null },
})
const emit = defineEmits(['close'])

function close() {
  emit('close')
}

function onKey(e) {
  if (e.key === 'Escape') close()
}

watch(
  () => props.open,
  (isOpen) => {
    if (typeof document === 'undefined') return
    if (isOpen) {
      document.addEventListener('keydown', onKey)
      document.body.style.overflow = 'hidden'
    } else {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="ees-dialog__scrim" @click="close">
      <div
        class="ees-dialog"
        role="dialog"
        aria-modal="true"
        :aria-label="title || undefined"
        :style="width ? { maxWidth: width } : undefined"
        @click.stop
      >
        <div class="ees-dialog__head">
          <h2 class="ees-dialog__title">{{ title }}</h2>
          <IconButton label="Close" @click="close">
            <Icon name="x" :size="18" />
          </IconButton>
        </div>
        <div class="ees-dialog__body"><slot /></div>
        <div v-if="$slots.footer" class="ees-dialog__foot"><slot name="footer" /></div>
      </div>
    </div>
  </Teleport>
</template>
