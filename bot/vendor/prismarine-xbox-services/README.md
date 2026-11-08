# prismarine-xbox-services

[![Build Status](https://github.com/PrismarineJS/prismarine-xbox-services/actions/workflows/ci.yml/badge.svg)](https://github.com/PrismarineJS/prismarine-xbox-services/actions/workflows/ci.yml)

Xbox and PlayFab services API for Node.js.

## Usage

Node.js 24 or newer is required. The API is experimental; coordinate version updates with consumers.

```js
const { XboxClient } = require('prismarine-xbox-services')
const { Authflow } = require('prismarine-auth')
const auth = new Authflow(username, cacheDirectory, authOptions)
const xbox = new XboxClient(auth)
const profile = await xbox.getProfile('me')
```

Supply your existing Authflow; sign-in, token acquisition, caching and refresh stay in
[prismarine-auth](https://github.com/PrismarineJS/prismarine-auth). The package has no runtime
dependency on prismarine-auth and accepts compatible credential providers.

- [XboxClient and XboxSession](docs/xbox.md): profiles, multiplayer sessions, invitations,
  and activity publishing with caller-provided title configuration.
- [XboxRTASocket](docs/rta.md): Real Time Activity connections, subscriptions and lifecycle management.
- [PlayFabClient](docs/playfab.md): isolated authenticated PlayFab requests with caller-provided
  credentials and title ID.

Minecraft title defaults, world metadata and game protocol behavior belong to consumers.
The focus is services needed by Minecraft clients and bots, with reusable configuration for
other applications. See the [migration guide](docs/migration.md), [architecture proposal](https://github.com/PrismarineJS/prismarine-auth/issues/185)
and [Xbox RTA reference implementation](https://github.com/LucienHH/xbox-rta).

Tests cover mocked service boundaries and local WebSocket connections. Live authenticated
Xbox and PlayFab integration still needs verification with title-specific credentials.

## Development

Use Node.js 24 or newer.

```sh
git clone https://github.com/PrismarineJS/prismarine-xbox-services.git
cd prismarine-xbox-services
npm install
npm test
```

`npm test` runs lint and tests. Use `npm run fix` to apply JavaScript Standard Style fixes.
See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution and release notes.

## License

[MIT](LICENSE).
