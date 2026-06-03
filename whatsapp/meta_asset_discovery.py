"""
Meta Tech Provider asset discovery and validation for WhatsApp onboarding.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from .oauth import REQUIRED_SCOPES

logger = logging.getLogger(__name__)

META_APP_ID = os.getenv("META_APP_ID") or os.getenv("FB_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET") or os.getenv("FB_APP_SECRET")
META_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")
META_GRAPH_API = f"https://graph.facebook.com/{META_API_VERSION}"


@dataclass
class PhoneAsset:
    phone_number_id: str
    display_phone_number: Optional[str] = None
    verified_name: Optional[str] = None
    quality_rating: Optional[str] = None


@dataclass
class WabaAsset:
    waba_id: str
    waba_name: Optional[str] = None
    business_id: Optional[str] = None
    business_name: Optional[str] = None
    phone_numbers: List[PhoneAsset] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    portfolio_visible: bool = False
    businesses: List[Dict[str, Any]] = field(default_factory=list)
    wabas: List[WabaAsset] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    granular_scopes: List[Dict[str, Any]] = field(default_factory=list)
    token_valid: bool = False
    error: Optional[str] = None


@dataclass
class BindingResult:
    waba_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    display_phone_number: Optional[str] = None
    verified_name: Optional[str] = None
    meta_business_id: Optional[str] = None
    meta_business_name: Optional[str] = None


@dataclass
class ValidationResult:
    ok: bool
    status: str
    onboarding_error: Optional[str] = None
    portfolio_visible: bool = False
    waba_visible: bool = False
    phone_visible: bool = False
    permissions_valid: bool = False
    asset_sharing_complete: bool = False
    app_subscribed: bool = False
    token_valid: bool = False
    missing_scopes: List[str] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)


def _app_access_token() -> str:
    return f"{META_APP_ID}|{META_APP_SECRET}"


def _get_json(url: str, *, params: Optional[Dict] = None, headers: Optional[Dict] = None, timeout: int = 15) -> Dict:
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    data = response.json()
    if response.status_code >= 400 or "error" in data:
        message = data.get("error", {}).get("message", f"HTTP {response.status_code}")
        raise ValueError(message)
    return data


def _debug_token(access_token: str) -> Dict[str, Any]:
    if not META_APP_ID or not META_APP_SECRET:
        return {}
    return _get_json(
        f"{META_GRAPH_API}/debug_token",
        params={"input_token": access_token, "access_token": _app_access_token()},
    ).get("data", {})


def discover_whatsapp_assets(access_token: str) -> DiscoveryResult:
    """Discover portfolios, WABAs, and phone numbers visible to the access token."""
    result = DiscoveryResult()

    try:
        token_data = _debug_token(access_token)
        result.token_valid = token_data.get("is_valid", False)
        result.scopes = token_data.get("scopes", []) or []
        result.granular_scopes = token_data.get("granular_scopes", []) or []
    except Exception as exc:
        logger.warning("debug_token failed during discovery: %s", exc)
        result.error = str(exc)

    try:
        me = _get_json(
            f"{META_GRAPH_API}/me",
            params={
                "access_token": access_token,
                "fields": (
                    "id,name,businesses{id,name,owned_whatsapp_business_accounts"
                    "{id,name,phone_numbers{id,display_phone_number,verified_name,quality_rating}}}"
                ),
            },
        )
        businesses = me.get("businesses", {}).get("data", [])
        result.businesses = businesses
        result.portfolio_visible = bool(businesses)

        for business in businesses:
            biz_id = business.get("id")
            biz_name = business.get("name")
            for waba in business.get("owned_whatsapp_business_accounts", {}).get("data", []):
                asset = WabaAsset(
                    waba_id=waba.get("id"),
                    waba_name=waba.get("name"),
                    business_id=biz_id,
                    business_name=biz_name,
                )
                for phone in waba.get("phone_numbers", {}).get("data", []):
                    asset.phone_numbers.append(
                        PhoneAsset(
                            phone_number_id=phone.get("id"),
                            display_phone_number=phone.get("display_phone_number"),
                            verified_name=phone.get("verified_name"),
                            quality_rating=phone.get("quality_rating"),
                        )
                    )
                if asset.waba_id and not asset.phone_numbers:
                    asset.phone_numbers.extend(_fetch_phones_for_waba(access_token, asset.waba_id))
                if asset.waba_id:
                    result.wabas.append(asset)
    except Exception as exc:
        logger.warning("/me discovery failed: %s", exc)
        if not result.error:
            result.error = str(exc)

    if not result.wabas:
        for scope in result.granular_scopes:
            if scope.get("scope") != "whatsapp_business_management":
                continue
            for waba_id in scope.get("target_ids", []) or []:
                asset = WabaAsset(waba_id=waba_id)
                asset.phone_numbers = _fetch_phones_for_waba(access_token, waba_id)
                result.wabas.append(asset)

    return result


def _fetch_phones_for_waba(access_token: str, waba_id: str) -> List[PhoneAsset]:
    phones: List[PhoneAsset] = []
    try:
        data = _get_json(
            f"{META_GRAPH_API}/{waba_id}/phone_numbers",
            params={"access_token": access_token},
        )
        for phone in data.get("data", []):
            phones.append(
                PhoneAsset(
                    phone_number_id=phone.get("id"),
                    display_phone_number=phone.get("display_phone_number"),
                    verified_name=phone.get("verified_name"),
                    quality_rating=phone.get("quality_rating"),
                )
            )
    except Exception as exc:
        logger.warning("Failed to fetch phones for WABA %s: %s", waba_id, exc)
    return phones


def resolve_binding_for_auto_connect(
    discovery: DiscoveryResult,
    preferred_waba_id: Optional[str] = None,
    preferred_phone_id: Optional[str] = None,
) -> BindingResult:
    """Pick WABA + phone from discovery, preferring Embedded Signup session IDs."""
    binding = BindingResult()

    if preferred_waba_id:
        for waba in discovery.wabas:
            if waba.waba_id == preferred_waba_id:
                binding.waba_id = waba.waba_id
                binding.meta_business_id = waba.business_id
                binding.meta_business_name = waba.business_name
                if preferred_phone_id:
                    for phone in waba.phone_numbers:
                        if phone.phone_number_id == preferred_phone_id:
                            binding.phone_number_id = phone.phone_number_id
                            binding.display_phone_number = phone.display_phone_number
                            binding.verified_name = phone.verified_name
                            return binding
                if waba.phone_numbers:
                    phone = waba.phone_numbers[0]
                    binding.phone_number_id = phone.phone_number_id
                    binding.display_phone_number = phone.display_phone_number
                    binding.verified_name = phone.verified_name
                return binding

    if discovery.wabas:
        waba = discovery.wabas[0]
        binding.waba_id = waba.waba_id
        binding.meta_business_id = waba.business_id
        binding.meta_business_name = waba.business_name
        if waba.phone_numbers:
            phone = waba.phone_numbers[0]
            binding.phone_number_id = phone.phone_number_id
            binding.display_phone_number = phone.display_phone_number
            binding.verified_name = phone.verified_name

    return binding


def _check_permissions(scopes: List[str]) -> tuple[bool, List[str]]:
    scope_set = {s.lower() for s in scopes}
    missing = [s for s in REQUIRED_SCOPES if s.lower() not in scope_set]
    return len(missing) == 0, missing


def _verify_waba_visible(access_token: str, waba_id: str) -> bool:
    try:
        _get_json(f"{META_GRAPH_API}/{waba_id}", params={"access_token": access_token, "fields": "id,name"})
        return True
    except Exception:
        return False


def _verify_phone_visible(access_token: str, phone_number_id: str) -> bool:
    try:
        _get_json(
            f"{META_GRAPH_API}/{phone_number_id}",
            params={"access_token": access_token, "fields": "id,display_phone_number"},
        )
        return True
    except Exception:
        return False


def validate_discovered_assets(
    access_token: str,
    discovery: DiscoveryResult,
    binding: BindingResult,
    *,
    app_subscribed: bool = False,
) -> ValidationResult:
    """Run Tech Provider validation rules before marking an account ACTIVE."""
    from .onboarding_status import OnboardingStatus

    permissions_valid, missing_scopes = _check_permissions(discovery.scopes)
    portfolio_visible = discovery.portfolio_visible or bool(discovery.granular_scopes)
    waba_visible = bool(binding.waba_id) and _verify_waba_visible(access_token, binding.waba_id)
    phone_visible = bool(binding.phone_number_id) and _verify_phone_visible(access_token, binding.phone_number_id)
    asset_sharing_complete = waba_visible and (phone_visible or bool(discovery.wabas))

    checks = {
        "portfolio_visible": portfolio_visible,
        "waba_visible": waba_visible,
        "phone_visible": phone_visible,
        "permissions_valid": permissions_valid,
        "asset_sharing_complete": asset_sharing_complete,
        "app_subscribed": app_subscribed,
        "token_valid": discovery.token_valid,
    }

    if not discovery.wabas and not binding.waba_id:
        return ValidationResult(
            ok=False,
            status=OnboardingStatus.ASSET_SHARING_PENDING,
            onboarding_error="No WhatsApp Business assets found — asset sharing may be incomplete",
            portfolio_visible=portfolio_visible,
            waba_visible=False,
            phone_visible=False,
            permissions_valid=permissions_valid,
            asset_sharing_complete=False,
            app_subscribed=app_subscribed,
            token_valid=discovery.token_valid,
            missing_scopes=missing_scopes,
            checks=checks,
        )

    if not permissions_valid:
        return ValidationResult(
            ok=False,
            status=OnboardingStatus.FAILED,
            onboarding_error=f"Missing required permissions: {', '.join(missing_scopes)}",
            portfolio_visible=portfolio_visible,
            waba_visible=waba_visible,
            phone_visible=phone_visible,
            permissions_valid=False,
            asset_sharing_complete=asset_sharing_complete,
            app_subscribed=app_subscribed,
            token_valid=discovery.token_valid,
            missing_scopes=missing_scopes,
            checks=checks,
        )

    if not portfolio_visible:
        return ValidationResult(
            ok=False,
            status=OnboardingStatus.PENDING,
            onboarding_error="Business Portfolio not visible — Meta may still be propagating access",
            portfolio_visible=False,
            waba_visible=waba_visible,
            phone_visible=phone_visible,
            permissions_valid=permissions_valid,
            asset_sharing_complete=asset_sharing_complete,
            app_subscribed=app_subscribed,
            token_valid=discovery.token_valid,
            missing_scopes=missing_scopes,
            checks=checks,
        )

    if not binding.waba_id or not waba_visible:
        return ValidationResult(
            ok=False,
            status=OnboardingStatus.WABA_NOT_VISIBLE,
            onboarding_error="WABA is not visible to the application",
            portfolio_visible=portfolio_visible,
            waba_visible=False,
            phone_visible=phone_visible,
            permissions_valid=permissions_valid,
            asset_sharing_complete=False,
            app_subscribed=app_subscribed,
            token_valid=discovery.token_valid,
            missing_scopes=missing_scopes,
            checks=checks,
        )

    if not binding.phone_number_id or not phone_visible:
        return ValidationResult(
            ok=False,
            status=OnboardingStatus.PHONE_NOT_VISIBLE,
            onboarding_error="Phone number is not visible to the application",
            portfolio_visible=portfolio_visible,
            waba_visible=waba_visible,
            phone_visible=False,
            permissions_valid=permissions_valid,
            asset_sharing_complete=asset_sharing_complete,
            app_subscribed=app_subscribed,
            token_valid=discovery.token_valid,
            missing_scopes=missing_scopes,
            checks=checks,
        )

    if not app_subscribed:
        return ValidationResult(
            ok=False,
            status=OnboardingStatus.SUBSCRIPTION_PENDING,
            onboarding_error="WABA is not subscribed to application webhooks",
            portfolio_visible=portfolio_visible,
            waba_visible=waba_visible,
            phone_visible=phone_visible,
            permissions_valid=permissions_valid,
            asset_sharing_complete=asset_sharing_complete,
            app_subscribed=False,
            token_valid=discovery.token_valid,
            missing_scopes=missing_scopes,
            checks=checks,
        )

    return ValidationResult(
        ok=True,
        status=OnboardingStatus.ACTIVE,
        portfolio_visible=portfolio_visible,
        waba_visible=waba_visible,
        phone_visible=phone_visible,
        permissions_valid=permissions_valid,
        asset_sharing_complete=asset_sharing_complete,
        app_subscribed=True,
        token_valid=discovery.token_valid,
        missing_scopes=missing_scopes,
        checks=checks,
    )
