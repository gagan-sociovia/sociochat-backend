"""
WhatsApp Tech Provider onboarding status constants and user-facing messages.
"""

from typing import Optional


class OnboardingStatus:
    PENDING = "PENDING"
    ASSET_SHARING_PENDING = "ASSET_SHARING_PENDING"
    WABA_NOT_VISIBLE = "WABA_NOT_VISIBLE"
    PHONE_NOT_VISIBLE = "PHONE_NOT_VISIBLE"
    SUBSCRIPTION_PENDING = "SUBSCRIPTION_PENDING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    RECONNECT_REQUIRED = "RECONNECT_REQUIRED"


# Statuses that background revalidation should retry
REVALIDATION_STATUSES = frozenset({
    OnboardingStatus.PENDING,
    OnboardingStatus.ASSET_SHARING_PENDING,
    OnboardingStatus.SUBSCRIPTION_PENDING,
    OnboardingStatus.WABA_NOT_VISIBLE,
    OnboardingStatus.PHONE_NOT_VISIBLE,
})

# Internal / technical error -> customer message (Phase 12)
USER_MESSAGES = {
    "missing_permissions": (
        "We were unable to access your WhatsApp Business assets due to missing permissions "
        "or incomplete Meta Business setup. Please reconnect your WhatsApp account and ensure "
        "all requested permissions are granted."
    ),
    "incomplete_onboarding": (
        "Your WhatsApp setup is incomplete. Please reconnect and complete all onboarding steps."
    ),
    "missing_business_access": (
        "We cannot access the selected WhatsApp Business Account. Please ensure you have "
        "administrator access and reconnect."
    ),
    "asset_sharing_incomplete": (
        "Asset sharing is incomplete. Please reconnect and share your WhatsApp Business "
        "Account with SocioChat during setup."
    ),
    "waba_not_visible": (
        "We cannot see your WhatsApp Business Account yet. Meta may still be processing setup — "
        "we will retry automatically, or you can reconnect."
    ),
    "phone_not_visible": (
        "We cannot see your WhatsApp phone number yet. Please ensure the number is added to "
        "your WABA and reconnect if needed."
    ),
    "subscription_failed": (
        "Webhook subscription could not be completed. We will retry automatically, or please reconnect."
    ),
    "reconnect_required": (
        "Please reconnect your WhatsApp account to complete setup and grant the required permissions."
    ),
    "portfolio_not_linked": (
        "Your Meta Business Portfolio is not linked. Please complete Business Portfolio linking "
        "during Embedded Signup and reconnect."
    ),
}


def user_message_for_status(status: str, technical_error: Optional[str] = None) -> str:
    """Map onboarding status / error to a customer-safe message."""
    if status == OnboardingStatus.RECONNECT_REQUIRED:
        return USER_MESSAGES["reconnect_required"]
    if status == OnboardingStatus.ASSET_SHARING_PENDING:
        return USER_MESSAGES["asset_sharing_incomplete"]
    if status == OnboardingStatus.WABA_NOT_VISIBLE:
        return USER_MESSAGES["waba_not_visible"]
    if status == OnboardingStatus.PHONE_NOT_VISIBLE:
        return USER_MESSAGES["phone_not_visible"]
    if status == OnboardingStatus.SUBSCRIPTION_PENDING:
        return USER_MESSAGES["subscription_failed"]
    if technical_error:
        lower = technical_error.lower()
        if "permission" in lower or "scope" in lower:
            return USER_MESSAGES["missing_permissions"]
        if "portfolio" in lower or "business" in lower:
            return USER_MESSAGES["portfolio_not_linked"]
        if "waba" in lower:
            return USER_MESSAGES["missing_business_access"]
    return USER_MESSAGES["incomplete_onboarding"]
