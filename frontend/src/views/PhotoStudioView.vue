<script setup>
// Full-screen event-day shortcut to the same studio in Committee admin.
import PhotoStudioPanel from '@/components/PhotoStudioPanel.vue'
</script>

<template>
  <main class="photo-studio-page">
    <RouterLink to="/admin?tab=photos" class="photo-studio-page__back">← Committee admin</RouterLink>
    <PhotoStudioPanel />
  </main>
</template>

<style scoped>
.photo-studio-page { max-width: 950px; width: 100%; margin: 0 auto; padding: var(--space-8) var(--space-5) var(--space-24); }
.photo-studio-page__back { display: inline-block; margin-bottom: var(--space-6); }
</style>
