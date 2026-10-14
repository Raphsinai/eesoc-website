// The society's WhatsApp number, linked to the site as one of its devices.
//
// The site posts announcements into the society's own groups and community by
// being a linked device on its WhatsApp Business app number — the way WhatsApp
// Web is one. WhatsApp's Business API cannot do this (it cannot post into a
// group it did not create, or one larger than eight), so this is the only way
// to it; and it is not a way WhatsApp's terms allow. If WhatsApp decides this
// device is a bot, the number that runs the community is the one banned, with
// no appeal. Everything below is shaped by that:
//
//   * groups only — `send` refuses anything that is not a group, so this can
//     never become a way to message people one by one;
//   * slowly — one post at a time, a few seconds apart, and a hard cap per
//     hour that a bug or a loop runs into long before WhatsApp notices it;
//   * quietly — it never shows as online (the phone keeps its notifications),
//     reads no history, and keeps no messages beyond what a resend needs;
//   * one connection — the deployment is one replica, Recreate, the same
//     shape as the eActivities bridge and for the same kind of reason.
//
// Nothing here decides *what* to post or *when*: the api does, and only ever
// because a committee member pressed send or approved a time.
//
// The Baileys socket is passed in rather than imported, so the tests can drive
// this with a fake one. main.js wires the real thing.

import fs from 'node:fs/promises'

// Baileys' DisconnectReason values, by number: importing the enum would load
// the whole library into every test.
const LOGGED_OUT = 401
const CONNECTION_REPLACED = 440
const RESTART_REQUIRED = 515

const HOUR = 3600 * 1000

export class LinkError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

/** "447700900123:12@s.whatsapp.net" → "447700900123@s.whatsapp.net": the same
 *  account, whichever of its devices the id names. */
export function bare(jid) {
  if (!jid) return null
  const [user, server] = String(jid).split('@')
  return `${user.split(':')[0]}@${server}`
}

export class Link {
  /**
   * @param {object} o
   * @param {string} o.dir                where the session lives (a volume)
   * @param {Function} o.auth             (dir) => {state, saveCreds} — useMultiFileAuthState
   * @param {Function} o.makeSocket       (config) => socket — makeWASocket, plus our defaults
   * @param {number} [o.maxPerHour]       posts allowed in any rolling hour
   * @param {number[]} [o.gapMs]          [min, max] pause between two posts
   */
  constructor({ dir, auth, makeSocket, log = console, maxPerHour = 20, gapMs = [4000, 9000],
    now = Date.now, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), random = Math.random }) {
    Object.assign(this, { dir, auth, makeSocket, log, maxPerHour, gapMs, now, sleep, random })
    this.state = 'unlinked'
    this.error = null
    this.since = now()
    this.qr = null
    this.pairingCode = null
    this.me = null
    this.sock = null
    this.mode = null // while linking: { phone } or { qr: true }
    this.attempts = 0
    this.timer = null
    this.stopping = false
    // What a resend needs: when a member's phone cannot decrypt a post, it
    // asks for it again, and the message is re-encrypted from here. Without
    // this, people in a large group are left with "Waiting for this message".
    this.sent = new Map()
    // Group details for sending, so each post does not fetch them again.
    this.meta = new Map()
    // Posts already made, by the caller's key: a retried request gets the
    // first answer instead of a second post.
    this.done = new Map()
    this.recent = []
    this.lastSentAt = 0
    this.chain = Promise.resolve()
    this.updates = Promise.resolve()
  }

  status() {
    return {
      state: this.state,
      error: this.error,
      since: new Date(this.since).toISOString(),
      me: this.me,
      qr: this.state === 'linking' ? this.qr : null,
      pairingCode: this.state === 'linking' ? this.pairingCode : null,
      postsThisHour: this._recent().length,
      maxPerHour: this.maxPerHour,
    }
  }

  _set(state, error = null) {
    if (state !== this.state) this.since = this.now()
    this.state = state
    this.error = error
  }

  // ------------------------------------------------------------ lifecycle

  /** Reconnect with the saved session, if there is one. */
  async start() {
    const { state } = await this.auth(this.dir)
    if (state.creds?.registered) {
      this._set('connecting')
      await this._connect()
    } else {
      // A link that was started and never finished leaves keys that belong
      // to nobody; the next link starts clean.
      await this._wipe()
    }
  }

  async stop() {
    this.stopping = true
    clearTimeout(this.timer)
    this._end()
  }

  /** Start linking. With a phone number, WhatsApp gives an eight-character
   *  code to type on the phone; without one, a QR code to scan. */
  async link({ phone } = {}) {
    if (this.state === 'connected') {
      throw new LinkError(409, 'Already linked. Unlink it first to link another number.')
    }
    const digits = phone ? String(phone).replace(/\D/g, '') : ''
    if (phone && digits.length < 8) {
      throw new LinkError(400, 'Give the whole number with its country code, e.g. 447700900123.')
    }
    clearTimeout(this.timer)
    this._end()
    await this._wipe()
    this.mode = digits ? { phone: digits, asked: false } : { qr: true }
    this.qr = null
    this.pairingCode = null
    this.me = null
    this._set('linking')
    await this._connect()
    return this.status()
  }

  /** Sign this device out of the account and forget the session. */
  async unlink() {
    clearTimeout(this.timer)
    const sock = this.sock
    this.sock = null // its closing must not schedule a reconnect
    if (sock && this.state === 'connected') {
      try {
        await sock.logout()
      } catch (e) {
        this.log.warn({ err: String(e) }, 'logout failed; forgetting the session anyway')
      }
    }
    try {
      sock?.end?.(undefined)
    } catch {
      // already closed
    }
    await this._wipe()
    this.mode = null
    this.me = null
    this.meta.clear()
    this._set('unlinked')
    return this.status()
  }

  async _wipe() {
    await fs.rm(this.dir, { recursive: true, force: true })
    await fs.mkdir(this.dir, { recursive: true })
  }

  _end() {
    const sock = this.sock
    this.sock = null
    try {
      sock?.end?.(undefined)
    } catch {
      // already closed
    }
  }

  async _connect() {
    const { state, saveCreds } = await this.auth(this.dir)
    const sock = await this.makeSocket({
      auth: state,
      getMessage: async (key) => this.sent.get(key.id)?.message,
      cachedGroupMetadata: async (jid) => this.meta.get(jid),
    })
    this.sock = sock
    sock.ev.on('creds.update', saveCreds)
    // One at a time, in order: handling a close wipes files and reconnects,
    // and the next update must not start on a half-handled one.
    sock.ev.on('connection.update', (update) => {
      this.updates = this.updates
        .then(() => this._onUpdate(sock, update))
        .catch((e) => this.log.error({ err: String(e) }, 'connection update failed'))
    })
    sock.ev.on('groups.update', (updates) => {
      for (const u of updates || []) this.meta.delete(u.id)
    })
    sock.ev.on('group-participants.update', (u) => this.meta.delete(u?.id))
  }

  async _onUpdate(sock, { connection, lastDisconnect, qr }) {
    if (sock !== this.sock || this.stopping) return // an old socket's last words

    if (qr) {
      if (this.state !== 'linking') {
        // Asked to link while we thought we were: the saved session is dead.
        this._end()
        await this._wipe()
        this._set('unlinked', 'WhatsApp no longer accepts this link. Link the number again.')
        return
      }
      if (this.mode?.phone) {
        if (!this.mode.asked) {
          this.mode.asked = true
          const code = await sock.requestPairingCode(this.mode.phone)
          this.pairingCode = code.length === 8 ? `${code.slice(0, 4)}-${code.slice(4)}` : code
        }
      } else {
        this.qr = qr
      }
    }

    if (connection === 'open') {
      this.attempts = 0
      this.mode = null
      this.qr = null
      this.pairingCode = null
      this.me = {
        id: bare(sock.user?.id),
        name: sock.user?.verifiedName || sock.user?.name || sock.user?.notify || null,
      }
      this._set('connected')
      this.log.info({ me: this.me.id }, 'linked and connected')
      return
    }

    if (connection !== 'close') return
    const code = lastDisconnect?.error?.output?.statusCode

    if (code === RESTART_REQUIRED) {
      // Normal straight after a code is accepted: WhatsApp closes the first
      // connection and the new device reconnects as itself.
      this.sock = null
      await this._connect()
      return
    }
    if (code === LOGGED_OUT) {
      this.sock = null
      await this._wipe()
      this.me = null
      this._set('unlinked', 'This device was signed out — from the phone, or by WhatsApp. Link the number again.')
      this.log.warn('logged out')
      return
    }
    if (this.state === 'linking') {
      this.sock = null
      await this._wipe()
      this._set('unlinked', 'Linking stopped before the phone confirmed it. Start again.')
      return
    }

    this.sock = null
    this.attempts += 1
    let delay = Math.min(60_000, 2000 * 2 ** Math.min(this.attempts - 1, 5))
    let why = `Connection lost (${code ?? 'no code'}); reconnecting.`
    if (code === CONNECTION_REPLACED) {
      // Something else connected with this same session. Fighting over it is
      // exactly the pattern that gets a number flagged, so back right off.
      delay = 5 * 60_000
      why = 'Another connection took over this link. Waiting five minutes before trying again.'
    }
    this._set('reconnecting', why)
    this.log.warn({ code, delay }, 'connection closed')
    this.timer = setTimeout(() => {
      // On the same queue as the updates, so a reconnect never races one.
      this.updates = this.updates
        .then(() => this._connect())
        .catch((e) => this._set('reconnecting', `Could not reconnect: ${e.message}`))
    }, delay)
  }

  // --------------------------------------------------------------- groups

  _connected() {
    if (this.state !== 'connected' || !this.sock) {
      throw new LinkError(409, this.state === 'unlinked'
        ? 'No WhatsApp number is linked.'
        : `WhatsApp is not connected right now (${this.state}).`)
    }
    return this.sock
  }

  /** Every group the number is in, and whether it can post there. */
  async groups() {
    const sock = this._connected()
    const all = Object.values((await sock.groupFetchAllParticipating()) || {})
    const mine = new Set([bare(sock.user?.id), bare(sock.user?.lid)].filter(Boolean))
    const names = Object.fromEntries(all.map((g) => [g.id, g.subject]))
    for (const g of all) this.meta.set(g.id, g)
    return all
      .map((g) => {
        const me = (g.participants || []).find((p) =>
          [p.id, p.lid, p.phoneNumber].some((id) => mine.has(bare(id))),
        )
        const admin = Boolean(me?.admin)
        return {
          jid: g.id,
          name: g.subject || '',
          size: g.size ?? (g.participants || []).length,
          // Only admins may write in it — a community's announcements group,
          // or a group set that way.
          adminsOnly: Boolean(g.announce),
          admin,
          community: Boolean(g.isCommunity),
          communityAnnouncements: Boolean(g.isCommunityAnnounce),
          parent: g.linkedParent ? { jid: g.linkedParent, name: names[g.linkedParent] || '' } : null,
          // A community itself is a container; its announcements group is
          // where its posts go.
          canPost: !g.isCommunity && (!g.announce || admin),
        }
      })
      .sort((a, b) => a.name.localeCompare(b.name))
  }

  // ---------------------------------------------------------------- send

  _recent() {
    const cutoff = this.now() - HOUR
    this.recent = this.recent.filter((t) => t > cutoff)
    return this.recent
  }

  /** Post `text` into one group. `key` makes a retry of the same request
   *  return the first answer instead of posting twice. */
  send({ jid, text, key }) {
    if (!/^[0-9-]+@g\.us$/.test(String(jid || ''))) {
      return Promise.reject(new LinkError(400, 'Only groups can be posted to.'))
    }
    if (!String(text || '').trim()) {
      return Promise.reject(new LinkError(400, 'Nothing to post.'))
    }
    if (key && this.done.has(key)) return this.done.get(key)

    const run = () => this._post(jid, String(text))
    const result = this.chain.then(run, run)
    this.chain = result.catch(() => {})
    if (key) {
      this.done.set(key, result)
      // A failed post may be tried again under the same key.
      result.catch(() => this.done.delete(key))
      if (this.done.size > 1000) this.done.delete(this.done.keys().next().value)
    }
    return result
  }

  async _post(jid, text) {
    this._connected()
    if (this._recent().length >= this.maxPerHour) {
      throw new LinkError(429, `${this.maxPerHour} posts in the last hour is the limit. Try again later.`)
    }
    const [min, max] = this.gapMs
    const wait = this.lastSentAt + min + this.random() * (max - min) - this.now()
    if (this.lastSentAt && wait > 0) await this.sleep(wait)

    const sock = this._connected() // still, after the pause
    const msg = await sock.sendMessage(jid, { text })
    this.lastSentAt = this.now()
    this.recent.push(this.lastSentAt)
    const id = msg?.key?.id || null
    if (id) {
      this.sent.set(id, { message: msg.message })
      if (this.sent.size > 300) this.sent.delete(this.sent.keys().next().value)
    }
    this.log.info({ jid, id }, 'posted')
    return { id, sentAt: new Date(this.lastSentAt).toISOString() }
  }
}
