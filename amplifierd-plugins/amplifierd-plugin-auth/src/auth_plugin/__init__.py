"""PAM authentication plugin for amplifierd.

Provides Linux PAM-based login with session cookie management.
Activates only when: platform is Linux, TLS is active, and auth is enabled
in the daemon settings. On other platforms or when inactive, registers only
a stub ``/auth/me`` endpoint (returns 401) so the distro-plugin's
auth-widget probe gets a clean response instead of a 404.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.responses import Response

logger = logging.getLogger(__name__)


def _stub_router() -> APIRouter:
    """Return a router with a stub /auth/me that always returns 401.

    When the auth plugin is inactive the full route set is not registered,
    but the distro-plugin's auth-widget.js still calls ``GET /auth/me`` on
    every page load.  Without this stub FastAPI has no matching route and
    returns 404, which clutters the server log on every request.  A 401 is
    the correct semantic response ("not authenticated") and the widget
    already treats any non-200 as "auth not enabled — render nothing".
    """
    router = APIRouter()

    @router.get("/auth/me", response_model=None)
    async def auth_me_stub() -> Response:
        return JSONResponse(
            status_code=401, content={"error": "Authentication is not enabled"}
        )

    return router


def create_router(state: Any) -> APIRouter:
    """amplifierd plugin entry point.

    Returns an APIRouter with /login, /logout, /auth/me routes when
    PAM auth is applicable.  Returns a stub router with only /auth/me
    (returning 401) otherwise, so the auth-widget's probe request gets
    a clean response instead of a 404.
    """
    router = APIRouter()

    settings = getattr(state, "settings", None)
    if settings is None:
        return _stub_router()

    # PAM auth only on Linux with auth enabled
    auth_enabled = getattr(settings, "auth_enabled", False)
    if sys.platform != "linux" or not auth_enabled:
        logger.debug(
            "Auth plugin inactive: platform=%s, auth_enabled=%s",
            sys.platform,
            auth_enabled,
        )
        return _stub_router()

    # Import heavy deps only when actually activating
    from auth_plugin.pam import get_or_create_secret, verify_session_token
    from auth_plugin.routes import create_auth_router

    secret = get_or_create_secret()

    # Expose a verify callable so SessionAuthMiddleware (in amplifierd) can
    # validate session cookies without importing from the auth plugin directly.
    # The middleware reads this from app.state at dispatch time.
    state.auth_verify_session = lambda token: verify_session_token(token, secret)

    auth_router = create_auth_router(secret)
    router.include_router(auth_router)

    logger.info("PAM authentication plugin active")
    return router
