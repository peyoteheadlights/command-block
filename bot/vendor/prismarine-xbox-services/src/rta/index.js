// Adapted from https://github.com/LucienHH/xbox-rta.
const { EventEmitter, once } = require('events')
const { withDeadline } = require('../withDeadline')
const { XboxRTASubscription } = require('./subscription')
const { MessageType, StatusCode, RTARequestError, SocketError, SocketClosedError, SocketNotConnectedError, SocketAlreadyConnectedError } = require('./constants')
const debug = require('debug')('prismarine-xbox-services:rta')
const NONCE_URL = 'https://rta.xboxlive.com/nonce'
const SOCKET_URL = 'wss://rta.xboxlive.com/connect'
const SOCKET_PROTOCOL = 'rta.xboxlive.com.V2'
const CONNECTION_RENEWAL_MS = 90 * 60 * 1000
const REQUEST_TIMEOUT_MS = 30_000

class XboxRTASocket extends EventEmitter {
  _pendingRequests = new Map()
  _connectController = null
  _socketListeners = null
  closed = false
  _subscriptions = new Set()
  ws = null
  reconnectTimeout = null
  sequenceId = 0
  constructor (client) {
    super()
    this.client = client
  }

  async connect (options = {}) {
    if (this.closed) throw new SocketClosedError()
    if (this._connectController || this.ws) throw new SocketAlreadyConnectedError()
    this.options = { timeout: options.timeout }
    const controller = new AbortController()
    this._connectController = controller
    try {
      await withDeadline(async signal => {
        const nonce = await this._getNonce(signal)
        await this._connectSocket(nonce, signal)
        await this._restoreSubscriptions(signal)
        signal.throwIfAborted()
        debug('RTA connected and subscriptions restored')
        this._scheduleRenewal()
      }, options, controller.signal)
    } catch (error) {
      // An older cancelled attempt must not clean up a replacement connection.
      if (this._connectController === controller) this.releaseConnection(error)
      throw error
    } finally {
      if (this._connectController === controller) this._connectController = null
    }
  }

  async close () {
    if (this.closed) return
    this.closed = true
    this._connectController?.abort(new SocketClosedError())
    this.releaseConnection(new SocketClosedError())
    for (const subscription of this._subscriptions) subscription._dispose()
    this._subscriptions.clear()
    this.emit('close')
  }

  async reconnect () {
    if (this.closed) throw new SocketClosedError()
    this._connectController?.abort(new SocketError('RTA connection reconnecting'))
    this.releaseConnection(new SocketError('RTA connection reconnecting'))
    this._connectController = null
    return this.connect(this.options)
  }

  // Shared by server close, failed startup and explicit destruction.
  releaseConnection (error) {
    if (this.reconnectTimeout) { clearTimeout(this.reconnectTimeout) }
    this.reconnectTimeout = null
    for (const subscription of this._subscriptions) subscription._id = null
    for (const pending of this._pendingRequests.values()) { pending.reject(error) }
    this._socketListeners?.abort()
    this._socketListeners = null
    const ws = this.ws
    this.ws = null
    if (ws) {
      // Release local work immediately; native WebSocket owns transport shutdown.
      ws.close()
    }
  }

  async subscribe (uri, options = {}) {
    if (this.closed) throw new SocketClosedError()
    if (this._connectController) throw new SocketNotConnectedError()
    const subscription = new XboxRTASubscription(this, uri)
    try {
      await this._subscribe(subscription, options)
      this._subscriptions.add(subscription)
      return subscription
    } catch (error) {
      subscription._dispose()
      this._subscriptions.delete(subscription)
      throw error
    }
  }

  async _subscribe (subscription, options = {}) {
    const response = await this._request(MessageType.Subscribe, subscription.uri, {
      ...options, signal: AbortSignal.any([subscription._lifetime.signal, ...[options.signal].filter(Boolean)])
    })
    options.signal?.throwIfAborted()
    if (this.closed) throw new SocketClosedError()
    if (subscription.closed) {
      await this._request(MessageType.Unsubscribe, response.subscriptionId)
      subscription._lifetime.signal.throwIfAborted()
    }
    subscription._id = response.subscriptionId
    subscription.initialData = response.data
    subscription.emit('ready', response.data)
  }

  async _unsubscribe (subscription) {
    this._subscriptions.delete(subscription)
    if (this.ws?.readyState === WebSocket.OPEN && subscription._id !== null) {
      await this._request(MessageType.Unsubscribe, subscription._id)
    }
  }

  async _request (type, payload, options = {}) {
    if (this.closed) throw new SocketClosedError()
    if (this.ws?.readyState !== WebSocket.OPEN) throw new SocketNotConnectedError()
    const socket = this.ws
    const sequenceId = this.sequenceId++
    const data = JSON.stringify([type, sequenceId, payload])
    try {
      return await withDeadline(signal => new Promise((resolve, reject) => {
        this._pendingRequests.set(sequenceId, { resolve, reject, type, signal })
        signal.throwIfAborted()
        if (this.ws !== socket) throw new SocketClosedError()
        socket.send(data)
      }), { signal: options.signal, timeout: options.timeout ?? REQUEST_TIMEOUT_MS })
    } finally {
      this._pendingRequests.delete(sequenceId)
    }
  }

  async _getNonce (signal) {
    const { nonce } = await this.client.request('GET', NONCE_URL, { signal, timeout: this.options.timeout })
    return nonce
  }

  _scheduleRenewal () {
    this.reconnectTimeout = setTimeout(() => {
      this.reconnect().catch(error => {
        if (!this.closed) this.emit('error', error)
      })
    }, CONNECTION_RENEWAL_MS)
  }

  async _connectSocket (nonce, signal) {
    signal.throwIfAborted()
    const socket = new WebSocket(`${SOCKET_URL}?nonce=${encodeURIComponent(nonce)}`, SOCKET_PROTOCOL)
    this.ws = socket
    this._socketListeners = new AbortController()
    const listeners = { signal: this._socketListeners.signal }
    socket.addEventListener('error', this.onSocketError, listeners)
    socket.addEventListener('close', this.onSocketClose, listeners)
    socket.addEventListener('message', this.onSocketMessage, listeners)
    await once(socket, 'open', { signal })
    signal.throwIfAborted()
  }

  async _restoreSubscriptions (signal) {
    await Promise.all([...this._subscriptions].map(async subscription => {
      subscription._id = null
      try {
        await this._subscribe(subscription, { signal })
      } catch (error) {
        if (!subscription.closed) throw error
      }
    }))
  }

  onSocketError = event => {
    if (event.target !== this.ws) return
    const error = event.error || new SocketError(event.message || 'RTA WebSocket failed')
    debug('RTA error', error)
    if (this._connectController) this._connectController.abort(error)
    else if (!this.closed) this.emit('error', error)
  }

  onSocketClose = ({ target, code, reason }) => {
    if (target !== this.ws) return
    if (this._connectController) {
      this._connectController.abort(new SocketClosedError(`RTA connection closed: ${code} ${reason}`))
      return
    }
    debug(`RTA disconnected: ${code} ${reason}`)
    this.releaseConnection(new SocketClosedError(`RTA connection closed: ${code} ${reason}`))
    this.emit('disconnect', code, reason)
    if (code === 1006 && !this.closed) {
      this.connect(this.options).catch(error => {
        if (!this.closed) { this.emit('error', error) }
      })
    }
  }

  onSocketMessage = ({ target, data: res }) => {
    if (target !== this.ws) return
    if (!(typeof res === 'string')) { return debug('Received non-string message', res) }
    let msgJson
    try {
      msgJson = JSON.parse(res)
      if (!Array.isArray(msgJson)) throw new Error('Invalid RTA message: expected an array')
    } catch (error) {
      this.emit('error', error)
      return
    }
    if (this.closed) return
    const messageType = msgJson[0]
    debug('Received message', res)
    switch (messageType) {
      case MessageType.Subscribe:
      case MessageType.Unsubscribe: {
        const [type, sequenceId, status, subscriptionId, data] = msgJson
        const pending = this._pendingRequests.get(sequenceId)
        if (!pending || pending.signal.aborted) {
          this._pendingRequests.delete(sequenceId)
          if (type === MessageType.Subscribe && status === StatusCode.Success && this.ws?.readyState === WebSocket.OPEN) {
            this._request(MessageType.Unsubscribe, subscriptionId).catch(error => {
              if (!this.closed) this.emit('error', error)
            })
          }
          return
        }
        if (pending.type !== type) return
        this._pendingRequests.delete(sequenceId)
        if (status !== StatusCode.Success) {
          pending.reject(new RTARequestError(status))
        } else {
          pending.resolve({ subscriptionId, data })
        }
        break
      }
      case MessageType.Event: {
        const [, subscriptionId, data] = msgJson
        for (const subscription of this._subscriptions) {
          if (subscription._id === subscriptionId) subscription.emit('data', data)
        }
        break
      }
      case MessageType.Resync: {
        this.emit('resync')
        break
      }
      default:
        debug('Received unknown message', res)
        break
    }
  }
}

module.exports = { XboxRTASocket }
