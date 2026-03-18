import requests
from flask import Blueprint, request, jsonify
from models import SocialAccount

meta_bp = Blueprint("meta_bp", __name__)

GRAPH_VERSION = "v22.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"


# --------------------------------
# Helper: Meta GET Request
# --------------------------------
def meta_get(url, params):
    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        if "error" in data:
            return None, data["error"]["message"]

        return data, None

    except Exception as e:
        return None, str(e)


# --------------------------------
# Helper: Get Meta Account
# --------------------------------
def get_meta_account(workspace_id, user_id):
    # Using provider="facebook" to match existing DB schema
    account = SocialAccount.query.filter_by(
        workspace_id=str(workspace_id),
        user_id=int(user_id) if user_id else None,
        provider="facebook"
    ).first()

    return account


# --------------------------------
# 1️⃣ Conversion Locations
# --------------------------------
@meta_bp.route("/api/meta/conversion-locations", methods=["GET"])
def get_conversion_locations():

    locations = [
        {"value": "website", "label": "Website", "group": "Single"},
        {"value": "app", "label": "App", "group": "Single"},
        {"value": "whatsapp", "label": "WhatsApp", "group": "Single"},
        {"value": "calls", "label": "Calls", "group": "Single"},
        {"value": "website_app", "label": "Website and App", "group": "Multiple"},
        {"value": "website_store", "label": "Website and In-store", "group": "Multiple"},
        {"value": "website_app_store", "label": "Website, App and In-store", "group": "Multiple"},
        {"value": "website_calls", "label": "Website and Calls", "group": "Multiple"}
    ]

    return jsonify({"locations": locations})


# --------------------------------
# 2️⃣ Performance Goals
# --------------------------------
@meta_bp.route("/api/meta/performance-goals", methods=["GET"])
def get_performance_goals():

    location = request.args.get("location", "website")

    PERFORMANCE_GOALS = {

        "website": [
            {
                "value": "OFFSITE_CONVERSIONS",
                "label": "Maximize conversions",
                "description": "Show ads to people most likely to take action on your site.",
                "recommended": True
            },
            {
                "value": "VALUE",
                "label": "Maximize conversion value",
                "description": "Optimize for higher value purchases."
            },
            {
                "value": "LANDING_PAGE_VIEWS",
                "label": "Maximize landing page views",
                "description": "Optimize for site visits."
            },
            {
                "value": "LINK_CLICKS",
                "label": "Maximize link clicks",
                "description": "Optimize for ad clicks."
            }
        ],

        "app": [
            {
                "value": "APP_INSTALLS",
                "label": "Maximize app installs",
                "description": "Show ads to people most likely to install your app."
            },
            {
                "value": "APP_EVENTS",
                "label": "Maximize in-app events",
                "description": "Optimize for actions inside your app."
            }
        ],

        "whatsapp": [
            {
                "value": "WHATSAPP_MESSAGE",
                "label": "Maximize WhatsApp messages",
                "description": "Show ads to people likely to chat with you."
            }
        ]
    }

    goals = PERFORMANCE_GOALS.get(location, PERFORMANCE_GOALS["website"])

    return jsonify({
        "location": location,
        "goals": goals
    })


# --------------------------------
# 3️⃣ Datasets / Pixels
# --------------------------------
@meta_bp.route("/api/meta/datasets", methods=["GET"])
def get_meta_datasets():

    workspace_id = request.args.get("workspace_id")
    user_id = request.args.get("user_id")

    if not workspace_id or not user_id:
        return jsonify({"error": "workspace_id and user_id required"}), 400

    account = get_meta_account(workspace_id, user_id)

    if not account or not account.access_token:
        return jsonify({"error": "Meta account not connected"}), 400

    access_token = account.access_token
    ad_account_id = account.ad_account_id

    params = {
        "access_token": access_token,
        "fields": "id,name"
    }

    # Try new datasets API
    url = f"{GRAPH_BASE}/{ad_account_id if ad_account_id.startswith('act_') else 'act_'+ad_account_id}/conversions_datasets"

    response, error = meta_get(url, params)

    if error:
        # Fallback to legacy pixels if datasets API fails
        url = f"{GRAPH_BASE}/{ad_account_id if ad_account_id.startswith('act_') else 'act_'+ad_account_id}/adspixels"
        response, error = meta_get(url, params)
        if error:
             return jsonify({"error": error}), 400

    datasets = response.get("data", [])

    formatted = [
        {
            "id": d["id"],
            "name": d.get("name", f"Dataset {d['id']}")
        }
        for d in datasets
    ]

    return jsonify({"datasets": formatted})


# --------------------------------
# 4️⃣ Conversion Events
# --------------------------------
@meta_bp.route("/api/meta/conversion-events", methods=["GET"])
def get_conversion_events():

    workspace_id = request.args.get("workspace_id")
    user_id = request.args.get("user_id")
    dataset_id = request.args.get("dataset_id")

    if not workspace_id or not user_id:
        return jsonify({"error": "workspace_id and user_id required"}), 400

    if not dataset_id:
        return jsonify({"error": "dataset_id required"}), 400

    account = get_meta_account(workspace_id, user_id)

    if not account:
        return jsonify({"error": "Meta account not found"}), 400

    access_token = account.access_token

    stats_url = f"{GRAPH_BASE}/{dataset_id}/stats"

    params = {
        "access_token": access_token
    }

    stats_res, error = meta_get(stats_url, params)

    if error:
        return jsonify({"error": error}), 400

    STANDARD_EVENTS = [
        "Purchase",
        "Lead",
        "AddToCart",
        "InitiateCheckout",
        "ViewContent",
        "CompleteRegistration"
    ]

    events = set(STANDARD_EVENTS)

    for stat in stats_res.get("data", []):
        if "event" in stat:
            events.add(stat["event"])

    formatted = [
        {"value": e, "label": e}
        for e in sorted(events)
    ]

    return jsonify({"events": formatted})
