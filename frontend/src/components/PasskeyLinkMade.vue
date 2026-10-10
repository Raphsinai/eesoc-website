<script setup>
// A passkey link that has just been made, with a way to copy it.
//
// Shown once: the server keeps only the token's hash, so when this goes the
// link cannot be shown again — only replaced by a new one. Nothing is emailed;
// the committee member sends it by whatever reaches the person, which is the
// point (our email is what does not).
import { computed, ref } from 'vue'

import Button from '@/components/ds/Button.vue'

const props = defineProps({ link: { type: Object, required: true } })

const copied = ref(false)
const failed = ref(false)

const until = computed(() =>
  new Date(props.link.expiresAt).toLocaleString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  }),
)

async function copy() {
  failed.value = false
  try {
    await navigator.clipboard.writeText(props.link.url)
    copied.value = true
    setTimeout(() => (copied.value = false), 3000)
  } catch {
    // No clipboard permission: the field selects itself on focus, so copying
    // by hand is one shortcut away.
    failed.value = true
  }
}
</script>

<template>
  <div class="made">
    <span class="made__label">Link for {{ link.email }} — copy it now, it won't be shown again</span>
    <div class="made__row">
      <input
        class="ees-input"
        :value="link.url"
        readonly
        aria-label="Passkey setup link"
        @focus="(e) => e.target.select()"
      />
      <Button size="sm" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</Button>
    </div>
    <span class="made__help">
      Works once, until {{ until }}. Anyone holding it can sign in as
      {{ link.email }}, so send it only to them.
      <template v-if="link.neverSignedIn"> That account has never signed in — check the address is right.</template>
      <template v-if="failed"> Couldn't copy automatically: select the link and copy it.</template>
    </span>
  </div>
</template>

<style scoped>
.made {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: var(--space-3);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-sm, 4px);
}
.made__label {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--text-accent);
}
.made__row {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}
.made__row .ees-input {
  flex: 1;
  min-width: 0;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}
.made__help {
  font: var(--type-2xs);
  color: var(--text-faint);
}
</style>
