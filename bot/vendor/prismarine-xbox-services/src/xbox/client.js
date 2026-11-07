const { JsonClient } = require('../http')
const { XboxSession } = require('./session')

class XboxClient extends JsonClient {
  constructor (authflow, options = {}) {
    super(options, 'Xbox')
    this.authflow = authflow
  }

  request (method, url, options = {}) {
    return this._request(method, { ...options, url })
  }

  async getHeaders () {
    const auth = await this.authflow.getXboxToken('http://xboxlive.com')
    return { authorization: `XBL3.0 x=${auth.userHash};${auth.XSTSToken}`, 'accept-language': 'en-US' }
  }

  _sessionRef (name) {
    for (const field of ['scid', 'templateName']) {
      if (!this.options[field]) throw new TypeError(`Xbox session requires ${field}`)
    }
    return { scid: this.options.scid, templateName: this.options.templateName, name }
  }

  _sessionUrl (name) {
    const ref = this._sessionRef(name)
    return `https://sessiondirectory.xboxlive.com/serviceconfigs/${encodeURIComponent(ref.scid)}/sessionTemplates/${encodeURIComponent(ref.templateName)}/sessions/${encodeURIComponent(ref.name)}`
  }

  async getProfile (identifier = 'me', options = {}) {
    let user
    if (identifier === 'me') user = 'me'
    else if (typeof identifier?.xuid === 'string' && /^\d+$/.test(identifier.xuid) && identifier.gamertag === undefined) user = `xuids(${identifier.xuid})`
    else if (typeof identifier?.gamertag === 'string' && identifier.xuid === undefined) user = `gt(${encodeURIComponent(identifier.gamertag)})`
    else throw new TypeError('Expected me, { xuid }, or { gamertag }')
    const response = await this.request('GET', `https://profile.xboxlive.com/users/${user}/settings?settings=Gamertag,GameDisplayName,GameDisplayPicRaw`, { ...options, contractVersion: '2' })
    const profile = response.profileUsers[0]
    const settings = Object.fromEntries((profile.settings || []).map(({ id, value }) => [id, value]))
    return { xuid: String(profile.id), gamertag: settings.Gamertag, displayName: settings.GameDisplayName, avatarUrl: settings.GameDisplayPicRaw }
  }

  async getActivityHandles (xuid, options = {}) {
    if (!this.options.scid) throw new TypeError('Xbox activity lookup requires scid')
    const response = await this.request('POST', 'https://sessiondirectory.xboxlive.com/handles/query?include=relatedInfo,customProperties', {
      ...options,
      data: { type: 'activity', scid: this.options.scid, owners: { people: { moniker: 'people', monikerXuid: xuid } } },
      contractVersion: '107'
    })
    return response.results
  }

  getSession (name, options = {}) {
    return this.request('GET', this._sessionUrl(name), { ...options, contractVersion: '107' })
  }

  updateSession (name, payload, options = {}) {
    return this.request('PUT', this._sessionUrl(name), { ...options, data: payload, contractVersion: '107' })
  }

  setActivity (name, options = {}) {
    return this._sendHandle({ version: 1, type: 'activity', sessionRef: this._sessionRef(name) }, options)
  }

  sendInvite (name, xuid, options = {}) {
    if (!this.options.titleId) throw new TypeError('Xbox invitations require titleId')
    return this._sendHandle({
      version: 1, type: 'invite', sessionRef: this._sessionRef(name), invitedXuid: xuid, inviteAttributes: { titleId: this.options.titleId }
    }, options)
  }

  _sendHandle (data, options) {
    return this.request('POST', 'https://sessiondirectory.xboxlive.com/handles', { ...options, data, contractVersion: '107' })
  }

  createSession (options = {}) {
    if (options.name !== undefined && (typeof options.name !== 'string' || !options.name)) throw new TypeError('Session name is required')
    return XboxSession.open(this, null, options)
  }

  joinSession (name, options = {}) {
    if (typeof name !== 'string' || !name) throw new TypeError('Session name is required')
    return XboxSession.open(this, name, options)
  }
}
module.exports = { XboxClient }
