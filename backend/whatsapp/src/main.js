// Entry point: the real Baileys socket, the HTTP server, and a clean stop.
//
// Configuration, all optional:
//   PORT                        8080
//   WHATSAPP_SESSION_DIR        /data/session — on a volume, so a restart does
//                               not mean linking the phone again
//   WHATSAPP_MAX_POSTS_PER_HOUR 20
//   WHATSAPP_GAP_SECONDS        "4-9" — the pause between two posts
//   LOG_LEVEL                   info (Baileys' own chatter is held at warn)

import makeWASocket, { Browsers, fetchLatestBaileysVersion, useMultiFileAuthState } from 'baileys'
import pino from 'pino'

import { Link } from './link.js'
import { createServer } from './server.js'

const log = pino({ level: process.env.LOG_LEVEL || 'info' })
const [gapMin, gapMax] = (process.env.WHATSAPP_GAP_SECONDS || '4-9').split('-').map(Number)

let version = null

async function makeSocket(config) {
  if (!version) {
    // WhatsApp refuses clients that claim an old version of WhatsApp Web.
    // Falls back to the version Baileys shipped with if the lookup fails.
    const latest = await fetchLatestBaileysVersion().catch(() => null)
    version = latest?.version
  }
  return makeWASocket({
    ...config,
    ...(version ? { version } : {}),
    logger: log.child({ module: 'baileys' }, { level: 'warn' }),
    browser: Browsers.ubuntu('Chrome'),
    printQRInTerminal: false,
    // Never "online": while a linked device is, the phone stops getting
    // notifications — and the committee reads the community on the phone.
    markOnlineOnConnect: false,
    // Nothing here reads chats, so no full history. The small initial sync
    // Baileys' default still takes is left alone: it carries the account's
    // LID mappings, and without them Baileys warns of broken sessions — the
    // last thing a link that must look ordinary can afford. Nothing received
    // is stored.
    syncFullHistory: false,
    generateHighQualityLinkPreview: false,
  })
}

const link = new Link({
  dir: process.env.WHATSAPP_SESSION_DIR || '/data/session',
  auth: useMultiFileAuthState,
  makeSocket,
  log,
  maxPerHour: Number(process.env.WHATSAPP_MAX_POSTS_PER_HOUR || 20),
  gapMs: [gapMin * 1000, (gapMax || gapMin) * 1000],
})

const server = createServer(link, log)
server.listen(Number(process.env.PORT || 8080), () => log.info('listening'))

link.start().catch((e) => log.error({ err: String(e) }, 'could not restore the session'))

async function shutdown(signal) {
  log.info({ signal }, 'stopping')
  server.close()
  await link.stop()
  process.exit(0)
}
process.on('SIGTERM', () => shutdown('SIGTERM'))
process.on('SIGINT', () => shutdown('SIGINT'))
