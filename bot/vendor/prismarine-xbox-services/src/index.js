const { SocketError, SocketClosedError, SocketNotConnectedError, SocketAlreadyConnectedError, RTARequestError } = require('./rta/constants')

module.exports = {
  RTARequestError,
  SocketError,
  SocketClosedError,
  SocketNotConnectedError,
  SocketAlreadyConnectedError,
  ServiceError: require('./errors').ServiceError,
  XboxClient: require('./xbox/client').XboxClient,
  XboxSession: require('./xbox/session').XboxSession,
  XboxRTASubscription: require('./rta/subscription').XboxRTASubscription,
  XboxRTASocket: require('./rta').XboxRTASocket,
  PlayFabClient: require('./playfab/client').PlayFabClient
}
