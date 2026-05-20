"""Streamable HTTP entrypoint for remote hosting of the TikTok Ads MCP server.

The stdio entrypoint (run_server.py / server.main) is for local, single-process
use where the MCP client spawns the process. This module instead exposes the
same `Server` over Streamable HTTP so remote MCP clients can connect to a URL,
authenticated with a shared bearer token. TikTok auth state is process-global
(single advertiser account shared by all connected clients).
"""

import contextlib
import logging
import os
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .server import app, tiktok_server

logger = logging.getLogger(__name__)


class BearerAuthMiddleware:
    """Pure-ASGI bearer-token gate.

    Implemented as raw ASGI (not BaseHTTPMiddleware) so it does not buffer the
    Streamable HTTP / SSE response stream. Paths in `public_paths` skip auth.
    """

    def __init__(self, app: ASGIApp, token: str, public_paths: tuple[str, ...] = ("/healthz",)):
        self.app = app
        self._token = token
        self._public_paths = public_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._public_paths:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        if not auth.startswith("Bearer ") or auth[len("Bearer "):] != self._token:
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return

        await self.app(scope, receive, send)


def build_app() -> Starlette:
    token = os.getenv("MCP_AUTH_TOKEN")
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN must be set to run the HTTP server.")

    session_manager = StreamableHTTPSessionManager(
        app=app,
        event_store=None,
        json_response=False,
        stateless=False,
    )

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def health(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        # Load TikTok credentials and warm up auth from any persisted tokens so
        # a restart doesn't force a re-login.
        await tiktok_server.initialize()
        try:
            await tiktok_server.get_auth_status()
        except Exception as e:  # non-fatal: clients can still call tiktok_ads_login
            logger.warning(f"Could not warm up saved auth on startup: {e}")
        async with session_manager.run():
            logger.info("TikTok Ads MCP HTTP server ready (Streamable HTTP at /mcp)")
            yield

    return Starlette(
        debug=False,
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Mount("/mcp", app=handle_mcp),
        ],
        middleware=[Middleware(BearerAuthMiddleware, token=token)],
        lifespan=lifespan,
    )


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    # Trust X-Forwarded-* from the front proxy (nginx terminates TLS). Without
    # this, uvicorn treats requests as http and emits http:// redirect targets
    # (e.g. /mcp -> /mcp/), causing clients to drop the Authorization header on
    # the scheme downgrade. The container is localhost-bound, so only the proxy
    # can reach it — trusting all forwarded IPs is safe here.
    uvicorn.run(
        build_app(),
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "*"),
    )


if __name__ == "__main__":
    main()
