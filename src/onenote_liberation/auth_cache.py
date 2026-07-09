"""Persistent Microsoft authentication for OneNote Liberation."""

from __future__ import annotations

import json
import pathlib
import stat

import msal

from . import main as legacy


CACHE_DIR = pathlib.Path.home() / ".onenote-liberation"
CACHE_FILE = CACHE_DIR / "msal_token_cache.json"


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        cache.deserialize(CACHE_FILE.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CACHE_FILE.write_text(cache.serialize(), encoding="utf-8")
    try:
        CACHE_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def clear_token_cache() -> None:
    try:
        CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def sign_in(reset_auth: bool = False) -> str:
    """Return an access token, reusing a persistent MSAL cache when possible.

    Do not validate the token by shape. Microsoft/MSAL may return tokens that
    are opaque to clients. The authoritative validation is whether Graph accepts
    the token.
    """
    if reset_auth:
        clear_token_cache()

    cache = _load_cache()
    app = msal.PublicClientApplication(
        legacy.CLIENT_ID,
        authority=legacy.AUTHORITY,
        token_cache=cache,
    )

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(legacy.SCOPES, account=accounts[0])

    if not result or "access_token" not in result:
        flow = app.initiate_device_flow(scopes=legacy.SCOPES)
        if "user_code" not in flow:
            print("\nCould not create device login flow. Microsoft returned:")
            print(json.dumps(flow, indent=2))
            raise RuntimeError("Could not create device login flow.")

        print("\nMicrosoft sign-in required:\n")
        print(flow["message"])
        print("\nSign in using the Microsoft account that owns the OneNote notebook.\n")

        result = app.acquire_token_by_device_flow(flow)

    _save_cache(cache)

    if "access_token" not in result:
        print("\nLogin failed. Microsoft returned:")
        print(json.dumps(result, indent=2))
        raise RuntimeError("Login failed.")

    return result["access_token"]
