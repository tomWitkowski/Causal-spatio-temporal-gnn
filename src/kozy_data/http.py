"""Shared HTTP session with retry/backoff and a tiny on-disk cache."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import CACHE_DIR

log = logging.getLogger("kozy_data.http")

USER_AGENT = (
    "kozy-data/0.1 (+https://github.com/tomWitkowski/Causal-spatio-temporal-gnn; "
    "spatio-temporal GNN research)"
)


def make_session(total_retries: int = 5, backoff: float = 1.0) -> requests.Session:
    """Session with retry on transient errors and a polite User-Agent."""
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


_SESSION: requests.Session | None = None


def session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = make_session()
    return _SESSION


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"{digest}.cache"


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 60,
    use_cache: bool = True,
    rate_limit: float = 0.0,
    **kwargs: Any,
) -> requests.Response:
    """GET with optional file cache keyed by url+params and a polite delay."""
    cache_key = url + "?" + json.dumps(params or {}, sort_keys=True)
    cache_file = _cache_path(cache_key)
    if use_cache and cache_file.exists():
        log.debug("cache hit: %s", url)
        resp = requests.models.Response()
        resp.status_code = 200
        resp._content = cache_file.read_bytes()
        resp.url = url
        return resp

    if rate_limit:
        time.sleep(rate_limit)
    log.info("GET %s params=%s", url, params)
    resp = session().get(url, params=params, timeout=timeout, **kwargs)
    resp.raise_for_status()
    if use_cache:
        cache_file.write_bytes(resp.content)
    return resp


def get_json(url: str, **kwargs: Any) -> Any:
    return get(url, **kwargs).json()


def post(url: str, *, data: Any = None, timeout: int = 120, **kwargs: Any) -> requests.Response:
    log.info("POST %s", url)
    resp = session().post(url, data=data, timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp
