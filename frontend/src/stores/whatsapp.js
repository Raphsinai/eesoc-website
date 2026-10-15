// The WhatsApp link, as the admin screens see it.
//
// The card that links the number and enables groups (WhatsAppLink) reads and
// refreshes it; the enabled groups themselves reach the audience lists through
// GET /admin/email/audiences, like every other audience.
//
// Same shape as the session store — a plain reactive module, no store library.

import { computed, reactive } from 'vue'

import api from '@/api'

export const whatsapp = reactive({
  loaded: false,
  // GET /admin/whatsapp: status, groups, chosen, allowlist, posts.
  data: null,
  error: '',
})

/** An error as one sentence. When the site itself does not answer, the edge
 *  (Cloudflare) answers with a whole HTML page, which is not one — and pasted
 *  into the card it buries everything else on it. */
export function problem(e) {
  const message = String(e?.message || '')
  if (message.trimStart().startsWith('<')) {
    return `The site didn't answer (${e?.status ?? 'no response'}). Try again in a minute.`
  }
  return message || 'Something went wrong.'
}

let pending = null

/** Fetch it again. Calls made while one is in flight share it. */
export function loadWhatsApp() {
  pending ??= api
    .get('/admin/whatsapp')
    .then((data) => {
      whatsapp.data = data
      whatsapp.error = ''
    })
    .catch((e) => {
      whatsapp.error = problem(e)
    })
    .finally(() => {
      whatsapp.loaded = true
      pending = null
    })
  return pending
}

export const connected = computed(() => whatsapp.data?.status?.state === 'connected')

/** What a group is called on screen. A community's announcements channel is
 *  named after the community, so it says what it is — it is one place a post
 *  can go, beside the community's groups, never a stand-in for them. */
export function groupLabel(g) {
  if (g.communityAnnouncements) return `Announcements — ${g.parent?.name || g.name}`
  return g.name || g.jid
}

export default whatsapp
