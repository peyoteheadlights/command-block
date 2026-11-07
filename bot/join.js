// The test player for Command Block's "Diagnose server" (agent/joinbot.py).
// It reads one JSON line on stdin (what to do, and the saved Microsoft sign-in
// if there is one), joins once like a real player and prints one JSON line per step:
//   {"step": "login", "ok": true}
//   {"step": "spawn", "ok": false, "kick": "You are not white-listed on this server!"}
// Java joins use Mineflayer, Bedrock joins bedrock-protocol.
//
// The Microsoft sign-in (prismarine-auth) never touches the disk here: the
// agent hands it in on stdin and every change comes back as a {"cache": ...}
// line, which the agent keeps encrypted (agent/microsoft.py).
// {"do": "signin"} signs in with a device code instead: it prints
// {"code": ..., "url": ...} for the owner to type in, then {"signed_in": ...}.
'use strict'
const { Titles } = require('prismarine-auth')

const STAY_MS = 10000 // stays this long after the chat line, answering keep-alives
const STEP_MS = 30000 // the most one step may take
// One sign-in for Java and Bedrock: both libraries use this flow by default.
const AUTH = { flow: 'live', authTitle: Titles.MinecraftNintendoSwitch, deviceType: 'Nintendo' }

let job
let step = 'connect'
let finished = false
let timer
let quit = () => {}
const caches = {}

function say (line) {
  process.stdout.write(JSON.stringify(line) + '\n')
}

// prismarine-auth's cache, kept in memory and handed back to the agent.
function cache ({ cacheName }) {
  const saved = () => say({ cache: caches })
  return {
    async reset () { caches[cacheName] = {}; saved(); return caches[cacheName] },
    async getCached () { return (caches[cacheName] ??= {}) },
    async setCached (value) { caches[cacheName] = value; saved() },
    async setCachedPartial (value) { caches[cacheName] = { ...caches[cacheName], ...value }; saved() }
  }
}

// During a join there is nobody to type a code: the saved sign-in ran out.
function noCode () {
  done({ step, ok: false, error: 'signed out' }, 1)
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
  try { quit() } catch {}
  setTimeout(() => process.exit(code), 200)
}

function fail (err) {
  done({ step, ok: false, error: String(err && (err.code || err.message || err)) }, 1)
}

function next (name) {
  if (finished) return
  say({ step, ok: true })
  step = name
  clearTimeout(timer)
  timer = setTimeout(() => done({ step, ok: false, error: 'timed out' }, 1), STEP_MS)
}

// After the chat line came back: stay a while, then leave.
function stayThenLeave () {
  next('stay')
  setTimeout(() => {
    say({ step, ok: true })
    step = 'leave'
    done({ step, ok: true }, 0)
  }, STAY_MS)
}

function joinJava () {
  const mineflayer = require('mineflayer')
  const bot = mineflayer.createBot({
    host: job.host,
    port: job.port,
    username: job.username,
    auth: job.auth === 'microsoft' ? 'microsoft' : 'offline',
    ...AUTH,
    profilesFolder: cache,
    onMsaCode: noCode,
    version: job.version || false,
    hideErrors: true,
    checkTimeoutInterval: STEP_MS,
    physicsEnabled: false
  })
  quit = () => bot.quit()
  bot._client.on('connect', () => next('login'))
  bot.once('login', () => next('spawn'))
  bot.once('spawn', async () => {
    next('chunks')
    try {
      await bot.waitForChunksToLoad()
    } catch (err) {
      return fail(err)
    }
    next('chat')
    bot.on('messagestr', (message) => {
      if (step === 'chat' && message.includes(job.chat)) stayThenLeave()
    })
    bot.chat(job.chat)
  })
  bot.on('kicked', (reason) => done({ step, ok: false, kick: words(reason) }, 1))
  bot.on('error', fail)
  bot.on('end', (why) => done({ step, ok: false, error: `disconnected (${why})` }, 1))
}

// The server's Bedrock version, from its answer to a ping (RakNet, UDP).
// bedrock-protocol's own ping needs raknet-native, which isn't shipped.
async function bedrockVersion (rak) {
  const { Versions } = require('bedrock-protocol/src/options')
  const { fromServerName } = require('bedrock-protocol/src/server/advertisement')
  const ping = new rak.RakClient({ host: job.host, port: job.port })
  let answer
  try {
    answer = fromServerName(await ping.ping(5000))
  } finally {
    ping.close()
  }
  const version = Object.keys(Versions).find(v => Versions[v] === Number(answer.protocol))
  try {
    // Only the newest Bedrock versions' game data ships (agent/joinbot.py).
    require('minecraft-data')('bedrock_' + version).supportFeature('itemRegistryPacket')
  } catch {
    throw Object.assign(new Error(answer.version), { code: 'bedrock version ' + answer.version })
  }
  return version
}

async function joinBedrock () {
  const bedrock = require('bedrock-protocol')
  const rak = require('bedrock-protocol/src/rak')('jsp-raknet')
  const version = job.version || await bedrockVersion(rak)
  const client = bedrock.createClient({
    host: job.host,
    port: job.port,
    username: job.username,
    offline: job.auth !== 'microsoft',
    ...AUTH,
    profilesFolder: cache,
    onMsaCode: noCode,
    version,
    skipPing: true,
    transport: 'raknet',
    raknetBackend: 'jsp-raknet',
    connectTimeout: STEP_MS
  })
  quit = () => client.close()
  let chunks = 0
  client.on('loggingIn', () => next('login')) // reached: it sends its login now
  client.on('join', () => next('spawn')) // the server took the login
  client.on('level_chunk', () => {
    chunks++
    if (step === 'chunks') chat()
  })
  client.once('spawn', () => {
    next('chunks')
    if (chunks) chat()
  })
  function chat () {
    next('chat')
    client.on('text', (packet) => {
      if (step === 'chat' && String(packet.message).includes(job.chat)) stayThenLeave()
    })
    client.queue('text', {
      needs_translation: false,
      category: 'authored',
      type: 'chat',
      source_name: client.username,
      message: job.chat,
      xuid: '',
      platform_chat_id: '',
      has_filtered_message: false,
      filtered_message: ''
    })
  }
  client.on('kick', (packet) => done({ step, ok: false, kick: words(packet.message) || packet.reason }, 1))
  client.on('error', fail)
  client.on('close', () => done({ step, ok: false, error: 'disconnected' }, 1))
}

// Sign in once with a device code. Java name and Xbox gamertag are read when
// the account has them (an account may own only one of the two games).
async function signIn () {
  const { Authflow } = require('prismarine-auth')
  const flow = new Authflow('owner', cache, AUTH, (code) => {
    say({ code: code.user_code, url: code.verification_uri, expires_in: code.expires_in })
  })
  let java = null
  let gamertag = null
  try {
    await flow.getMsaToken()
  } catch (err) {
    return done({ signed_in: false, error: String(err && err.message) }, 1)
  }
  try {
    java = (await flow.getMinecraftJavaToken({ fetchProfile: true })).profile?.name || null
  } catch {}
  try {
    const crypto = require('crypto')
    const { publicKey } = crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' })
    const der = publicKey.export({ format: 'der', type: 'spki' }).toString('base64')
    for (const jwt of await flow.getMinecraftBedrockChain(der)) {
      const body = JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url'))
      gamertag = body.extraData?.displayName || gamertag
    }
  } catch {}
  done({ signed_in: true, java, gamertag }, 0)
}

let input = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', (data) => { input += data })
process.stdin.on('end', () => {
  try {
    job = JSON.parse(input)
  } catch {
    return done({ step, ok: false, error: 'no job' }, 1)
  }
  Object.assign(caches, job.cache || {})
  if (job.do === 'signin') return signIn()
  timer = setTimeout(() => done({ step, ok: false, error: 'timed out' }, 1), STEP_MS)
  const run = job.edition === 'bedrock' ? joinBedrock : joinJava
  Promise.resolve().then(run).catch(fail)
})
