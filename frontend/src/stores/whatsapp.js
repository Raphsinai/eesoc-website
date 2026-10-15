// The WhatsApp link, as the admin screens see it.
//
// Shared because two kinds of component need it: the card that links the
// number and chooses groups (WhatsAppLink), and every "WhatsApp version" block
// (WhatsAppPost), which offers "Post to …" only when there is somewhere to post.
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

/** The chosen groups a post would actually go to here. */
export const targets = computed(() =>
  connected.value ? (whatsapp.data?.chosen ?? []).filter((g) => g.allowed) : [],
)

export default whatsapp
