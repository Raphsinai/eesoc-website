// HTTP client for the EESoc API.
//
// Same-origin: nginx proxies /api to the api service, so cookies are sent
// without any CORS ceremony. `credentials: 'same-origin'` is still explicit
// because a future absolute VITE_API_BASE would otherwise drop them silently.

const BASE = import.meta.env.VITE_API_BASE || '/api'

/** Read a cookie by name. The CSRF cookie is deliberately not HttpOnly so this
 *  can echo it back in a header — that echo is what proves the request came
 *  from our own page and not a cross-site form. */
function cookie(name) {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`))
  return match ? decodeURIComponent(match[2]) : null
}

/** A request body that fails validation comes back with `detail` as a list of
 *  {loc, msg} objects, not a sentence. Used as an Error message as-is it reads
 *  "[object Object]", so each one becomes "ticket types 1 › price pence: …". */
function describe(detail) {
  if (typeof detail === 'string') return detail
  const inner = detail?.detail
  if (typeof inner === 'string') return inner
  if (Array.isArray(inner) && inner.length) {
    return inner
      .map((item) => {
        if (typeof item === 'string') return item
        const where = (item?.loc || [])
          .filter((part) => part !== 'body')
          .map((part) => (typeof part === 'number' ? part + 1 : String(part).replace(/_/g, ' ')))
          .join(' › ')
        const msg = item?.msg || 'is not valid'
        return where ? `${where}: ${msg}` : msg
      })
      .join('; ')
  }
  return 'Something went wrong'
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(describe(detail))
    this.status = status
    this.detail = detail
  }
}

async function request(method, path, body, params) {
  // Query built by hand rather than with `new URL`: Safari's parser is
  // stricter and throws "The string did not match the expected pattern" on
  // inputs other browsers accept, and a relative path needs no URL object.
  const search = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) search.append(k, String(v))
    }
  }
  const qs = search.toString()
  const url = `${BASE}${path}${qs ? (path.includes('?') ? '&' : '?') + qs : ''}`

  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  // Only unsafe methods need the token; sending it on GETs is noise.
  if (!['GET', 'HEAD'].includes(method)) {
    const token = cookie('csrf')
    if (token) headers['X-CSRF-Token'] = token
  }

  const res = await fetch(url, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (res.status === 204) return null

  const text = await res.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }

  if (!res.ok) throw new ApiError(res.status, data)
  return data
}

export const api = {
  get: (path, params) => request('GET', path, undefined, params),
  post: (path, body, params) => request('POST', path, body ?? {}, params),
  put: (path, body, params) => request('PUT', path, body ?? {}, params),
  patch: (path, body, params) => request('PATCH', path, body ?? {}, params),
  del: (path, params) => request('DELETE', path, undefined, params),
}

export default api
