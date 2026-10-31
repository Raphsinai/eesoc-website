<script setup>
// Anonymous, unlisted photo page opened from the QR printed at an EESoc event.
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { usePageTitle } from '@/utils/pageTitle'
import api from '@/api'
import Card from '@/components/ds/Card.vue'
import Button from '@/components/ds/Button.vue'

const route = useRoute()
const set = ref(null)
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    set.value = await api.get(`/photos/${route.params.token}`)
  } catch {
    error.value = 'This photo link is unavailable or has been withdrawn.'
  } finally {
    loading.value = false
  }
})
usePageTitle(computed(() => set.value?.title ? `${set.value.title} · Photos` : 'Your photos'))
</script>

<template>
  <main class="photo-gallery">
    <header class="photo-gallery__header">
      <span class="eyebrow">EESoc event photos</span>
      <h1>{{ set?.title || 'Your photos' }}</h1>
      <p class="muted">Your printed keepsake, with digital photographs to take home.</p>
    </header>
    <p v-if="loading" role="status">Loading your photos…</p>
    <Card v-else-if="error" variant="outline"><p role="alert">{{ error }}</p></Card>
    <template v-else-if="set">
      <div class="photo-gallery__actions">
        <Button :href="set.downloadUrl">Download all {{ set.count }} photos (.zip)</Button>
        <Button variant="secondary" :href="set.printUrl" target="_blank">View printed collage</Button>
      </div>
      <div class="photo-gallery__grid">
        <article v-for="(photo, index) in set.images" :key="photo.id" class="photo-gallery__photo">
          <a :href="photo.url" target="_blank" rel="noopener" :aria-label="`Open photo ${index+1}`">
            <img :src="photo.url" :alt="`Photo ${index+1} from ${set.title}`" loading="lazy">
          </a>
          <a class="photo-gallery__download" :href="photo.url" :download="photo.filename">Download photo {{ index+1 }} ↓</a>
        </article>
      </div>
      <p class="muted photo-gallery__privacy">This album is unlisted, but anyone with the QR code or link can access it. Please ask people before reposting their photos.</p>
    </template>
  </main>
</template>

<style scoped>
.photo-gallery { max-width: var(--container-lg); width: 100%; margin: 0 auto; padding: var(--space-10) var(--space-5) var(--space-24); min-height: 65vh; }
.photo-gallery__header { display: grid; justify-items: start; gap: var(--space-3); }
.photo-gallery__header h1 { margin: 0; font: var(--type-h1); overflow-wrap: anywhere; }
.photo-gallery__header p { margin: 0; }
.photo-gallery__actions { display: flex; flex-wrap: wrap; gap: var(--space-3); margin: var(--space-7) 0; }
.photo-gallery__grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(min(100%,280px),1fr)); align-items: start; gap: var(--space-5); }
.photo-gallery__photo { min-width: 0; border: 1px solid var(--border-default); border-radius: var(--radius-md); padding: var(--space-3); }
.photo-gallery__photo img { display: block; width: 100%; height: auto; max-height: 530px; object-fit: contain; background: var(--surface-page); }
.photo-gallery__download { display: block; padding: var(--space-4) var(--space-2) var(--space-2); }
.photo-gallery__privacy { margin-top: var(--space-7); font-size: var(--text-xs); }
</style>
