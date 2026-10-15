// The service's HTTP face. Only the api reaches it (a NetworkPolicy says so in
// the cluster), and it carries no knowledge of the site: it links, lists
// groups and posts what it is given.
//
//   GET  /health   always 200 while the process is up — a dropped WhatsApp
//                  connection is reported by /status, not by restarting
//   GET  /status   linked or not, as whom, and the QR or pairing code
//   POST /link     {phone?}  start linking
//   POST /unlink   sign this device out and forget the session
//   GET  /groups   the groups the number is in
//   POST /send     {jid, text, key}  post into one group

import http from 'node:http'

import { LinkError } from './link.js'

const MAX_BODY = 64 * 1024

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks = []
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > MAX_BODY) {
        reject(new LinkError(413, 'Too large.'))
        req.destroy()
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => {
      if (!chunks.length) return resolve({})
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')))
      } catch {
        reject(new LinkError(400, 'Not JSON.'))
      }
    })
    req.on('error', reject)
  })
}

function reply(res, status, body) {
  const data = JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(data),
  })
  res.end(data)
}

export function createServer(link, log = console) {
  const routes = {
    'GET /health': async () => ({ ok: true }),
    'GET /status': async () => link.status(),
    'POST /link': async (body) => link.link({ phone: body.phone }),
    'POST /unlink': async () => link.unlink(),
    'GET /groups': async () => ({ groups: await link.groups() }),
    'POST /send': async (body) => link.send({ jid: body.jid, text: body.text, key: body.key }),
  }

  return http.createServer(async (req, res) => {
    const { pathname } = new URL(req.url, 'http://localhost')
    const handler = routes[`${req.method} ${pathname}`]
    if (!handler) return reply(res, 404, { detail: 'Not found.' })
    try {
      const body = req.method === 'POST' ? await readJson(req) : {}
      reply(res, 200, await handler(body))
    } catch (e) {
      // 500 for anything unexpected — this service's own failure, said as one.
      const status = e instanceof LinkError ? e.status : 500
      if (status >= 500) log.error({ err: String(e), path: pathname }, 'request failed')
      reply(res, status, { detail: e.message || 'Something went wrong in the WhatsApp service.' })
    }
  })
}
