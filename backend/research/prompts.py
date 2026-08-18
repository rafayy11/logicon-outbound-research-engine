"""Prompt-version bookkeeping. The actual per-field prompts live in each
campaign config (backend/campaigns/configs/*.py) next to the field they
belong to -- this module just centralizes the version tag used for
idempotency keys and cache invalidation, per the credit-control rules.
Bump PROMPT_VERSION whenever a prompt's wording changes meaningfully
enough that cached research should be treated as stale.
"""

PROMPT_VERSION = "v1"
