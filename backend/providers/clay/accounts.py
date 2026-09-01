"""Multi-account Clay support.

Function/routine ids are per-workspace -- switching Clay accounts means
switching BOTH the API key and every routine id at once, not just the
key. CLAY_ACTIVE_ACCOUNT selects which complete set to use.

"primary" (the default, used whenever CLAY_ACTIVE_ACCOUNT is unset)
reads the original bare env vars (CLAY_API_KEY, CLAY_ROUTINE_*, ...) so
existing .env files keep working with zero migration. Any other name
reads the same variable names prefixed with CLAY_ACCOUNT_{NAME}_, e.g.
CLAY_ACCOUNT_2_API_KEY, CLAY_ACCOUNT_2_ROUTINE_WORK_EMAIL.

To add a new account: add its CLAY_ACCOUNT_{NAME}_* block to .env
(api key now, routine ids once the functions exist in that workspace).
To actually start using it: set CLAY_ACTIVE_ACCOUNT={NAME}. Nothing
else in the codebase needs to change -- routines.py and engine.py both
resolve every Clay env var through active_account().env(...) rather
than os.environ.get(...) directly, so the switch is one line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ClayAccount:
    name: str
    api_key: Optional[str]
    _env_prefix: str  # "" for primary, "CLAY_ACCOUNT_{NAME}_" otherwise

    def env(self, suffix: str) -> Optional[str]:
        """Read a Clay-related env var scoped to this account, e.g.
        env("ROUTINE_WORK_EMAIL") -> CLAY_ROUTINE_WORK_EMAIL for primary,
        or CLAY_ACCOUNT_2_ROUTINE_WORK_EMAIL for account "2"."""
        return os.environ.get(f"{self._env_prefix}{suffix}") or None


def active_account() -> ClayAccount:
    name = (os.environ.get("CLAY_ACTIVE_ACCOUNT") or "primary").strip()
    if not name or name.lower() == "primary":
        return ClayAccount(name="primary", api_key=os.environ.get("CLAY_API_KEY") or None, _env_prefix="CLAY_")
    prefix = f"CLAY_ACCOUNT_{name.upper()}_"
    return ClayAccount(name=name, api_key=os.environ.get(f"{prefix}API_KEY") or None, _env_prefix=prefix)
