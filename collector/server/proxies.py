"""
Route all Garmin traffic through a rotating pool of SOCKS5 proxies, with
automatic failover when a proxy is down.

Why: Garmin rate-limits by source IP. On a shared host every participant logs in
and downloads from the SAME outbound IP, so a handful of people can trip Garmin's
429s for everyone. Sending each session through a different proxy spreads the
traffic across many IPs.

Where it hooks in (one place only): garminconnect's ``Garmin`` owns a
``garth.Client``, which owns a single ``requests.Session`` that is reused for
BOTH the SSO login and every data-API call, and every call funnels through
``garth.Client.request``. So we only ever touch two things:

  * ``apply_to(api)``      — point the session at the next proxy in the pool.
  * ``install_request_retry(api)`` — wrap ``Client.request`` so each data-API
    call retries on a dead proxy, rotating to the next one.

Failover strategy:
  * Login / MFA resume — retry the WHOLE flow on a fresh proxy (see
    ``with_proxy_retry``). Rotating between the sub-requests of one SSO flow
    would change the egress IP mid-handshake and Garmin rejects that.
  * Download — many independent, idempotent calls, so per-call rotation via the
    wrapped ``request`` is fine and recovers from a proxy dying mid-download.

Each new session rotates to the next proxy, and keeps it (login + MFA + download)
unless a proxy dies, in which case it rotates again.

Configuration (env vars):
  GARMIN_USE_PROXY         "1" (default) routes through a proxy; "0" = direct.
  USE_FREE_PROXIES         "1" = free no-auth test pool; "0" (default) = NordVPN
                           (needs NORDVPN_USER / NORDVPN_PASSWORD).
  NORDVPN_USER             NordVPN *service* credentials (not your account login).
  NORDVPN_PASSWORD
  GARMIN_PROXY_MAX_RETRIES how many times to rotate-and-retry on a proxy failure
                           before giving up (default 3, i.e. up to 4 attempts).
  GARMIN_PROXY_LOG         log level for this module (default INFO).

Edit the proxy host lists in NORDVPN_PROXIES / FREE_PROXIES below.

Note: SOCKS5 support needs PySocks; that's why requirements pins requests[socks].
"""

import logging
import os
import sys
import threading
from itertools import cycle
from urllib.parse import quote

from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    ConnectTimeout,
    ProxyError,
    ReadTimeout,
    Timeout,
)

NORDVPN_USER = os.environ.get("NORDVPN_USER", "")
NORDVPN_PASSWORD = os.environ.get("NORDVPN_PASSWORD", "")

# NordVPN SOCKS5 endpoints (require service credentials, listen on port 1080).
NORDVPN_PROXIES = [
    "nl.socks.nordhold.net:1080",
    "se.socks.nordhold.net:1080",
    "us.socks.nordhold.net:1080",
    "amsterdam.nl.socks.nordhold.net:1080",
    "atlanta.us.socks.nordhold.net:1080",
    "chicago.us.socks.nordhold.net:1080",
    "dallas.us.socks.nordhold.net:1080",
    "los-angeles.us.socks.nordhold.net:1080",
    "new-york.us.socks.nordhold.net:1080",
    "phoenix.us.socks.nordhold.net:1080",
    "san-francisco.us.socks.nordhold.net:1080",
    "stockholm.se.socks.nordhold.net:1080",
]

# Free public SOCKS5 proxies (no auth) for testing without NordVPN credentials.
# These are volatile and may stop working at any time — re-run a fresh fetch if
# they all fail. Last verified working 2026-06-25.
FREE_PROXIES = [
    "206.123.156.210:4679",
    "206.123.156.230:4750",
    "206.123.156.210:4458",
    "206.123.156.213:7689",
    "206.123.156.233:4534",
]

USE_PROXY = os.environ.get("GARMIN_USE_PROXY", "1") == "1"
USE_FREE_PROXIES = os.environ.get("USE_FREE_PROXIES", "0") == "1"
MAX_RETRIES = int(os.environ.get("GARMIN_PROXY_MAX_RETRIES", "3"))

if not USE_PROXY:
    PROXIES = []
elif USE_FREE_PROXIES:
    PROXIES = FREE_PROXIES
else:
    PROXIES = NORDVPN_PROXIES

# --- logging -------------------------------------------------------------
# Use a named logger so output is easy to filter. If nothing has configured
# logging yet (e.g. running this module standalone, not under uvicorn), attach a
# stderr handler so messages are still visible.
log = logging.getLogger("garmin.proxy")
log.setLevel(os.environ.get("GARMIN_PROXY_LOG", "INFO").upper())
if not logging.getLogger().handlers and not log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)


def _proxy_url(host_port: str) -> str:
    # Free proxies are no-auth; NordVPN needs the service credentials inlined.
    if USE_FREE_PROXIES:
        return f"socks5h://{host_port}"
    user = quote(NORDVPN_USER, safe="")
    password = quote(NORDVPN_PASSWORD, safe="")
    return f"socks5h://{user}:{password}@{host_port}"


# cycle() over an empty list raises StopIteration, so guard on PROXIES.
_pool = cycle(PROXIES) if PROXIES else None
_lock = threading.Lock()  # next() on a cycle isn't guaranteed atomic across threads

if PROXIES:
    log.info("proxy pool ready: %d %s proxies, up to %d retries on failure",
             len(PROXIES), "free" if USE_FREE_PROXIES else "NordVPN", MAX_RETRIES)
else:
    log.info("proxying disabled — Garmin traffic goes direct")


def _next():
    """Return (host_port, proxies_dict) for the next proxy, or (None, None)."""
    if not _pool:
        return None, None
    with _lock:
        host_port = next(_pool)
    url = _proxy_url(host_port)
    return host_port, {"http": url, "https": url}


def next_proxy_dict() -> dict | None:
    """Return the next proxy as a requests-style dict, or None if proxying is off."""
    return _next()[1]


def _brief(exc: Exception) -> str:
    """One-line, credential-scrubbed description of an exception for logging."""
    msg = f"{type(exc).__name__}: {exc}"
    for secret in (NORDVPN_PASSWORD, quote(NORDVPN_PASSWORD, safe="")):
        if secret:
            msg = msg.replace(secret, "***")
    return msg[:200]


def is_proxy_error(exc: Exception) -> bool:
    """True if the exception chain looks like a proxy / connection failure
    (proxy down, refused, timed out) rather than an HTTP status or auth error."""
    cur = exc
    while cur is not None:
        if isinstance(cur, (ProxyError, ConnectTimeout, ReadTimeout, Timeout,
                            RequestsConnectionError)):
            return True
        m = str(cur).lower()
        if any(s in m for s in ("proxy", "socks", "connection refused",
                                "connection reset", "max retries", "timed out",
                                "failed to establish", "unable to connect")):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def apply_to(api) -> dict | None:
    """Point a garminconnect ``Garmin``'s session at the next proxy in the pool.
    No-op (direct connection) if proxying is disabled. Returns the proxy dict."""
    host_port, proxies = _next()
    if proxies:
        api.garth.configure(proxies=proxies)
        log.info("session now using proxy %s", host_port)
    return proxies


def with_proxy_retry(api, func, *, retries: int | None = None):
    """Run ``func()`` (an operation on ``api``). If it fails because the proxy is
    down, rotate ``api`` to the next proxy and retry the WHOLE operation, up to
    ``retries`` times. Non-proxy errors (auth, rate-limit, ...) are re-raised
    immediately. No-op passthrough when proxying is disabled."""
    if not _pool:
        return func()
    retries = MAX_RETRIES if retries is None else retries
    attempt = 0
    while True:
        try:
            result = func()
            if attempt:
                log.info("succeeded after %d proxy rotation(s)", attempt)
            return result
        except Exception as exc:  # noqa: BLE001
            if not is_proxy_error(exc):
                raise
            if attempt >= retries:
                log.error("proxy failover exhausted after %d attempt(s); "
                          "last error: %s", attempt + 1, _brief(exc))
                raise
            attempt += 1
            log.warning("proxy down on attempt %d/%d (%s) — rotating to next proxy",
                        attempt, retries + 1, _brief(exc))
            apply_to(api)


def install_request_retry(api, *, retries: int | None = None) -> None:
    """Wrap the garth client's ``request`` so EVERY data-API call automatically
    retries on a dead proxy, rotating to the next one. Idempotent per client.

    Use this for the download phase (many independent calls). Do NOT install it
    before login: rotating between the sub-requests of one SSO flow changes the
    egress IP mid-handshake — wrap login with ``with_proxy_retry`` instead."""
    if not _pool:
        return
    client = api.garth
    if getattr(client, "_proxy_retry_installed", False):
        return
    original = client.request

    def request_with_retry(*args, **kwargs):
        return with_proxy_retry(api, lambda: original(*args, **kwargs), retries=retries)

    client.request = request_with_retry
    client._proxy_retry_installed = True
    log.info("per-request proxy failover installed for download phase")
