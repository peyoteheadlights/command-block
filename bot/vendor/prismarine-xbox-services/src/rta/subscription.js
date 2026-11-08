const { EventEmitter } = require('events')

class XboxRTASubscription extends EventEmitter {
  constructor (rta, uri) {
    super()
    this.uri = uri
    this.initialData = undefined
    this.closed = false
    this._rta = rta
    this._id = null
    this._lifetime = new AbortController()
  }

  _dispose () {
    this.closed = true
    this._lifetime.abort(new Error('RTA subscription is closed'))
  }

  close () {
    if (this._closing) return this._closing
    if (this.closed) return Promise.resolve()
    this._dispose()
    this._closing = this._rta._unsubscribe(this)
    return this._closing
  }
}
module.exports = { XboxRTASubscription }
