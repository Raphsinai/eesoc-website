<script setup>
// Shown in place of a control that cannot work right now.
//
// States the reason rather than just "unavailable": a member who knows sign-in
// codes are not sending will wait, whereas one who meets a dead button assumes
// the whole site is broken and emails the committee.
//
// Styling lives in the design system as `.ees-maint`, not in a scoped block —
// same as every other component here.
defineProps({
  reason: { type: String, default: null },
  // Compact form, for sitting beside a disabled button. The default is a full
  // panel that replaces the control outright.
  inline: { type: Boolean, default: false },
  title: { type: String, default: 'Under maintenance' },
})
</script>

<template>
  <div :class="['ees-maint', inline && 'ees-maint--inline']" role="status">
    <span class="ees-maint__icon" aria-hidden="true">
      <svg viewBox="0 0 16 16" width="15" height="15" fill="none"
           stroke="currentColor" stroke-width="1.5">
        <circle cx="8" cy="8" r="6.4" />
        <path d="M8 4.6v4" stroke-linecap="round" />
        <circle cx="8" cy="11.3" r=".8" fill="currentColor" stroke="none" />
      </svg>
    </span>
    <div class="ees-maint__body">
      <p v-if="!inline" class="ees-maint__title">{{ title }}</p>
      <p class="ees-maint__text">
        {{ reason || 'This is temporarily unavailable while we sort something out.' }}
      </p>
    </div>
  </div>
</template>
