"""Authentication for the Origin Energy (Kraken) integration.

Origin's consumer portal logs in via an Auth0-hosted Universal Login flow.
The interactive login endpoint (POST /usernamepassword/login) is guarded
by Kasada bot detection, so this integration deliberately never attempts
to submit a username/password itself.

Instead, the user supplies the long-lived `auth0` session cookie captured
from their own browser after a normal, human login. This module then uses
Auth0's silent-authentication support (`prompt=none` on the /authorize
endpoint) to mint fresh short-lived access tokens from that cookie,
indefinitely - without ever touching the Kasada-guarded login form.

When the underlying session cookie eventually expires or is invalidated
(logout elsewhere, password change, Origin shortens session lifetime,
etc.), silent refresh will start failing. At that point OriginSessionExpired
is raised, which the integration should turn into a Home Assistant reauth
flow asking the user to log in again in a real browser and paste a fresh
cookie value.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

import aiohttp

from .const import (
    AUTH0_AUDIENCE,
    AUTH0_CLIENT_ID,
    AUTH0_DOMAIN,
    AUTH0_REDIRECT_URI,
    AUTH0_SCOPE,
)

_LOGGER = logging.getLogger(__name__)

AUTHORIZE_URL = f"https://{AUTH0_DOMAIN}/authorize"

# Refresh this many seconds before actual expiry, to avoid a request
# racing the token's expiry mid-flight.
_REFRESH_MARGIN_SECONDS = 300


class OriginAuthError(Exception):
    """Base class for authentication failures."""


class OriginSessionExpired(OriginAuthError):
    """The stored auth0 session cookie is no longer sufficient.

    Raised when a silent-auth attempt does not land on the callback URL
    with a token (e.g. it lands back on the login page instead). This
    means the user needs to log in again in a real browser and supply a
    fresh cookie value - it does NOT mean "wait and retry".
    """


@dataclass
class _TokenSet:
    access_token: str
    id_token: str | None
    expires_at: float  # unix timestamp

    @property
    def expired(self) -> bool:
        return time.time() >= (self.expires_at - _REFRESH_MARGIN_SECONDS)


class OriginEnergyAuth:
    """Mints fresh Origin/Auth0 access tokens via silent re-authentication."""

    def __init__(self, session: aiohttp.ClientSession, auth0_cookie: str) -> None:
        self._session = session
        self._auth0_cookie = auth0_cookie
        self._tokens: _TokenSet | None = None

    @property
    def auth0_cookie(self) -> str:
        """The auth0 session cookie currently in use.

        Origin has been observed rotating this value server-side during
        the auth flow, so this can change after construction (see
        _maybe_update_cookie_from_response). The config entry should be
        updated with this value after any refresh, so a rotation isn't
        lost on Home Assistant restart.
        """
        return self._auth0_cookie

    async def async_get_access_token(self) -> str:
        """Return a currently-valid access token, refreshing if needed."""
        if self._tokens is None or self._tokens.expired:
            await self._async_silent_refresh()
        assert self._tokens is not None  # noqa: S101 - set by the refresh above
        return self._tokens.access_token

    def _maybe_update_cookie_from_response(self, resp: aiohttp.ClientResponse) -> None:
        """Pick up a rotated auth0 cookie if the server issued one.

        Origin/Auth0 has been observed rotating the auth0 session cookie
        to a new value partway through the login/silent-auth flow (the
        value set at /login/callback is not always the one that ends up
        being the "live" one). If a refresh response carries a new
        Set-Cookie: auth0=..., that's the value to keep using going
        forward - not the one the caller originally supplied.
        """
        for raw in resp.headers.getall("Set-Cookie", []):
            # e.g. "auth0=s%3Axxx.yyy; Path=/; Expires=...; HttpOnly; ..."
            first_pair = raw.split(";", 1)[0].strip()
            name, _, value = first_pair.partition("=")
            if name == "auth0" and value:
                if value != self._auth0_cookie:
                    _LOGGER.debug("Origin Energy auth0 cookie rotated by server")
                self._auth0_cookie = value

    async def _async_silent_refresh(self) -> None:
        params = {
            "client_id": AUTH0_CLIENT_ID,
            "response_type": "token id_token",
            "redirect_uri": AUTH0_REDIRECT_URI,
            "scope": AUTH0_SCOPE,
            "audience": AUTH0_AUDIENCE,
            "prompt": "none",
            "state": secrets.token_urlsafe(18),
            "nonce": secrets.token_urlsafe(18),
        }
        # id.originenergy.com.au sits behind a CloudFront/WAF layer that
        # 403s requests that don't look like they came from a browser -
        # aiohttp's default headers (generic User-Agent, no Accept, etc.)
        # get blocked outright before Auth0's app logic even runs. These
        # headers were copied from a real Chrome request that succeeded;
        # they are NOT secrets, unlike the cookie.
        headers = {
            "Cookie": f"auth0={self._auth0_cookie}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.originenergy.com.au/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }

        async with self._session.get(
            AUTHORIZE_URL,
            params=params,
            headers=headers,
            allow_redirects=False,
        ) as resp:
            status = resp.status
            location = resp.headers.get("Location", "")
            self._maybe_update_cookie_from_response(resp)

        if "auth/callback#" not in location:
            # Landed on /login, an error page, or got blocked outright
            # (e.g. a 403 from the CloudFront/WAF layer in front of this
            # domain) - the stored cookie is either no longer good, or
            # the request itself didn't look enough like a browser to be
            # let through. status/location are logged to help tell the
            # two apart.
            _LOGGER.info(
                "Origin Energy silent re-authentication failed "
                "(HTTP %s, redirected to %s); the stored session cookie "
                "may have expired, or the request was blocked before "
                "reaching Auth0.",
                status,
                location or "<no Location header>",
            )
            raise OriginSessionExpired(
                "Silent re-authentication failed - the stored Origin "
                "session cookie is no longer valid. Log in again in a "
                "browser and provide a fresh cookie value."
            )

        fragment = urlparse(location).fragment
        parsed = dict(parse_qsl(fragment))

        access_token = parsed.get("access_token")
        expires_in = parsed.get("expires_in")
        if not access_token or not expires_in:
            raise OriginAuthError(
                "Silent auth response did not include the expected "
                f"token fields: got keys {sorted(parsed)}"
            )

        self._tokens = _TokenSet(
            access_token=access_token,
            id_token=parsed.get("id_token"),
            expires_at=time.time() + int(expires_in),
        )
        _LOGGER.debug(
            "Refreshed Origin Energy access token (expires in %ss)", expires_in
        )
