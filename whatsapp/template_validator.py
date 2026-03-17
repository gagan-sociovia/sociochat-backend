"""
Template Validator - Pre-Approval Intelligence Engine
=====================================================

Validates WhatsApp templates BEFORE submission to Meta.
Provides confidence scoring, risk flags, and approval path prediction.

Rules based on Meta's automated approval criteria:
- Sequential variables ({{1}}, {{2}})
- Category-specific restrictions
- Intent detection and mismatch blocking
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class ApprovalPath(str, Enum):
    """Predicted approval path from Meta."""
    AUTOMATED_FAST = "AUTOMATED_FAST"  # Score ≥90, no flags
    AUTOMATED_SLOW = "AUTOMATED_SLOW"  # Score 75-89
    AUTOMATED_EXTENDED = "AUTOMATED_EXTENDED"  # Score 60-74, new WABA
    LIKELY_MANUAL_REVIEW = "LIKELY_MANUAL_REVIEW"  # Score <60 or high-risk


class TemplateCategory(str, Enum):
    """Template category types."""
    UTILITY = "UTILITY"
    MARKETING = "MARKETING"
    AUTHENTICATION = "AUTHENTICATION"


class DetectedIntent(str, Enum):
    """Detected content intent."""
    UTILITY = "UTILITY"
    MARKETING = "MARKETING"
    AUTHENTICATION = "AUTHENTICATION"
    UNCLEAR = "UNCLEAR"


@dataclass
class ValidationResult:
    """Result of template validation."""
    confidence_score: int  # 0-100
    risk_flags: List[str] = field(default_factory=list)
    approval_path: str = ApprovalPath.AUTOMATED_SLOW.value
    detected_intent: str = DetectedIntent.UNCLEAR.value
    suggestions: List[str] = field(default_factory=list)
    show_fast_path_badge: bool = False
    intent_mismatch: bool = False
    intent_mismatch_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "confidence_score": self.confidence_score,
            "risk_flags": self.risk_flags,
            "approval_path": self.approval_path,
            "detected_intent": self.detected_intent,
            "suggestions": self.suggestions,
            "show_fast_path_badge": self.show_fast_path_badge,
            "intent_mismatch": self.intent_mismatch,
            "intent_mismatch_message": self.intent_mismatch_message,
        }


# ==============================================================
# Validation Rules & Scoring
# ==============================================================

# Promotional/Marketing keywords that should NOT appear in Utility
PROMOTIONAL_KEYWORDS = [
    "limited time", "don't miss", "hurry", "act now", "exclusive",
    "sale", "discount", "offer", "deal", "promo", "save", "free",
    "buy now", "shop now", "order now", "get yours", "grab",
    "special", "bonus", "reward", "gift", "win", "prize",
    "% off", "half price", "clearance", "flash", "today only",
]

# CTA verbs that trigger marketing intent
CTA_VERBS = [
    "buy", "shop", "order", "subscribe", "sign up", "register",
    "download", "get started", "try", "claim", "redeem",
    "explore", "discover", "learn more", "find out",
]

# OTP/Authentication patterns
AUTH_PATTERNS = [
    r'\b\d{4,6}\b',  # 4-6 digit codes
    r'otp', r'verification code', r'one.?time.?password',
    r'verify', r'confirm your', r'security code',
]


class TemplateValidator:
    """
    Pre-approval validation engine for WhatsApp templates.
    
    Calculates confidence score based on Meta's automated approval criteria.
    Detects intent and blocks category mismatches.
    """
    
    def __init__(self, is_new_waba: bool = False):
        """
        Args:
            is_new_waba: True if WhatsApp Business Account is < 30 days old
        """
        self.is_new_waba = is_new_waba
    
    def validate(
        self,
        body: str,
        category: str,
        header: Optional[str] = None,
        footer: Optional[str] = None,
        buttons: Optional[List[Dict]] = None,
        urls: Optional[List[str]] = None,
        language: str = "en_US",
    ) -> ValidationResult:
        """
        Validate template and return confidence score with risk flags.
        
        Returns:
            ValidationResult with score, flags, approval path, and suggestions
        """
        score = 100  # Start with perfect score, deduct for issues
        flags: List[str] = []
        suggestions: List[str] = []
        
        category = category.upper()
        full_text = f"{header or ''} {body} {footer or ''}".lower()
        
        # ============================================================
        # UNIVERSAL RULES (All Categories)
        # ============================================================
        
        # 1. Check variable format (must be sequential {{1}}, {{2}})
        var_score, var_flags, var_suggestions = self._check_variables(body)
        score += var_score
        flags.extend(var_flags)
        suggestions.extend(var_suggestions)
        
        # 2. Check body length (< 300 chars is best)
        if len(body) <= 300:
            score += 10  # Bonus for short templates
        elif len(body) > 500:
            score -= 10
            flags.append("LONG_BODY")
            suggestions.append("Consider shortening body to < 300 characters for faster approval")
        
        # 3. Language bonus (English first)
        if language == "en_US":
            score += 5
        
        # 4. Check for markdown (not allowed)
        if re.search(r'\*\*|__|~~', body):
            score -= 15
            flags.append("MARKDOWN_DETECTED")
            suggestions.append("Remove markdown formatting (**, __, ~~)")
        
        # ============================================================
        # CATEGORY-SPECIFIC RULES
        # ============================================================
        
        if category == TemplateCategory.UTILITY.value:
            cat_score, cat_flags, cat_suggestions = self._validate_utility(
                body, full_text, buttons
            )
            score += cat_score
            flags.extend(cat_flags)
            suggestions.extend(cat_suggestions)
            
        elif category == TemplateCategory.MARKETING.value:
            cat_score, cat_flags, cat_suggestions = self._validate_marketing(
                body, full_text, buttons
            )
            score += cat_score
            flags.extend(cat_flags)
            suggestions.extend(cat_suggestions)
            
        elif category == TemplateCategory.AUTHENTICATION.value:
            cat_score, cat_flags, cat_suggestions = self._validate_authentication(
                body, buttons, urls
            )
            score += cat_score
            flags.extend(cat_flags)
            suggestions.extend(cat_suggestions)
        
        # ============================================================
        # INTENT DETECTION & MISMATCH CHECK
        # ============================================================
        
        detected_intent = self._detect_intent(full_text, buttons)
        intent_mismatch = False
        intent_mismatch_message = None
        
        # Critical: Block Marketing content submitted as Utility
        if detected_intent == DetectedIntent.MARKETING.value and category == TemplateCategory.UTILITY.value:
            intent_mismatch = True
            intent_mismatch_message = (
                "Intent mismatch detected. This content appears promotional but is submitted as Utility. "
                "Utility templates must be user-triggered and non-promotional. "
                "Consider using Marketing category instead."
            )
            score -= 40
            flags.append("INTENT_MISMATCH")
        
        # ============================================================
        # CALCULATE APPROVAL PATH
        # ============================================================
        
        # Clamp score to 0-100
        score = max(0, min(100, score))
        
        # Determine approval path
        if score >= 90 and not flags:
            approval_path = ApprovalPath.AUTOMATED_FAST.value
        elif score >= 75:
            approval_path = ApprovalPath.AUTOMATED_SLOW.value
        elif score >= 60:
            # Extended for new WABAs or borderline cases
            approval_path = ApprovalPath.AUTOMATED_EXTENDED.value
        else:
            approval_path = ApprovalPath.LIKELY_MANUAL_REVIEW.value
        
        # New WABAs get extended path unless very high score
        if self.is_new_waba and approval_path == ApprovalPath.AUTOMATED_SLOW.value:
            approval_path = ApprovalPath.AUTOMATED_EXTENDED.value
        
        # Fast path badge
        show_fast_path_badge = score >= 90 and len(flags) == 0
        
        return ValidationResult(
            confidence_score=score,
            risk_flags=flags,
            approval_path=approval_path,
            detected_intent=detected_intent,
            suggestions=suggestions,
            show_fast_path_badge=show_fast_path_badge,
            intent_mismatch=intent_mismatch,
            intent_mismatch_message=intent_mismatch_message,
        )
    
    def _check_variables(self, body: str) -> tuple:
        """Check variable format and placement."""
        score = 0
        flags = []
        suggestions = []
        
        # Find all variables
        variables = re.findall(r'\{\{(\d+)\}\}', body)
        
        if variables:
            # Check if sequential (1, 2, 3...)
            expected = list(range(1, len(variables) + 1))
            actual = [int(v) for v in variables]
            
            if actual != expected:
                score -= 20
                flags.append("NON_SEQUENTIAL_VARIABLES")
                suggestions.append(f"Variables must be sequential: {{{{1}}}}, {{{{2}}}}, etc. Found: {actual}")
            
            # CRITICAL: Variable at start (Meta rejects this)
            stripped = body.strip()
            if stripped.startswith("{{"):
                score -= 50  # Critical - blocks submission
                flags.append("VARIABLE_AT_START")
                suggestions.append("⚠️ BLOCKED: Variables cannot be at the start of the template (Meta rejects this)")
            
            # CRITICAL: Variable at end (Meta rejects this)
            if stripped.endswith("}}"):
                score -= 50  # Critical - blocks submission
                flags.append("VARIABLE_AT_END")
                suggestions.append("⚠️ BLOCKED: Variables cannot be at the end of the template (Meta rejects this). Add text after the variable, e.g. 'Reference: {{1}}.'")
        
        return score, flags, suggestions
    
    def _validate_utility(self, body: str, full_text: str, buttons: Optional[List]) -> tuple:
        """Validate Utility template rules."""
        score = 0
        flags = []
        suggestions = []
        
        # Check for promotional keywords (NOT allowed in Utility)
        found_promo = []
        for keyword in PROMOTIONAL_KEYWORDS:
            if keyword in full_text:
                found_promo.append(keyword)
        
        if found_promo:
            score -= 30
            flags.append("PROMOTIONAL_LANGUAGE")
            suggestions.append(f"Remove promotional words for Utility: {', '.join(found_promo[:3])}")
        
        # Check for emojis (discouraged in Utility)
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # flags
            "]+", 
            flags=re.UNICODE
        )
        if emoji_pattern.search(body):
            score -= 25
            flags.append("EMOJI_IN_UTILITY")
            suggestions.append("Remove emojis from Utility templates for faster approval")
        
        # Check for CTA verbs
        found_cta = [v for v in CTA_VERBS if v in full_text]
        if found_cta:
            score -= 20
            flags.append("CTA_IN_UTILITY")
            suggestions.append(f"Avoid call-to-action verbs in Utility: {', '.join(found_cta[:3])}")
        
        return score, flags, suggestions
    
    def _validate_marketing(self, body: str, full_text: str, buttons: Optional[List]) -> tuple:
        """Validate Marketing template rules."""
        score = 0
        flags = []
        suggestions = []
        
        # Marketing templates are more lenient
        # Check for required elements
        has_cta = any(v in full_text for v in CTA_VERBS)
        
        if not has_cta and not buttons:
            suggestions.append("Consider adding a clear call-to-action for better engagement")
        
        # Excessive emojis warning
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "]+"
        )
        emoji_count = len(emoji_pattern.findall(body))
        if emoji_count > 5:
            score -= 10
            flags.append("EXCESSIVE_EMOJIS")
            suggestions.append("Reduce emoji count for professional appearance")
        
        return score, flags, suggestions
    
    def _validate_authentication(self, body: str, buttons: Optional[List], urls: Optional[List]) -> tuple:
        """Validate Authentication template rules."""
        score = 0
        flags = []
        suggestions = []
        
        body_lower = body.lower()
        
        # Must contain OTP/verification pattern
        has_auth_pattern = any(re.search(p, body_lower) for p in AUTH_PATTERNS)
        if not has_auth_pattern:
            score -= 40
            flags.append("MISSING_OTP_PATTERN")
            suggestions.append("Authentication templates must contain verification code or OTP")
        
        # No buttons allowed
        if buttons and len(buttons) > 0:
            # Only copy-code button is allowed
            non_copy_buttons = [b for b in buttons if b.get("type", "").upper() != "COPY_CODE"]
            if non_copy_buttons:
                score -= 30
                flags.append("BUTTONS_IN_AUTH")
                suggestions.append("Remove buttons from Authentication templates (only Copy Code allowed)")
        
        # No URLs allowed
        if urls and len(urls) > 0:
            score -= 30
            flags.append("URLS_IN_AUTH")
            suggestions.append("Remove URLs from Authentication templates")
        
        # Check for non-auth content
        non_auth_keywords = ["order", "delivery", "payment", "invoice", "product"]
        found = [k for k in non_auth_keywords if k in body_lower]
        if found:
            score -= 20
            flags.append("NON_AUTH_CONTENT")
            suggestions.append("Authentication templates should only contain verification/OTP content")
        
        return score, flags, suggestions
    
    def _detect_intent(self, full_text: str, buttons: Optional[List]) -> str:
        """Detect the actual intent of the template content."""
        scores = {
            DetectedIntent.UTILITY.value: 0,
            DetectedIntent.MARKETING.value: 0,
            DetectedIntent.AUTHENTICATION.value: 0,
        }
        
        # Check for auth patterns
        if any(re.search(p, full_text) for p in AUTH_PATTERNS):
            scores[DetectedIntent.AUTHENTICATION.value] += 50
        
        # Check for promotional keywords
        promo_count = sum(1 for k in PROMOTIONAL_KEYWORDS if k in full_text)
        if promo_count > 0:
            scores[DetectedIntent.MARKETING.value] += promo_count * 15
        
        # Check for CTA verbs
        cta_count = sum(1 for v in CTA_VERBS if v in full_text)
        if cta_count > 0:
            scores[DetectedIntent.MARKETING.value] += cta_count * 10
        
        # Utility indicators
        utility_keywords = [
            "order status", "tracking", "invoice", "receipt", "appointment",
            "booking confirmed", "shipment", "delivery update", "payment received",
            "reminder", "scheduled", "confirmation",
        ]
        utility_count = sum(1 for k in utility_keywords if k in full_text)
        if utility_count > 0:
            scores[DetectedIntent.UTILITY.value] += utility_count * 12
        
        # Get highest score
        max_intent = max(scores.items(), key=lambda x: x[1])
        
        # Return if confident, else unclear
        if max_intent[1] >= 20:
            return max_intent[0]
        return DetectedIntent.UNCLEAR.value


# ==============================================================
# Convenience Function for Routes
# ==============================================================

def validate_template(
    body: str,
    category: str,
    header: Optional[str] = None,
    footer: Optional[str] = None,
    buttons: Optional[List[Dict]] = None,
    urls: Optional[List[str]] = None,
    language: str = "en_US",
    is_new_waba: bool = False,
) -> Dict[str, Any]:
    """
    Validate a template and return results as dict.
    
    Convenience wrapper for API routes.
    """
    validator = TemplateValidator(is_new_waba=is_new_waba)
    result = validator.validate(
        body=body,
        category=category,
        header=header,
        footer=footer,
        buttons=buttons,
        urls=urls,
        language=language,
    )
    return result.to_dict()
