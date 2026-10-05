"""Constants for the Origin Energy (Kraken) integration.

Everything here was reverse-engineered from a captured browser session
(a HAR file of the consumer web portal). None of it is documented or
supported by Origin Energy, and it may change or break without notice.
"""

DOMAIN = "origin_energy"

# --- Auth0 (id.originenergy.com.au) ---
# These are the same client_id/scope/audience the consumer web app itself
# uses. They are not secrets - they are visible in every browser request
# the web app makes - but the resulting tokens/cookies ARE secrets.
AUTH0_DOMAIN = "id.originenergy.com.au"
AUTH0_CLIENT_ID = "yOHRT97N3yH85jzTDlqN2A7Cf2D0cmQe"
AUTH0_REDIRECT_URI = "https://www.originenergy.com.au/auth/callback"
AUTH0_SCOPE = "openid email read:api all"
AUTH0_AUDIENCE = "https://digitalapi"

# --- Data API (api.rx.originenergy.com.au) ---
API_BASE = "https://api.rx.originenergy.com.au"
GRAPHQL_URL = f"{API_BASE}/v1/gateway/graphql"

# --- EV iCharge / EV Power Up (same-origin on www.originenergy.com.au,
# NOT the api.rx.* gateway - same Bearer token though) ---
EV_ICHARGE_BASE = "https://www.originenergy.com.au/ev-icharge/api/v2"

# --- Config entry keys ---
CONF_AUTH0_COOKIE = "auth0_cookie"
CONF_ORIGIN_ACCOUNT_ID = "origin_account_id"

DEFAULT_SCAN_INTERVAL_MINUTES = 15
