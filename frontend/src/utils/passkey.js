// The browser half of a passkey ceremony.
//
// The server speaks WebAuthn's JSON form, where every binary field is a
// base64url string; navigator.credentials speaks ArrayBuffers. This converts
// between the two by hand rather than with PublicKeyCredential.parse…FromJSON
// and .toJSON(): those are recent enough that a sponsor on a managed laptop a
// couple of Safari versions behind would get a button that does nothing.

function toBase64url(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (const b of bytes) binary += String.fromCharCode(b)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function fromBase64url(value) {
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/')
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

/** Can this browser make and use passkeys at all? A browser without WebAuthn
 *  is never offered the buttons; one with it but no authenticator gets the
 *  browser's own "use a phone or security key" sheet, which is the right
 *  answer there. */
export function passkeysSupported() {
  return typeof window !== 'undefined'
    && typeof window.PublicKeyCredential === 'function'
    && Boolean(navigator.credentials?.create)
}

function descriptors(list) {
  return (list || []).map((d) => ({ ...d, id: fromBase64url(d.id) }))
}

/** Ask the authenticator to make a passkey from the server's creation options,
 *  and return the answer in the JSON form the server verifies. */
export async function createPasskey(options) {
  const credential = await navigator.credentials.create({
    publicKey: {
      ...options,
      challenge: fromBase64url(options.challenge),
      user: { ...options.user, id: fromBase64url(options.user.id) },
      excludeCredentials: descriptors(options.excludeCredentials),
    },
  })
  const r = credential.response
  return {
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    clientExtensionResults: credential.getClientExtensionResults?.() ?? {},
    response: {
      clientDataJSON: toBase64url(r.clientDataJSON),
      attestationObject: toBase64url(r.attestationObject),
      transports: r.getTransports?.() ?? [],
    },
  }
}

/** Ask the authenticator to sign the server's challenge with a passkey the
 *  person picks, and return the answer in JSON form. */
export async function getPasskey(options) {
  const credential = await navigator.credentials.get({
    publicKey: {
      ...options,
      challenge: fromBase64url(options.challenge),
      allowCredentials: descriptors(options.allowCredentials),
    },
  })
  const r = credential.response
  return {
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    clientExtensionResults: credential.getClientExtensionResults?.() ?? {},
    response: {
      clientDataJSON: toBase64url(r.clientDataJSON),
      authenticatorData: toBase64url(r.authenticatorData),
      signature: toBase64url(r.signature),
      userHandle: r.userHandle ? toBase64url(r.userHandle) : null,
    },
  }
}

/** True when the person closed the browser's passkey sheet or it timed out —
 *  not a failure worth an error message, just a "no". */
export function cancelled(error) {
  return error?.name === 'NotAllowedError' || error?.name === 'AbortError'
}

/** "Chrome on macOS" — what the account page will call this passkey, so
 *  somebody with three of them can tell which to remove. */
export function deviceName() {
  const ua = navigator.userAgent || ''
  const os = /iPhone/.test(ua) ? 'iPhone'
    : /iPad/.test(ua) ? 'iPad'
    : /Android/.test(ua) ? 'Android'
    : /Mac OS X|Macintosh/.test(ua) ? 'macOS'
    : /Windows/.test(ua) ? 'Windows'
    : /CrOS/.test(ua) ? 'ChromeOS'
    : /Linux/.test(ua) ? 'Linux'
    : null
  const browser = /Edg\//.test(ua) ? 'Edge'
    : /Firefox\//.test(ua) ? 'Firefox'
    : /Chrome\//.test(ua) ? 'Chrome'
    : /Safari\//.test(ua) ? 'Safari'
    : null
  return [browser, os].filter(Boolean).join(' on ') || 'Passkey'
}

/** The message for a passkey attempt that went wrong. Cancelling is a choice,
 *  not an error; InvalidStateError on create means this device already holds
 *  a passkey for the account. */
export function passkeyError(error) {
  if (cancelled(error)) return ''
  if (error?.name === 'InvalidStateError') {
    return 'This device already has a passkey for your account.'
  }
  if (error?.name === 'SecurityError') {
    return "Passkeys can't be used on this address. Open the site at its usual address and try again."
  }
  return error?.message || 'Something went wrong with the passkey.'
}
