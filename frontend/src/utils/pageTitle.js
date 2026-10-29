import { watch } from 'vue'
import { useRoute } from 'vue-router'
import { applySeo } from '@/seo'

/** Keep the current browser-tab title in sync with loaded page data. */
export function usePageTitle(title) {
  const route = useRoute()
  watch([title, () => route.fullPath], ([value]) => {
    applySeo({ title: value || route.meta.title, description: route.meta.description,
      path: route.path, noindex: route.meta.noindex })
  }, { immediate: true, flush: 'post' })
}
