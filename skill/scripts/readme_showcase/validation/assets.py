from __future__ import annotations

from typing import Any

from ..contracts.assets import validate_asset_manifest


def validate_explicit_asset_manifest(payload: Any, **kwargs: Any) -> dict[str, Any]:
    return validate_asset_manifest(payload, **kwargs)
