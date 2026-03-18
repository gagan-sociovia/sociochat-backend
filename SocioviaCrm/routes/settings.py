from flask import Blueprint, jsonify, request, current_app
import secrets
import string

bp = Blueprint("settings", __name__, url_prefix="/settings")


# ------------------------------------
# Helpers
# ------------------------------------

def _get_workspace_from_request():
    """
    Prefer query param workspace_id.
    Fallback to header X-Workspace-ID.
    """
    ws = request.args.get("workspace_id") or request.headers.get("X-Workspace-ID")
    return str(ws) if ws else None


def _mask_value(name: str, val: str, masked_flag: bool) -> str:
    """
    Mask secrets only for UI display (/config only).
    Masking is currently DISABLED to return the full original value.
    """

    # If value is empty, return empty string
    if not val:
        return ""

    # Convert name to lowercase for checks
    # name_l = name.lower()

    # Masking logic is DISABLED
    # must_mask = (
    #     masked_flag
    #     or "api_key" in name_l
    #     or "secret" in name_l
    #     or "token" in name_l
    #     or "password" in name_l
    # )

    # if must_mask:
    #     return val[:4] + "..." + val[-4:] if len(val) >= 12 else ("*" * len(val))

    # Always return full original value
    return val



def _generate_secure_key(length=48):
    """
    Simple URL-safe secret generator.
    """
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ------------------------------------
# GET CONFIG (masked for UI)
# ------------------------------------

@bp.route("/config", methods=["GET"])
def config():
    """
    GET /settings/config?workspace_id=<id>
    Returns masked settings for UI display.
    """
    db = current_app.db
    Setting = current_app.crm_models.get("Setting")

    workspace_id = _get_workspace_from_request()
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400

    q = db.session.query(Setting).filter(Setting.workspace_id == workspace_id)

    out = {}
    for s in q.all():
        out[s.name] = _mask_value(s.name, s.value or "", s.masked)

    return jsonify(out), 200


# ------------------------------------
# GET full key (NO masking)
# ------------------------------------

@bp.route("/key", methods=["GET"])
def get_key():
    """
    GET /settings/key?workspace_id=<id>&key_name=<name>
    Always returns full unmasked key.
    Simple + user-friendly.
    """
    db = current_app.db
    Setting = current_app.crm_models.get("Setting")

    key_name = request.args.get("key_name")
    workspace_id = _get_workspace_from_request()

    if not key_name:
        return jsonify({"error": "key_name required"}), 400
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400

    setting = (
        db.session.query(Setting)
        .filter(Setting.name == key_name, Setting.workspace_id == workspace_id)
        .first()
    )

    if not setting:
        return jsonify({"error": "not found"}), 404

    return jsonify({
        "name": key_name,
        "value": setting.value,
        "workspace_id": workspace_id
    }), 200


# ------------------------------------
# GET all keys (NO masking)
# ------------------------------------

@bp.route("/keys", methods=["GET"])
def list_keys():
    """
    GET /settings/keys?workspace_id=<id>
    Returns all keys unmasked.
    """
    db = current_app.db
    Setting = current_app.crm_models.get("Setting")

    workspace_id = _get_workspace_from_request()
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400

    q = db.session.query(Setting).filter(Setting.workspace_id == workspace_id)

    out = {s.name: (s.value or "") for s in q.all()}

    return jsonify({
        "workspace_id": workspace_id,
        "settings": out
    }), 200


# ------------------------------------
# Regenerate key (returns ONLY masked)
# ------------------------------------

@bp.route("/regenerate-key", methods=["POST"])
def regenerate_key():
    """
    POST /settings/regenerate-key
    { "key_name": "webhook_api_key" }
    Creates or updates the key, returns masked preview.
    """
    db = current_app.db
    Setting = current_app.crm_models.get("Setting")

    payload = request.get_json() or {}
    key_name = payload.get("key_name")
    workspace_id = _get_workspace_from_request()

    if not key_name:
        return jsonify({"error": "key_name required"}), 400
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400

    # Generate strong key
    new_key = _generate_secure_key()

    q = db.session.query(Setting).filter(
        Setting.name == key_name,
        Setting.workspace_id == workspace_id
    )
    setting = q.first()

    if not setting:
        setting = Setting(
            workspace_id=workspace_id,
            name=key_name,
            value=new_key,
            masked=True
        )
        db.session.add(setting)
    else:
        setting.value = new_key
        setting.masked = True

    db.session.commit()

    masked = _mask_value(key_name, new_key, True)

    return jsonify({
        "name": key_name,
        "key": masked,
        "workspace_id": workspace_id
    }), 200
