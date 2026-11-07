const { randomUUID } = require('crypto')
const { EventEmitter } = require('events')
const { isDeepStrictEqual } = require('util')
const { XboxRTASocket } = require('../rta')
const { withDeadline } = require('../withDeadline')
const debug = require('debug')('prismarine-xbox-services:session')

class XboxSession extends EventEmitter {
  constructor (client, name) {
    super()
    this._client = client
    this.name = name
    this.state = 'opening'
    this._lifetime = new AbortController()
    this._rta = new XboxRTASocket(client)
    this._membershipAttempted = false
    this._refresh = Promise.resolve()
    this._activityPublished = false
    this._pendingChange = false
    this._rta.on('resync', () => this._queueRefresh())
    this._rta.on('error', error => this._fail(error))
    this._rta.on('disconnect', (code, reason) => {
      if (code !== 1006) this._fail(new Error(`Xbox RTA closed: ${code} ${reason}`))
    })
  }

  static async open (client, name, options) {
    // Each session owns its lifetime signal; the HTTP client can be shared.
    client._sessionRef(name || '')
    const session = new XboxSession(client, name ?? options.name ?? randomUUID())
    const timeout = options.timeout ?? client.options.timeout ?? 15000
    try {
      await withDeadline(async signal => {
        const requestOptions = { signal, timeout }
        const profile = await client.getProfile('me', requestOptions)
        signal.throwIfAborted()
        await session._rta.connect(requestOptions)
        const subscription = await session._rta.subscribe('https://sessiondirectory.xboxlive.com/connections/', requestOptions)
        signal.throwIfAborted()
        // Queue refreshes even during startup so a reconnect cannot lose its new connection ID.
        subscription.on('ready', data => {
          if (session.state === 'opening') session._pendingConnection = data
          else session._queueRefresh(data)
        })
        subscription.on('data', () => session._queueRefresh())
        const connection = subscription.initialData.ConnectionId
        const properties = typeof options.properties === 'function' ? await options.properties({ profile }) : options.properties
        signal.throwIfAborted()
        const payload = {
          members: {
            me: {
              constants: { system: { xuid: profile.xuid, initialize: true } },
              properties: { system: { active: true, connection, subscription: { id: randomUUID(), changeTypes: ['everything'] } } }
            }
          }
        }
        if (name === null) payload.properties = properties || {}
        session._membershipAttempted = true
        await session._write(payload, requestOptions)
        signal.throwIfAborted()
        do {
          session._pendingChange = false
          if (session._pendingConnection) {
            const data = session._pendingConnection
            session._pendingConnection = null
            await session._updateConnection(data, requestOptions)
          }
          await session._read(requestOptions)
        } while (session._pendingChange || session._pendingConnection)
        signal.throwIfAborted()
        session.state = 'open'
      }, { signal: options.signal, timeout }, session._lifetime.signal)
      return session
    } catch (error) {
      await session.close()
      throw error
    }
  }

  _run (run, options) {
    if (this.state !== 'open') return Promise.reject(new Error('Xbox session is not open'))
    const timeout = options?.timeout ?? this._client.options.timeout ?? 15000
    return withDeadline(signal => run({ signal, timeout }), { signal: options?.signal, timeout }, this._lifetime.signal)
  }

  async _write (payload, options) {
    await this._client.updateSession(this.name, payload, options)
    if (this._lifetime.signal.aborted) await this._leave()
    options.signal.throwIfAborted()
  }

  get current () {
    return structuredClone(this._current)
  }

  setActivity (options) {
    return this._run(async requestOptions => {
      await this._client.setActivity(this.name, requestOptions)
      this._activityPublished = true
    }, options)
  }

  get (options) {
    return this._run(requestOptions => this._client.getSession(this.name, requestOptions), options)
  }

  updateProperties (properties, options) {
    return this._run(requestOptions => this._write({ properties }, requestOptions), options)
  }

  invite (identifier, options) {
    return this._run(async requestOptions => {
      const xuid = typeof identifier?.xuid === 'string' && /^\d+$/.test(identifier.xuid) && identifier.gamertag === undefined
        ? identifier.xuid
        : (await this._client.getProfile(identifier, requestOptions)).xuid
      requestOptions.signal.throwIfAborted()
      await this._client.sendInvite(this.name, xuid, requestOptions)
    }, options)
  }

  async _updateConnection (data, options) {
    await this._write({ members: { me: { properties: { system: { active: true, connection: data.ConnectionId } } } } }, options)
    if (this._activityPublished) await this._client.setActivity(this.name, options)
  }

  async _read (options) {
    const document = structuredClone(await this._client.getSession(this.name, options))
    options.signal.throwIfAborted()
    const previous = this._current
    this._current = document
    if (!previous || isDeepStrictEqual(previous, document)) return
    // Listener exceptions must not be mistaken for service failures.
    queueMicrotask(() => {
      if (this.state !== 'open') return
      this.emit('changed', structuredClone(document), structuredClone(previous))
      const members = value => new Map(Object.values(value.members || {})
        .filter(member => member?.constants?.system?.xuid)
        .map(member => [String(member.constants.system.xuid), member]))
      const before = members(previous)
      const after = members(document)
      for (const [xuid, member] of after) {
        if (!before.has(xuid)) this.emit('memberJoin', structuredClone(member))
      }
      for (const [xuid, member] of before) {
        if (!after.has(xuid)) this.emit('memberLeave', structuredClone(member))
      }
      if (!isDeepStrictEqual(previous.properties, document.properties)) {
        this.emit('propertiesChanged', structuredClone(document.properties))
      }
    })
  }

  _queueRefresh (data) {
    if (this.state === 'opening') {
      this._pendingChange = true
      return
    }
    this._refresh = this._refresh.then(async () => {
      if (this.state !== 'open') return
      await this._run(async options => {
        if (data) await this._updateConnection(data, options)
        await this._read(options)
      })
    }).catch(error => this._fail(error))
  }

  _fail (error) {
    if (this.state === 'closing' || this.state === 'closed') return
    const established = this.state === 'open'
    this._lifetime.abort(error)
    this.close().then(() => {
      // Emission is outside the cleanup promise; application listener exceptions are not swallowed.
      if (established) queueMicrotask(() => this.emit('error', error))
    })
  }

  async _leave () {
    await this._client.updateSession(this.name, { members: { me: null } }, { timeout: this._client.options.cleanupTimeout ?? 5000 })
      .catch(error => { debug('Failed to leave session %s: %s', this.name, error.message) })
  }

  close () {
    if (this._closing) return this._closing
    this.state = 'closing'
    this._lifetime.abort(new Error('Xbox session is closed'))
    this._closing = (async () => {
      await this._rta.close()
      if (this._membershipAttempted) await this._leave()
      this.state = 'closed'
    })()
    return this._closing
  }
}
module.exports = { XboxSession }
