const { JsonClient } = require('../http')

class PlayFabClient extends JsonClient {
  constructor (getCredentials, options) {
    super(options, 'PlayFab')
    if (!/^[a-z0-9]+$/i.test(options?.titleId || '')) throw new TypeError('PlayFab requires a titleId')
    if (typeof getCredentials !== 'function') throw new TypeError('PlayFab requires a credential provider')
    this.getCredentials = getCredentials
  }

  async getHeaders (config) {
    const credentials = await this.getCredentials()
    const token = config.auth === 'entity' ? credentials.EntityToken?.EntityToken : credentials.SessionTicket
    if (!token) throw new Error(`Missing PlayFab ${config.auth === 'entity' ? 'entity token' : 'session ticket'}`)
    return { [config.auth === 'entity' ? 'X-EntityToken' : 'X-Authorization']: token }
  }

  getTitleData ({ keys } = {}, options = {}) {
    return this.request('Client/GetTitleData', { Keys: keys }, { ...options, auth: 'session' })
  }

  getUserInventory (options = {}) {
    return this.request('Client/GetUserInventory', {}, { ...options, auth: 'session' })
  }

  executeCloudScript ({ functionName, functionParameter, generatePlayStreamEvent }, options = {}) {
    return this.request('Client/ExecuteCloudScript', {
      FunctionName: functionName,
      FunctionParameter: functionParameter,
      GeneratePlayStreamEvent: generatePlayStreamEvent
    }, { ...options, auth: 'session' })
  }

  async request (path, data = {}, options = {}) {
    // Only API paths are accepted; credentials must stay on the configured title host.
    if (!/^[A-Za-z]+\/[A-Za-z][A-Za-z0-9]*$/.test(path)) throw new TypeError('Expected a PlayFab API path such as Client/GetAccountInfo')
    if (options.auth !== undefined && !['session', 'entity'].includes(options.auth)) throw new TypeError('Unknown PlayFab authentication type')
    const response = await this._request('POST', {
      timeout: options.timeout,
      signal: options.signal,
      auth: options.auth,
      url: `https://${this.options.titleId}.playfabapi.com/${path}`,
      data
    })
    return response?.data
  }
}

module.exports = { PlayFabClient }
