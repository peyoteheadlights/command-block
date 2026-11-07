// The test player for Command Block's "Diagnose server" (agent/joinbot.py).
// It joins once, like a real player, and prints one JSON line per step:
//   {"step": "login", "ok": true}
//   {"step": "spawn", "ok": false, "kick": "You are not white-listed on this server!"}
// Usage: node join.js <host> <port> <username> <version or ""> <chat line>
'use strict'
const mineflayer = require('mineflayer')

const [host, port, username, version, chatLine] = process.argv.slice(2)
const STAY_MS = 10000 // stays this long after the chat line, answering keep-alives
const STEP_MS = 30000 // the most one step may take

let step = 'connect'
let finished = false

function say (line) {
  process.stdout.write(JSON.stringify(line) + '\n')
}

// A kick reason is chat JSON (a string, or {text, extra, translate}); keep the words.
function words (reason) {
  if (reason == null) return ''
  if (typeof reason === 'string') {
    try { return words(JSON.parse(reason)) } catch { return reason }
  }
  if (Array.isArray(reason)) return reason.map(words).join('')
  if (typeof reason === 'object') {
    let text = reason.text || reason.translate || ''
    if (reason[''] !== undefined) text += words(reason[''])
    if (reason.extra) text += words(reason.extra)
    if (reason.value && typeof reason.value === 'object') text += words(reason.value)
    return text
  }
  return String(reason)
}

function done (line, code) {
  if (finished) return
  finished = true
  if (line) say(line)
  try { bot.quit() } catch {}
  setTimeout(() => process.exit(code), 200)
}

function next (name) {
  say({ step, ok: true })
  step = name
  clearTimeout(timer)
  timer = setTimeout(() => done({ step, ok: false, error: 'timed out' }, 1), STEP_MS)
}

let timer = setTimeout(() => done({ step, ok: false, error: 'timed out' }, 1), STEP_MS)

const bot = mineflayer.createBot({
  host,
  port: Number(port),
  username,
  auth: 'offline',
  version: version || false,
  hideErrors: true,
  checkTimeoutInterval: STEP_MS,
  physicsEnabled: false
})

bot._client.on('connect', () => next('login'))
bot.once('login', () => next('spawn'))
bot.once('spawn', async () => {
  next('chunks')
  try {
    await bot.waitForChunksToLoad()
  } catch (err) {
    return done({ step, ok: false, error: String(err && err.message) }, 1)
  }
  next('chat')
  bot.on('messagestr', (message) => {
    if (step === 'chat' && message.includes(chatLine)) {
      next('stay')
      setTimeout(() => {
        say({ step, ok: true })
        step = 'leave'
        done({ step, ok: true }, 0)
      }, STAY_MS)
    }
  })
  bot.chat(chatLine)
})
bot.on('kicked', (reason) => done({ step, ok: false, kick: words(reason) }, 1))
bot.on('error', (err) => done({ step, ok: false, error: String(err && (err.code || err.message)) }, 1))
bot.on('end', (why) => done({ step, ok: false, error: `disconnected (${why})` }, 1))
