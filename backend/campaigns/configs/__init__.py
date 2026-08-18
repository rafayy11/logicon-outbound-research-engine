from __future__ import annotations

from backend.campaigns.configs.base import CampaignConfig
from backend.campaigns.configs.court_reporting import COURT_REPORTING

_REGISTRY: dict[str, CampaignConfig] = {
    COURT_REPORTING.key: COURT_REPORTING,
}

try:
    from backend.campaigns.configs.process_serving import PROCESS_SERVING

    _REGISTRY[PROCESS_SERVING.key] = PROCESS_SERVING
except ImportError:
    pass

try:
    from backend.campaigns.configs.ia_ime import IA_IME

    _REGISTRY[IA_IME.key] = IA_IME
except ImportError:
    pass


def get_config(campaign_key: str) -> CampaignConfig:
    try:
        return _REGISTRY[campaign_key]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY)) or "(none configured yet)"
        raise ValueError(
            f"Unknown campaign '{campaign_key}'. Available: {available}"
        ) from exc


def available_campaigns() -> list[str]:
    return sorted(_REGISTRY)
