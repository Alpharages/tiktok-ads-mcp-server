# Deployment — Remote Streamable HTTP MCP server on a VPS

This server is hosted as a **remote Streamable HTTP** MCP server: it listens on a
network port and speaks MCP over HTTP, so developers connect to a **URL** with a
shared bearer token. They never need SSH or a copy of your TikTok credentials —
the secrets stay on the server.

```
Developer's MCP client ──HTTPS──▶ nginx (TLS, :443) ──▶ 127.0.0.1:8000  (container)
   Authorization: Bearer <token>        proxy_pass            Starlette + StreamableHTTP
```

The container runs `python -m tiktok_ads_mcp.http_server` (this is the default
Docker `CMD`). TikTok auth state is **process-global** — one advertiser account
is shared by every connected client. (The older stdio transport is still
available for purely local use; see [Local stdio use](#appendix-local-stdio-use).)

## Prerequisites

- A VPS with **Docker + Docker Compose** installed.
- A **domain/subdomain** pointing at the VPS (an `A` record), e.g.
  `tiktok-ads-mcp.example.com`. Required for Let's Encrypt TLS.
- A reverse proxy on `:80/:443`. This guide uses **nginx + certbot**; any
  TLS-terminating proxy works as long as it is configured for SSE (below).
- TikTok `APP_ID` / `APP_SECRET` from the
  [TikTok For Business Developer Portal](https://business-api.tiktok.com/portal/).
- An **OAuth redirect URI** you control, registered in your TikTok app. You set
  the same value in `TIKTOK_REDIRECT_URI`; it's where you read the `code` from
  after authorizing. (No inbound callback port is needed — the code is exchanged
  server-side over HTTPS.)

## 1. Get the code onto the VPS and configure secrets

```bash
ssh user@your-vps
git clone git@github.com:Alpharages/tiktok-ads-mcp-server.git
cd tiktok-ads-mcp-server

cp .env.example .env
chmod 600 .env
```

Edit `.env` and fill in:

```dotenv
TIKTOK_APP_ID=your_app_id
TIKTOK_APP_SECRET=your_app_secret
# Must exactly match a redirect URI registered in your TikTok app:
TIKTOK_REDIRECT_URI=https://your-domain.example/oauth/callback

# Shared bearer token every MCP client must send. Generate a strong value:
#   openssl rand -hex 32
MCP_AUTH_TOKEN=paste_a_long_random_token_here

HOST=0.0.0.0
PORT=8000
```

`MCP_AUTH_TOKEN` is **required** — the HTTP server refuses to start without it.

## 2. Build and run the container

```bash
docker compose up -d --build
docker compose logs -f          # expect: "TikTok Ads MCP HTTP server ready"
```

`docker-compose.yml` binds the container to **`127.0.0.1:8000`** only — it is
never exposed directly to the internet. nginx (next step) is the public entry
point. OAuth tokens persist in the `./tokens` volume across restarts.

Verify the container is up (loopback, no token required for health):

```bash
curl -s http://127.0.0.1:8000/healthz   # -> ok
```

## 3. nginx reverse proxy (SSE-safe)

Streamable HTTP can stream responses as Server-Sent Events, so the proxy **must
not buffer** and **must** speak HTTP/1.1. Create
`/etc/nginx/sites-available/tiktok-ads-mcp.example.com`:

```nginx
server {
    listen 80;
    server_name tiktok-ads-mcp.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;          # required for streaming
        proxy_set_header Connection "";  # keep upstream connection alive
        proxy_buffering off;             # do not buffer the SSE stream
        proxy_read_timeout 3600s;        # long-lived MCP sessions

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # so the app knows it's HTTPS
    }
}
```

```bash
ln -s /etc/nginx/sites-available/tiktok-ads-mcp.example.com \
      /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

> **Why `X-Forwarded-Proto` matters:** nginx terminates TLS and forwards plain
> HTTP to the container. The app trusts these headers (`uvicorn(proxy_headers=
> True, forwarded_allow_ips="*")`, already set in `http_server.py`). Without it,
> uvicorn would emit `http://` redirect targets for `/mcp` → `/mcp/`, and MCP
> clients drop the `Authorization` header on the scheme downgrade — surfacing as
> a confusing `401`.

## 4. Enable TLS with certbot

```bash
certbot --nginx -d tiktok-ads-mcp.example.com
```

certbot adds the `:443` server block and an HTTP→HTTPS redirect, and sets up
auto-renewal. Your endpoint is now:

```
https://tiktok-ads-mcp.example.com/mcp
```

## 5. Point an MCP client at it

Developers add this to their MCP client config — **no SSH, no credentials**,
just the URL and the shared token:

```json
{
  "mcpServers": {
    "tiktok-ads": {
      "type": "http",
      "url": "https://tiktok-ads-mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer paste_the_MCP_AUTH_TOKEN_here"
      }
    }
  }
}
```

Quick smoke test from anywhere:

```bash
# Missing/wrong token -> 401
curl -s -o /dev/null -w '%{http_code}\n' https://tiktok-ads-mcp.example.com/mcp
# Health endpoint is public -> ok
curl -s https://tiktok-ads-mcp.example.com/healthz
```

## 6. First-time TikTok authentication (once)

Auth is server-side and shared by all clients, so this is done **once**:

1. Call **`tiktok_ads_login`**. The server is headless, so no browser opens —
   the tool returns an `auth_url` in its response.
2. Open that `auth_url` in a browser and authorize. You'll be redirected to your
   configured `TIKTOK_REDIRECT_URI` with a `?code=…` query parameter.
3. Copy the `code` value and call **`tiktok_ads_complete_auth`** with it.
4. Tokens are saved to the `./tokens` volume; restarts auto-authenticate.

Check status any time with **`tiktok_ads_auth_status`**.

## 7. Updating

```bash
ssh user@your-vps 'cd tiktok-ads-mcp-server && git pull && docker compose up -d --build'
```

The `./tokens` volume is external to the image, so updates don't log you out.

## 8. Rotating the shared token

Editing `.env` does **not** hot-reload a running container — you must recreate it:

```bash
# edit MCP_AUTH_TOKEN in .env, then:
docker compose up -d --force-recreate
```

Then redistribute the new token to your developers.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 unauthorized` from `/mcp` | Token mismatch. The client's `Authorization: Bearer` value must equal `.env`'s `MCP_AUTH_TOKEN`. If you just changed `.env`, recreate the container (`docker compose up -d --force-recreate`) — it doesn't hot-reload. |
| `401` even with the right token | Proxy not forwarding `X-Forwarded-Proto`, causing an `http://` redirect on `/mcp`→`/mcp/` that drops the auth header. Confirm the nginx `proxy_set_header X-Forwarded-Proto $scheme;` line (step 3). |
| Responses hang / never stream | nginx buffering the SSE stream. Ensure `proxy_buffering off`, `proxy_http_version 1.1`, and `proxy_set_header Connection "";`. |
| Container won't start: "MCP_AUTH_TOKEN must be set" | `MCP_AUTH_TOKEN` empty/missing in `.env`. |
| "Missing TikTok API credentials" | `.env` missing `TIKTOK_APP_ID`/`TIKTOK_APP_SECRET`. |
| "Missing TIKTOK_REDIRECT_URI" on login | Set `TIKTOK_REDIRECT_URI` in `.env` to the URI registered in your TikTok app, then recreate the container. |
| Re-prompted to log in after a rebuild | The `./tokens` volume isn't mounted or isn't writable by uid `10001`: `sudo chown -R 10001:10001 tokens`. |

## Appendix: Local stdio use

For a single local user whose MCP client launches the process directly (no
hosting, no token), override the container command to use stdio transport:

```bash
docker run -i --rm --env-file .env \
  -v "$PWD/tokens:/home/app/.tiktok_ads_mcp" \
  tiktok-ads-mcp:latest python run_server.py
```

In this mode `MCP_AUTH_TOKEN`, `HOST`, and `PORT` are unused, and the MCP client
config points at the `docker run … python run_server.py` command instead of a URL.
