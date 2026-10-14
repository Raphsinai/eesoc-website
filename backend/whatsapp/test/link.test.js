// The linked device, driven with a fake Baileys socket: linking, staying
// linked, and posting only the way link.js promises — into groups, one at a
// time, a few seconds apart, never past the hourly cap, never twice for one
// request.

import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, it, mock } from 'node:test'

import { Link, LinkError, bare } from '../src/link.js'
import { createServer } from '../src/server.js'

const quiet = { info() {}, warn() {}, error() {} }
const GROUP = '120363000000000001@g.us'

class FakeSock {
  constructor(config) {
    this.config = config
    this.handlers = {}
    this.ev = { on: (event, fn) => (this.handlers[event] ||= []).push(fn) }
    this.posts = []
    this.user = null
    this.ended = false
    this.loggedOut = false
    this.pairingRequests = []
    this.groupsData = {}
  }

  emit(event, data) {
    for (const fn of this.handlers[event] || []) fn(data)
  }

  async requestPairingCode(phone) {
    this.pairingRequests.push(phone)
    return 'ABCD1234'
  }

  async sendMessage(jid, content) {
    this.posts.push({ jid, text: content.text })
    return { key: { id: `m${this.posts.length}` }, message: { conversation: content.text } }
  }

  async groupFetchAllParticipating() {
    return this.groupsData
  }

  async logout() {
    this.loggedOut = true
  }

  end() {
    this.ended = true
  }
}

const closed = (statusCode) => ({
  connection: 'close',
  lastDisconnect: { error: { output: { statusCode } } },
})

// Waits for every connection update handed to the link so far.
let current
const settle = async () => {
  await current?.updates
  await new Promise((r) => setImmediate(r))
}

let dir
let sockets
let clock
let waits

function makeLink(extra = {}) {
  current = new Link({
    dir,
    log: quiet,
    auth: async (d) => ({
      state: { creds: { registered: await fs.stat(path.join(d, 'registered')).then(() => true, () => false) } },
      saveCreds: async () => {},
    }),
    makeSocket: async (config) => {
      const sock = new FakeSock(config)
      sockets.push(sock)
      return sock
    },
    now: () => clock,
    sleep: async (ms) => {
      waits.push(ms)
      clock += ms
    },
    random: () => 0.5,
    gapMs: [4000, 8000],
    ...extra,
  })
  return current
}

/** Link by QR and connect, as a phone scanning the code would. */
async function linked(extra) {
  const link = makeLink(extra)
  await link.link()
  const sock = sockets.at(-1)
  sock.user = { id: '447700900123:5@s.whatsapp.net', lid: '99887766@lid', verifiedName: 'EESoc' }
  sock.emit('connection.update', { connection: 'open' })
  await settle()
  return { link, sock }
}

beforeEach(async () => {
  dir = await fs.mkdtemp(path.join(os.tmpdir(), 'wa-test-'))
  sockets = []
  clock = 1_000_000
  waits = []
})

afterEach(async () => {
  mock.timers.reset()
  await fs.rm(dir, { recursive: true, force: true })
})

describe('linking', () => {
  it('shows the QR code while linking, and who it is once the phone confirms', async () => {
    const link = makeLink()
    await link.link()
    const sock = sockets[0]
    sock.emit('connection.update', { qr: 'qr-data' })
    await settle()
    assert.equal(link.status().state, 'linking')
    assert.equal(link.status().qr, 'qr-data')

    sock.user = { id: '447700900123:5@s.whatsapp.net', verifiedName: 'EESoc' }
    sock.emit('connection.update', { connection: 'open' })
    await settle()
    const status = link.status()
    assert.equal(status.state, 'connected')
    assert.deepEqual(status.me, { id: '447700900123@s.whatsapp.net', name: 'EESoc' })
    assert.equal(status.qr, null)
  })

  it('asks for one pairing code when given a number, and never shows the QR', async () => {
    const link = makeLink()
    await link.link({ phone: '+44 7700 900123' })
    const sock = sockets[0]
    sock.emit('connection.update', { qr: 'qr-1' })
    await settle()
    sock.emit('connection.update', { qr: 'qr-2' })
    await settle()
    assert.deepEqual(sock.pairingRequests, ['447700900123'])
    assert.equal(link.status().pairingCode, 'ABCD-1234')
    assert.equal(link.status().qr, null)
  })

  it('refuses a number too short to have a country code', async () => {
    await assert.rejects(makeLink().link({ phone: '07700' }), (e) => e.status === 400)
  })

  it('reconnects as itself after WhatsApp asks for a restart', async () => {
    const link = makeLink()
    await link.link()
    sockets[0].emit('connection.update', closed(515))
    await settle()
    assert.equal(sockets.length, 2)
    assert.equal(link.status().state, 'linking')
  })

  it('gives up cleanly when nobody confirms the link', async () => {
    const link = makeLink()
    await link.link()
    await fs.writeFile(path.join(dir, 'creds.json'), '{}')
    sockets[0].emit('connection.update', closed(408))
    await settle()
    assert.equal(link.status().state, 'unlinked')
    assert.match(link.status().error, /before the phone confirmed/)
    assert.deepEqual(await fs.readdir(dir), [])
  })

  it('refuses to link a second number over a connected one', async () => {
    const { link } = await linked()
    await assert.rejects(link.link(), (e) => e.status === 409)
  })
})

describe('staying linked', () => {
  it('forgets the session when the phone signs the device out', async () => {
    const { link, sock } = await linked()
    await fs.writeFile(path.join(dir, 'creds.json'), '{}')
    sock.emit('connection.update', closed(401))
    await settle()
    assert.equal(link.status().state, 'unlinked')
    assert.match(link.status().error, /signed out/)
    assert.deepEqual(await fs.readdir(dir), [])
  })

  it('reconnects after a dropped connection, backing off', async () => {
    mock.timers.enable({ apis: ['setTimeout'] })
    const { link, sock } = await linked()
    sock.emit('connection.update', closed(428))
    await settle()
    assert.equal(link.status().state, 'reconnecting')
    assert.equal(sockets.length, 1)
    mock.timers.tick(2000)
    await settle()
    assert.equal(sockets.length, 2)
  })

  it('backs right off when another connection takes the session over', async () => {
    mock.timers.enable({ apis: ['setTimeout'] })
    const { link, sock } = await linked()
    sock.emit('connection.update', closed(440))
    await settle()
    assert.match(link.status().error, /Another connection/)
    mock.timers.tick(60_000)
    await settle()
    assert.equal(sockets.length, 1)
    mock.timers.tick(4 * 60_000)
    await settle()
    assert.equal(sockets.length, 2)
  })

  it('ignores what an old socket says after a new one has taken over', async () => {
    const link = makeLink()
    await link.link()
    const old = sockets[0]
    await link.link()
    old.emit('connection.update', closed(401))
    await settle()
    assert.equal(link.status().state, 'linking')
  })

  it('reconnects with a saved session at startup, and starts clean without one', async () => {
    await fs.writeFile(path.join(dir, 'registered'), '')
    const restored = makeLink()
    await restored.start()
    assert.equal(sockets.length, 1)
    assert.equal(restored.status().state, 'connecting')

    await fs.rm(path.join(dir, 'registered'))
    await fs.writeFile(path.join(dir, 'half-linked.json'), '{}')
    const fresh = makeLink()
    await fresh.start()
    assert.equal(sockets.length, 1)
    assert.equal(fresh.status().state, 'unlinked')
    assert.deepEqual(await fs.readdir(dir), [])
  })

  it('signs out and forgets the session on unlink', async () => {
    const { link, sock } = await linked()
    await link.unlink()
    assert.ok(sock.loggedOut)
    assert.equal(link.status().state, 'unlinked')
    // Its closing is not taken for a dropped connection.
    sock.emit('connection.update', closed(428))
    await settle()
    assert.equal(link.status().state, 'unlinked')
  })
})

describe('groups', () => {
  it('says which groups it can post in', async () => {
    const { link, sock } = await linked()
    sock.groupsData = {
      'c@g.us': { id: 'c@g.us', subject: 'EESoc', isCommunity: true, participants: [] },
      'a@g.us': {
        id: 'a@g.us', subject: 'EESoc Announcements', announce: true, isCommunityAnnounce: true,
        linkedParent: 'c@g.us', size: 412,
        participants: [{ id: '99887766@lid', admin: 'admin' }],
      },
      'y@g.us': {
        id: 'y@g.us', subject: 'Year 2', announce: true,
        participants: [{ id: '447700900123@s.whatsapp.net', admin: null }],
      },
      'o@g.us': { id: 'o@g.us', subject: 'Open chat', participants: [{ id: 'x@s.whatsapp.net' }] },
    }
    const groups = Object.fromEntries((await link.groups()).map((g) => [g.jid, g]))
    assert.equal(groups['c@g.us'].canPost, false) // a community is a container
    assert.equal(groups['a@g.us'].canPost, true) // admin, matched by LID
    assert.equal(groups['a@g.us'].size, 412)
    assert.deepEqual(groups['a@g.us'].parent, { jid: 'c@g.us', name: 'EESoc' })
    assert.equal(groups['y@g.us'].canPost, false) // admins only, and not one
    assert.equal(groups['o@g.us'].canPost, true)
    // Fetched once, kept for sending.
    assert.equal(await sock.config.cachedGroupMetadata('a@g.us'), sock.groupsData['a@g.us'])
  })

  it('refuses while nothing is linked', async () => {
    await assert.rejects(makeLink().groups(), (e) => e.status === 409)
  })
})

describe('posting', () => {
  it('posts into groups and nothing else', async () => {
    const { link, sock } = await linked()
    await assert.rejects(
      link.send({ jid: '447700900123@s.whatsapp.net', text: 'hi' }),
      (e) => e instanceof LinkError && e.status === 400,
    )
    await assert.rejects(link.send({ jid: GROUP, text: '   ' }), (e) => e.status === 400)
    const res = await link.send({ jid: GROUP, text: '*Hello*' })
    assert.equal(res.id, 'm1')
    assert.deepEqual(sock.posts, [{ jid: GROUP, text: '*Hello*' }])
  })

  it('refuses while not connected', async () => {
    await assert.rejects(makeLink().send({ jid: GROUP, text: 'x' }), (e) => e.status === 409)
  })

  it('leaves a pause between posts', async () => {
    const { link } = await linked()
    await link.send({ jid: GROUP, text: 'one' })
    await link.send({ jid: GROUP, text: 'two' })
    // min 4s + 0.5 × (8s − 4s)
    assert.deepEqual(waits, [6000])
  })

  it('posts one at a time even when asked at once', async () => {
    const { link, sock } = await linked()
    await Promise.all([1, 2, 3].map((n) => link.send({ jid: GROUP, text: `post ${n}` })))
    assert.deepEqual(sock.posts.map((p) => p.text), ['post 1', 'post 2', 'post 3'])
    assert.equal(waits.length, 2)
  })

  it('stops at the hourly cap', async () => {
    const { link } = await linked({ maxPerHour: 2 })
    await link.send({ jid: GROUP, text: 'one' })
    await link.send({ jid: GROUP, text: 'two' })
    await assert.rejects(link.send({ jid: GROUP, text: 'three' }), (e) => e.status === 429)
    clock += 3600 * 1000
    await link.send({ jid: GROUP, text: 'an hour later' })
  })

  it('answers a repeated request with the first post instead of a second', async () => {
    const { link, sock } = await linked()
    const [a, b] = await Promise.all([
      link.send({ jid: GROUP, text: 'once', key: 'post:7' }),
      link.send({ jid: GROUP, text: 'once', key: 'post:7' }),
    ])
    assert.deepEqual(a, b)
    assert.equal(sock.posts.length, 1)
  })

  it('keeps what it sent, so a member whose phone asks again can be answered', async () => {
    const { link, sock } = await linked()
    const { id } = await link.send({ jid: GROUP, text: 'resend me' })
    assert.deepEqual(await sock.config.getMessage({ id }), { conversation: 'resend me' })
  })
})

describe('http', () => {
  async function call(server, method, route, body) {
    const { port } = server.address()
    const res = await fetch(`http://127.0.0.1:${port}${route}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
    return { status: res.status, body: await res.json() }
  }

  it('maps the link to routes, and its refusals to statuses', async () => {
    const link = makeLink()
    const server = createServer(link, quiet).listen(0)
    await new Promise((r) => server.once('listening', r))
    try {
      assert.equal((await call(server, 'GET', '/health')).status, 200)
      assert.equal((await call(server, 'GET', '/status')).body.state, 'unlinked')
      const refused = await call(server, 'POST', '/send', { jid: GROUP, text: 'x' })
      assert.equal(refused.status, 409)
      assert.match(refused.body.detail, /No WhatsApp number is linked/)
      assert.equal((await call(server, 'GET', '/nothing')).status, 404)
    } finally {
      server.close()
    }
  })
})

it('bare() names the account, not the device', () => {
  assert.equal(bare('447700900123:12@s.whatsapp.net'), '447700900123@s.whatsapp.net')
  assert.equal(bare(null), null)
})
