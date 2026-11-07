const MessageType = { Subscribe: 1, Unsubscribe: 2, Event: 3, Resync: 4 }
const StatusCode = { Success: 0, UnknownResource: 1, SubscriptionLimitReached: 2, NoResourceData: 3, Throttled: 1001, ServiceUnavailable: 1002 }
const convertRTAStatus = status => Object.keys(StatusCode).find(key => StatusCode[key] === status) || 'Unknown'

class SocketError extends Error {
  constructor (message, options) {
    super(message, options)
    this.name = new.target.name
  }
}
class SocketClosedError extends SocketError {
  constructor (message = 'RTA connection is closed') { super(message) }
}
class SocketNotConnectedError extends SocketError {
  constructor () { super('RTA is not connected') }
}
class SocketAlreadyConnectedError extends SocketError {
  constructor () { super('RTA connection already started') }
}

class RTARequestError extends SocketError {
  constructor (status) {
    const code = convertRTAStatus(status)
    super(`RTA request failed: ${status} ${code}`)
    this.status = status
    this.code = code
  }
}

module.exports = { MessageType, StatusCode, convertRTAStatus, SocketError, SocketClosedError, SocketNotConnectedError, SocketAlreadyConnectedError, RTARequestError }
