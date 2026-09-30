// Which features the server says are usable right now.
//
// Fetched once on boot alongside the session probe. The point is that a member
// is told *why* something is unavailable before they try it, rather than
// meeting a 503 after filling in a form.
//
// Same shape as the session store — a plain reactive module, no store library.

import { reactive } from 'vue'

import api from '@/api'

export const status = reactive({
  loaded: false,
  features: {},
  unavailable: [],
  notice: null,
})

/** Is a feature usable? Unknown keys and a failed probe both read as usable:
 *  the server enforces this anyway, and hiding the whole site because one
 *  status request failed would be a worse outcome than a control that errors. */
export function available(feature) {
  return !status.unavailable.includes(feature)
}

/** The member-facing reason, or null when the feature is fine. */
export function reason(feature) {
  return status.features?.[feature]?.reason ?? null
}

export async function refreshStatus() {
  try {
    const data = await api.get('/status')
    status.features = data?.features ?? {}
    status.unavailable = data?.unavailable ?? []
    status.notice = data?.notice ?? null
  } catch {
    // Fail open — see `available` above.
    status.features = {}
    status.unavailable = []
    status.notice = null
  }
  status.loaded = true
  return status
}

export default status
