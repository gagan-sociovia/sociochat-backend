"""
Intent Detection Module
======================
Server-side intent classification for template content validation.
Mirrors frontend logic for consistent enforcement.
"""

import re
from typing import List, Dict, Any, Tuple, Literal
from dataclasses import dataclass
from enum import Enum

# ============================================================
# Types
# ============================================================

class Intent(Enum):
    TRANSACTIONAL = "TRANSACTIONAL"
    PROMOTIONAL = "PROMOTIONAL"
    AUTHENTICATION = "AUTHENTICATION"
    AMBIGUOUS = "AMBIGUOUS"

class Confidence(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class ViolationType(Enum):
    PROMOTIONAL_KEYWORD = "PROMOTIONAL_KEYWORD"
    PROMOTIONAL_EMOJI = "PROMOTIONAL_EMOJI"
    BLOCKED_URL = "BLOCKED_URL"
    PERSUASIVE_BUTTON = "PERSUASIVE_BUTTON"
    URGENCY_LANGUAGE = "URGENCY_LANGUAGE"

@dataclass
class CategoryViolation:
    type: ViolationType
    detail: str
    location: str
    severity: str  # 'ERROR' or 'WARNING'

@dataclass
class IntentResult:
    intent: Intent
    confidence: Confidence
    violations: List[CategoryViolation]
    suggested_category: str = None

@dataclass
class ComplianceResult:
    is_compliant: bool
    violations: List[CategoryViolation]
    detected_intent: Intent
    message: str = None
    suggest_switch: bool = False

# ============================================================
# Detection Lists
# ============================================================

PROMOTIONAL_KEYWORDS = [
    'discount', 'offer', 'sale', 'free', 'cashback', 'deal', 'save', 'off',
    'promo', 'coupon', 'voucher', 'reward', 'bonus', 'gift', 'win',
    'exclusive', 'special', 'limited', 'flash', 'clearance',
]

URGENCY_KEYWORDS = [
    'buy now', 'order now', 'shop now', 'get now', 'claim now',
    'hurry', "don't miss", 'limited time', 'ends soon', 'last chance',
    'act fast', 'today only', 'expires', 'running out', 'almost gone',
    'grab', 'unlock', 'redeem',
]

PROMOTIONAL_CTA = [
    'shop', 'buy', 'order', 'purchase', 'get', 'claim', 'redeem',
    'unlock', 'grab', 'explore', 'discover', 'browse', 'check out',
]

ALLOWED_UTILITY_BUTTONS = [
    'view', 'track', 'download', 'open', 'review', 'check', 'see',
    'access', 'read', 'confirm', 'acknowledge', 'submit', 'continue',
    'manage', 'update', 'verify', 'reschedule', 'cancel',
]

PROMOTIONAL_EMOJIS = [
    '🎉', '🔥', '💥', '🛍️', '🚀', '🎁', '💰', '🎊', '⚡', '✨',
    '🤑', '💸', '🎯', '💎', '🏆', '🥳', '💪', '👉', '⭐', '🌟',
]

BLOCKED_UTILITY_URLS = [
    '/pricing', '/products', '/shop', '/offers', '/promo', '/deals',
    '/sale', '/discount', '/catalog', '/store', '/buy', '/offer',
    '/campaign', '/collection', '/new-arrivals', '/bestsellers',
]

TRANSACTIONAL_SIGNALS = [
    'order', 'confirmation', 'confirmed', 'shipped', 'delivered', 'tracking',
    'invoice', 'receipt', 'appointment', 'booking', 'reservation',
    'payment', 'refund', 'account', 'password', 'otp', 'verification',
    'reminder', 'alert', 'notification', 'update', 'status',
]

# ============================================================
# Detection Functions
# ============================================================

def find_promotional_keywords(text: str) -> List[str]:
    """Find promotional keywords in text."""
    lower_text = text.lower()
    return [kw for kw in PROMOTIONAL_KEYWORDS if kw in lower_text]

def find_urgency_language(text: str) -> List[str]:
    """Find urgency phrases in text."""
    lower_text = text.lower()
    return [phrase for phrase in URGENCY_KEYWORDS if phrase in lower_text]

def find_promotional_emojis(text: str) -> List[str]:
    """Find promotional emojis in text."""
    return [emoji for emoji in PROMOTIONAL_EMOJIS if emoji in text]

def is_promotional_button(button_text: str) -> bool:
    """Check if button text is promotional."""
    lower_text = button_text.lower()
    return any(cta in lower_text for cta in PROMOTIONAL_CTA)

def is_blocked_utility_url(url: str) -> bool:
    """Check if URL path is blocked for Utility."""
    try:
        from urllib.parse import urlparse
        pathname = urlparse(url).path.lower()
        return any(blocked in pathname for blocked in BLOCKED_UTILITY_URLS)
    except:
        return False

# ============================================================
# Main Intent Detection
# ============================================================

def detect_intent(
    body: str,
    header: str = None,
    footer: str = None,
    buttons: List[Dict[str, str]] = None
) -> IntentResult:
    """
    Detect the primary intent of template content.
    
    Args:
        body: Template body text
        header: Optional header text
        footer: Optional footer text
        buttons: List of buttons with 'text' and optional 'url'
        
    Returns:
        IntentResult with intent, confidence, and violations
    """
    violations = []
    promo_score = 0
    transactional_score = 0
    
    full_text = ' '.join(filter(None, [body, header, footer]))
    
    # Check for promotional keywords
    promo_keywords = find_promotional_keywords(full_text)
    if promo_keywords:
        promo_score += len(promo_keywords) * 2
        violations.append(CategoryViolation(
            type=ViolationType.PROMOTIONAL_KEYWORD,
            detail=f"Found promotional keywords: {', '.join(promo_keywords)}",
            location='BODY',
            severity='ERROR'
        ))
    
    # Check for urgency language
    urgency_phrases = find_urgency_language(full_text)
    if urgency_phrases:
        promo_score += len(urgency_phrases) * 3
        violations.append(CategoryViolation(
            type=ViolationType.URGENCY_LANGUAGE,
            detail=f"Found urgency language: {', '.join(urgency_phrases)}",
            location='BODY',
            severity='ERROR'
        ))
    
    # Check for promotional emojis
    promo_emojis = find_promotional_emojis(full_text)
    if promo_emojis:
        promo_score += len(promo_emojis)
        violations.append(CategoryViolation(
            type=ViolationType.PROMOTIONAL_EMOJI,
            detail=f"Found promotional emojis: {' '.join(promo_emojis)}",
            location='BODY',
            severity='WARNING'
        ))
    
    # Check button text and URLs
    if buttons:
        for btn in buttons:
            btn_text = btn.get('text', '')
            btn_url = btn.get('url', '')
            
            if is_promotional_button(btn_text):
                promo_score += 2
                violations.append(CategoryViolation(
                    type=ViolationType.PERSUASIVE_BUTTON,
                    detail=f"Button '{btn_text}' appears promotional",
                    location='BUTTON',
                    severity='ERROR'
                ))
            
            if btn_url and is_blocked_utility_url(btn_url):
                promo_score += 2
                violations.append(CategoryViolation(
                    type=ViolationType.BLOCKED_URL,
                    detail=f"URL path appears promotional: {btn_url}",
                    location='URL',
                    severity='ERROR'
                ))
    
    # Check for transactional signals
    lower_body = body.lower()
    for signal in TRANSACTIONAL_SIGNALS:
        if signal in lower_body:
            transactional_score += 1
    
    # Determine intent
    auth_pattern = re.compile(r'\b(otp|code|verify|verification code|one.?time)\b', re.IGNORECASE)
    
    if auth_pattern.search(body) and len(body) < 200:
        intent = Intent.AUTHENTICATION
        confidence = Confidence.HIGH
        suggested_category = 'AUTHENTICATION'
    elif promo_score >= 4:
        intent = Intent.PROMOTIONAL
        confidence = Confidence.HIGH if promo_score >= 6 else Confidence.MEDIUM
        suggested_category = 'MARKETING'
    elif transactional_score >= 2 and promo_score == 0:
        intent = Intent.TRANSACTIONAL
        confidence = Confidence.HIGH if transactional_score >= 4 else Confidence.MEDIUM
        suggested_category = 'UTILITY'
    elif promo_score > 0 and transactional_score > 0:
        intent = Intent.AMBIGUOUS
        confidence = Confidence.LOW
        suggested_category = 'MARKETING' if promo_score > transactional_score else 'UTILITY'
    else:
        intent = Intent.AMBIGUOUS
        confidence = Confidence.LOW
        suggested_category = None
    
    return IntentResult(
        intent=intent,
        confidence=confidence,
        violations=violations,
        suggested_category=suggested_category
    )

# ============================================================
# Category Compliance Validation
# ============================================================

def validate_category_compliance(
    category: str,
    body: str,
    header: str = None,
    footer: str = None,
    buttons: List[Dict[str, str]] = None
) -> ComplianceResult:
    """
    Validate if template content complies with selected category.
    
    Args:
        category: 'UTILITY', 'MARKETING', or 'AUTHENTICATION'
        body: Template body text
        header: Optional header text
        footer: Optional footer text
        buttons: List of buttons
        
    Returns:
        ComplianceResult with compliance status and violations
    """
    intent_result = detect_intent(body, header, footer, buttons)
    
    # AUTHENTICATION validation
    if category == 'AUTHENTICATION':
        auth_violations = []
        
        if buttons:
            auth_violations.append(CategoryViolation(
                type=ViolationType.PERSUASIVE_BUTTON,
                detail='Authentication templates cannot have buttons',
                location='BUTTON',
                severity='ERROR'
            ))
        
        if intent_result.intent == Intent.PROMOTIONAL:
            auth_violations.extend(intent_result.violations)
        
        has_otp = bool(re.search(r'\b(otp|code|verification|verify)\b', body, re.IGNORECASE))
        if not has_otp:
            auth_violations.append(CategoryViolation(
                type=ViolationType.PROMOTIONAL_KEYWORD,
                detail='Authentication templates should contain OTP/verification content',
                location='BODY',
                severity='WARNING'
            ))
        
        errors = [v for v in auth_violations if v.severity == 'ERROR']
        return ComplianceResult(
            is_compliant=len(errors) == 0,
            violations=auth_violations,
            detected_intent=intent_result.intent,
            message='Authentication templates should only contain OTP/verification content' if auth_violations else None
        )
    
    # UTILITY validation (strict)
    if category == 'UTILITY':
        errors = [v for v in intent_result.violations if v.severity == 'ERROR']
        is_promo = intent_result.intent == Intent.PROMOTIONAL
        is_ambiguous = intent_result.intent == Intent.AMBIGUOUS and intent_result.violations
        
        message = None
        suggest_switch = False
        
        if is_promo:
            message = 'This message appears promotional and does not qualify as Utility. Meta may reject or later pause this template.'
            suggest_switch = True
        elif is_ambiguous:
            message = 'This message has mixed signals. Consider removing promotional elements or switching to Marketing.'
            suggest_switch = True
        elif errors:
            message = 'This message contains elements not allowed in Utility templates.'
        
        return ComplianceResult(
            is_compliant=not errors and not is_promo,
            violations=intent_result.violations,
            detected_intent=intent_result.intent,
            message=message,
            suggest_switch=suggest_switch
        )
    
    # MARKETING validation (lenient)
    if category == 'MARKETING':
        if intent_result.intent == Intent.AUTHENTICATION:
            return ComplianceResult(
                is_compliant=True,
                violations=[],
                detected_intent=intent_result.intent,
                message='This looks like authentication content. Consider using Authentication category.',
                suggest_switch=True
            )
        
        return ComplianceResult(
            is_compliant=True,
            violations=[],
            detected_intent=intent_result.intent
        )
    
    return ComplianceResult(
        is_compliant=True,
        violations=[],
        detected_intent=intent_result.intent
    )

def violations_to_dict(violations: List[CategoryViolation]) -> List[Dict[str, str]]:
    """Convert violations to JSON-serializable dict."""
    return [
        {
            'type': v.type.value,
            'detail': v.detail,
            'location': v.location,
            'severity': v.severity
        }
        for v in violations
    ]
