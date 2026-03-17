"""
SMS OTP Routes for SocioChat
Send and verify phone OTP via smshorizon.co.in provider.
"""

import os
import re
import random
import logging
import urllib.parse
import requests as http_requests
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User

sms_bp = Blueprint("sms", __name__, url_prefix="/api/sms")
logger = logging.getLogger(__name__)

# ── SMS Provider Config ──
SMS_USER = os.getenv("SMS_USER", "profes")
SMS_APIKEY = os.getenv("SMS_APIKEY", "ghSV6w6TthpzHc3ytnGj")
SMS_SEND_URL = os.getenv("SMS_SEND_URL", "https://smshorizon.co.in/api/sendsms.php")
SMS_SENDERID = os.getenv("SMS_SENDERID", "TRVTKT")
SMS_TID = os.getenv("SMS_TID", "1207176191702982105")

OTP_TTL_MIN = int(os.getenv("PHONE_OTP_TTL_MIN", 10))
OTP_RESEND_COOLDOWN_SEC = int(os.getenv("PHONE_OTP_RESEND_COOLDOWN_SEC", 30))


# ── Helpers ──

def _now_utc():
    return datetime.now(timezone.utc)


def _generate_numeric_otp(length=4):
    start = 10 ** (length - 1)
    end = (10 ** length) - 1
    return str(random.randint(start, end))


def _normalize_phone(phone: str) -> str:
    """Normalize Indian phone to digits-only (e.g. 919876543210)."""
    if not phone:
        return ""
    s = str(phone).strip()
    if s.startswith("+"):
        digits = re.sub(r"\D", "", s[1:])
    else:
        digits = re.sub(r"\D", "", s)
    # Add 91 prefix for 10-digit Indian numbers
    if len(digits) == 10:
        digits = "91" + digits
    if digits.startswith("0") and len(digits) == 11:
        digits = "91" + digits[1:]
    return digits


def send_sms_horizon(mobile_digits: str, message_text: str) -> tuple:
    """Send SMS via smshorizon.co.in. Returns (success, response_text_or_error)."""
    try:
        encoded_message = urllib.parse.quote_plus(message_text)
        url = (
            f"{SMS_SEND_URL}"
            f"?user={SMS_USER}"
            f"&apikey={SMS_APIKEY}"
            f"&mobile={mobile_digits}"
            f"&senderid={SMS_SENDERID}"
            f"&message={encoded_message}"
            f"&type=txt"
            f"&tid={SMS_TID}"
        )

        resp = http_requests.get(url, timeout=10)
        resp_text = (resp.text or "").strip()
        logger.info("SMS request => url=%s status=%s response=%s", url[:100], resp.status_code, resp_text)

        if not resp.ok:
            return False, f"HTTP {resp.status_code}: {resp_text}"

        if resp_text.upper().startswith("ERROR"):
            return False, resp_text

        return True, resp_text

    except http_requests.exceptions.RequestException as e:
        logger.exception("HTTP request to SMS provider failed")
        return False, str(e)
    except Exception as e:
        logger.exception("Unexpected error sending SMS")
        return False, str(e)


# ── Routes ──

@sms_bp.route("/send-otp", methods=["POST"])
def send_otp():
    """Send phone OTP. Body: { phone, email (optional), otp_length (optional, default 4) }"""
    data = request.get_json() or {}
    phone_in = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip().lower()
    otp_length = int(data.get("otp_length", 4))

    if not phone_in and not email:
        return jsonify({"success": False, "error": "phone_or_email_required"}), 400

    # Find user
    user = None
    if email:
        user = User.query.filter_by(email=email).first()
    if not user and phone_in:
        normalized = _normalize_phone(phone_in)
        user = User.query.filter(
            (User.phone == normalized) | (User.phone == phone_in)
        ).order_by(User.id.desc()).first()

    if not user:
        return jsonify({"success": False, "error": "user_not_found"}), 404

    # Cooldown check
    now = _now_utc()
    last_sent = getattr(user, "phone_last_sent_at", None)
    if last_sent:
        # Make last_sent timezone-aware if it isn't
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        if (now - last_sent).total_seconds() < OTP_RESEND_COOLDOWN_SEC:
            retry_after = OTP_RESEND_COOLDOWN_SEC - int((now - last_sent).total_seconds())
            return jsonify({"success": False, "error": "otp_cooldown", "retry_after_seconds": retry_after}), 429

    # Generate OTP
    otp = _generate_numeric_otp(otp_length)
    logger.info(f"PHONE_OTP for {phone_in}: {otp}")
    otp_hash = generate_password_hash(otp)
    expires_at = now + timedelta(minutes=OTP_TTL_MIN)

    user.phone_otp_hash = otp_hash
    user.phone_otp_expires_at = expires_at
    user.phone_last_sent_at = now

    # Normalize and store phone
    normalized_mobile = _normalize_phone(phone_in) or getattr(user, "phone", "") or ""
    if normalized_mobile and (not user.phone or user.phone != normalized_mobile):
        user.phone = normalized_mobile

    db.session.commit()

    # Build DLT-compliant message
    message_text = (
        " Dear Customer,\n"
        f"Your One-Time Password (OTP) is {otp}.\n"
        "Please do not share this code with anyone for security reasons.\n\n"
        "Regards,\n"
        "Profes"
    )

    # Send SMS
    try:
        success, provider_resp = send_sms_horizon(normalized_mobile, message_text)
    except Exception as exc:
        logger.exception("SMS provider call raised exception for user=%s", user.id)
        return jsonify({"success": False, "error": "sms_provider_error", "detail": str(exc)}), 502

    if not success:
        logger.warning("SMS send failed for mobile=%s user=%s detail=%s", normalized_mobile, user.id, provider_resp)
        return jsonify({"success": False, "error": "sms_provider_error", "detail": provider_resp}), 502

    return jsonify({
        "success": True,
        "msgid": provider_resp,
        "expires_at": expires_at.isoformat(),
        "user_id": user.id,
        "normalized_mobile": normalized_mobile,
    }), 200


@sms_bp.route("/verify-otp", methods=["POST"])
def verify_otp():
    """Verify phone OTP. Body: { phone, code, email (optional), user_id (optional) }"""
    data = request.get_json() or {}
    phone_in = (data.get("phone") or "").strip()
    code = (data.get("code") or "").strip()
    email = (data.get("email") or "").strip().lower()
    user_id = data.get("user_id")

    if not code or not (phone_in or email or user_id):
        return jsonify({"success": False, "error": "phone_and_code_required"}), 400

    # Find user (prefer user_id)
    user = None
    if user_id:
        try:
            user = User.query.get(int(user_id))
        except Exception:
            pass

    if not user:
        if email:
            user = User.query.filter_by(email=email).first()
        if not user and phone_in:
            cleaned = _normalize_phone(phone_in)
            user = User.query.filter(
                (User.phone == cleaned) | (User.phone == phone_in)
            ).order_by(User.id.desc()).first()

    if not user:
        return jsonify({"success": False, "error": "user_not_found"}), 404

    if not getattr(user, "phone_otp_hash", None) or not getattr(user, "phone_otp_expires_at", None):
        return jsonify({"success": False, "error": "otp_not_requested"}), 400

    otp_expires = user.phone_otp_expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)

    if otp_expires < _now_utc():
        return jsonify({"success": False, "error": "otp_expired"}), 400

    if not check_password_hash(user.phone_otp_hash, code):
        return jsonify({"success": False, "error": "invalid_otp"}), 400

    # Success
    user.phone_verified = True
    user.phone_otp_hash = None
    user.phone_otp_expires_at = None
    user.phone_last_sent_at = None
    db.session.commit()

    logger.info("User %s phone verified", user.id)
    return jsonify({"success": True, "message": "phone_verified", "user_id": user.id}), 200
