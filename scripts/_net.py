"""One place that decides how this study talks to the network.

Two facts make this necessary, both discovered the hard way:

* The host exports ``http_proxy=http://127.0.0.1:3768`` (its own egress). Yahoo
  Finance answers that route with HTTP 429, so every process here has to point
  somewhere else. ``load_dotenv`` will not override an already-exported
  variable, so the override has to be done in-process.
* The local Clash client does not keep a stable HTTP port across restarts (it has
  been seen on 4780 and on 7890). Hard-coding either one breaks silently, so the
  port is probed.

``RESEARCH_PROXY`` short-circuits the probe when you know the answer.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

CANDIDATE_PROXIES = (
    "http://127.0.0.1:7890",
    "http://127.0.0.1:4780",
)


def _port_open(url: str, timeout: float = 0.6) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_proxy() -> str:
    explicit = os.getenv("RESEARCH_PROXY")
    if explicit:
        return explicit
    for candidate in CANDIDATE_PROXIES:
        if _port_open(candidate):
            return candidate
    return CANDIDATE_PROXIES[0]


def apply_proxy(proxy: str | None = None) -> str:
    """Export the proxy for urllib/requests and drop the lowercase duplicates.

    Both cases are cleared first: on Windows the lowercase ``http_proxy`` can
    shadow the uppercase one depending on the library.
    """
    proxy = proxy or resolve_proxy()
    for name in ("http_proxy", "https_proxy", "all_proxy",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(name, None)
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = proxy
    return proxy


def configure_yfinance(proxy: str | None = None):
    """yfinance builds its own curl_cffi session and ignores HTTP(S)_PROXY.

    Without this line every Yahoo call is answered with 429 even though the
    proxy is reachable, and the market analyst silently receives
    ``DATA_UNAVAILABLE``.
    """
    import yfinance as yf

    yf.config.network.proxy = proxy or resolve_proxy()
    return yf
