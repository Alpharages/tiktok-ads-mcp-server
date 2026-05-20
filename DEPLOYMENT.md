# Deployment — Single-user Docker container on a VPS (over SSH)

This server uses **stdio transport**: it talks JSON-RPC over stdin/stdout and is
launched as a subprocess by the MCP client. It does **not** listen on a network
port. So "hosting it on a VPS" means: build the image on the VPS, and have your
local MCP client launch it remotely with `ssh … docker run -i`. The stdio stream
is tunneled over SSH.

```
Claude Desktop (laptop)  ──ssh──▶  VPS: docker run -i tiktok-ads-mcp
        stdin/stdout  ◀────────────────  stdin/stdout (JSON-RPC)
```

## Prerequisites

- A VPS with Docker installed and your SSH key authorized (key-based login, no
  password prompt — the client launches this non-interactively).
- TikTok `APP_ID` / `APP_SECRET` from the [TikTok For Business Developer Portal](https://business-api.tiktok.com/portal/).
- **OAuth redirect URI:** the auth flow redirects to `https://adsmcp.com` by
  default (`oauth_simple.py`). That URL must be registered as a redirect URI in
  your TikTok app, since that's where you'll read the `code` from after
  authorizing. (No inbound callback port is needed on the VPS — the code is
  exchanged server-side over HTTPS.)

## 1. Get the image onto the VPS

**Option A — build on the VPS (recommended):**

```bash
ssh user@your-vps
git clone git@github.com:Alpharages/tiktok-ads-mcp-server.git
cd tiktok-ads-mcp-server
docker build -t tiktok-ads-mcp:latest .
```

**Option B — build locally, ship the image (no git on the VPS):**

```bash
# on your laptop
docker build -t tiktok-ads-mcp:latest .
docker save tiktok-ads-mcp:latest | gzip | ssh user@your-vps 'gunzip | docker load'
```

## 2. Configure secrets + token storage on the VPS

```bash
ssh user@your-vps
mkdir -p ~/tiktok-ads-mcp/tokens

# Secrets (NOT baked into the image)
cat > ~/tiktok-ads-mcp/.env <<'EOF'
TIKTOK_APP_ID=your_app_id
TIKTOK_APP_SECRET=your_app_secret
LOG_LEVEL=INFO
EOF
chmod 600 ~/tiktok-ads-mcp/.env

# The container runs as uid 10001; make the token dir writable by it so OAuth
# tokens persist across runs (each session starts a fresh --rm container).
sudo chown -R 10001:10001 ~/tiktok-ads-mcp/tokens
```

## 3. Point your MCP client at it

In your local MCP client config (e.g. Claude Desktop
`claude_desktop_config.json`), launch the remote container over SSH. Replace
`user@your-vps` and the absolute paths with your own:

```json
{
  "mcpServers": {
    "tiktok-ads": {
      "command": "ssh",
      "args": [
        "-T", "user@your-vps",
        "docker", "run", "-i", "--rm",
        "--env-file", "/home/user/tiktok-ads-mcp/.env",
        "-v", "/home/user/tiktok-ads-mcp/tokens:/home/app/.tiktok_ads_mcp",
        "tiktok-ads-mcp:latest"
      ]
    }
  }
}
```

Notes:
- `ssh -T` disables TTY allocation — MCP needs a raw stdio stream, not a terminal.
- `docker run -i` (interactive stdin), `--rm` (clean up each session). Never `-d`.
- `--env-file` injects the TikTok credentials at runtime.
- `-v …:/home/app/.tiktok_ads_mcp` persists OAuth tokens between sessions.

## 4. First-time authentication

Once the client is connected, in a conversation:

1. Call **`tiktok_ads_login`**. On a headless server no browser opens, so the
   tool returns an `auth_url` in its response.
2. Open that `auth_url` in your local browser and authorize. You'll be
   redirected to `https://adsmcp.com/...?code=XXXXX`.
3. Copy the `code` value and call **`tiktok_ads_complete_auth`** with it.
4. Tokens are saved to the mounted volume; subsequent sessions auto-authenticate.

Verify any time with **`tiktok_ads_auth_status`**.

## 5. Updating

```bash
ssh user@your-vps 'cd tiktok-ads-mcp-server && git pull && docker build -t tiktok-ads-mcp:latest .'
```
The token volume is external to the image, so updates don't log you out.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Client shows "server exited" instantly | SSH key not set up, or `docker` not on the remote `$PATH` for non-login shells. Test: `ssh -T user@your-vps docker run -i --rm tiktok-ads-mcp:latest` and type an `initialize` JSON line. |
| "Missing TikTok API credentials" | `--env-file` path wrong or `.env` missing `TIKTOK_APP_ID`/`TIKTOK_APP_SECRET`. |
| Re-prompted to log in every session | Token volume not mounted, or the host `tokens/` dir isn't owned by uid `10001`. |
| `permission denied` writing tokens | `sudo chown -R 10001:10001 ~/tiktok-ads-mcp/tokens`. |
