# Clash API extensions

`experimental.clash_api.external_controller` starts the Clash-compatible REST
API. In addition to the documented proxy and controller routes, tachyon-core
provides an authenticated endpoint for LuCI integrations to convert NaiveProxy
share links into outbound configuration.

## `POST /parse`

Send a JSON body containing the share link:

```http
POST /parse HTTP/1.1
Content-Type: application/json
Authorization: Bearer <secret>

{"url":"naive+https://user:password@proxy.example:443?padding=true#My%20proxy"}
```

The endpoint returns a single outbound object that can be added to the
configuration's `outbounds` array:

```json
{
  "type": "naive",
  "tag": "My proxy",
  "server": "proxy.example",
  "server_port": 443,
  "username": "user",
  "password": "password",
  "tls": {
    "enabled": true
  }
}
```

The `naive+https://` scheme is required. The server port defaults to `443`;
bracketed IPv6 addresses, percent-encoded credentials, and a percent-encoded
fragment for the outbound tag are supported. The optional query may be
`padding=true`; NaiveProxy padding is already implemented by the runtime. A
missing fragment uses the server address as the tag. Invalid links return
`400 Bad Request` with a JSON `message`.

The resulting outbound appears in `GET /proxies` as type `NaiveProxy`.
