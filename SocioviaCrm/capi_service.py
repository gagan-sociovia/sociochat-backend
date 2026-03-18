import os
import time
import uuid
import hashlib
import traceback
import requests
from datetime import datetime
from flask import current_app

def _hash_data(data):
    if not data:
        return None
    return hashlib.sha256(str(data).encode('utf-8').strip().lower()).hexdigest()

def send_capi_event(event_name, user_data_dict, custom_data_dict=None, workspace_id=None, action_source="system_generated", event_source_url=None):
    """
    Sends an event to Meta Conversions API and logs it to CapiEvent DB table.
    
    :param event_name: e.g., 'Lead', 'Purchase', 'QualifiedLead'
    :param user_data_dict: dict containing 'email', 'phone', 'first_name', 'last_name', 'client_ip_address', 'client_user_agent', etc.
    :param custom_data_dict: dict containing 'value', 'currency', etc.
    :param workspace_id: The workspace ID this event belongs to.
    :param action_source: Source of action, e.g., 'website', 'system_generated', 'business_messaging'
    :param event_source_url: Custom URL if action_source is 'website'
    :return: dict with success status and event_id
    """
    try:
        pixel_id = None
        access_token = None
        
        # 1. Try fetching from Database if workspace_id provided
        if workspace_id and current_app:
            try:
                # Import directly if current_app.crm_models is missing
                from models import CapiAccount
                
                # Force workspace_id to string since DB column might be varchar
                str_workspace_id = str(workspace_id)
                account = CapiAccount.query.filter_by(workspace_id=str_workspace_id).first()
                if account and account.pixel_id and account.system_user_token:
                    pixel_id = account.pixel_id
                    access_token = account.system_user_token
                else:
                    current_app.logger.warning(f"CapiAccount found but missing pixel/token, or no account found for workspace_id={str_workspace_id}")
                    
                # 1b. Fallback to SocialAccount if CapiAccount token is missing
                if pixel_id and not access_token:
                    from models import SocialAccount
                    sa = SocialAccount.query.filter_by(workspace_id=str(workspace_id)).order_by(SocialAccount.created_at.desc()).first()
                    if sa and sa.access_token:
                        access_token = sa.access_token
                        current_app.logger.info(f"CAPI Fallback: Using SocialAccount token for workspace {workspace_id}")

                # 1c. Last resort: Discover Pixel using the token if still missing
                if access_token and not pixel_id:
                    try:
                        from MetaHelpers.GetWorkspaceData import discover_ad_accounts_for_token
                        ad_accs = discover_ad_accounts_for_token(access_token)
                        if ad_accs:
                            aid = ad_accs[0]
                            if not str(aid).startswith("act_"): aid = f"act_{aid}"
                            v = os.environ.get("FB_API_VERSION", "v21.0")
                            px_resp = requests.get(f"https://graph.facebook.com/{v}/{aid}/adspixels", 
                                                 params={"access_token": access_token}, timeout=5)
                            if px_resp.ok:
                                px_data = px_resp.json().get("data", [])
                                if px_data:
                                    pixel_id = px_data[0]["id"]
                                    current_app.logger.info(f"CAPI Fallback: Discovered Pixel {pixel_id} via account {aid}")
                    except Exception as px_e:
                        current_app.logger.debug(f"Pixel discovery fallback failed: {px_e}")

            except Exception as e:
                current_app.logger.error(f"Failed to fetch CapiAccount/SocialAccount from DB: {str(e)}")

        # 2. Prevent Fallback to Environment Variables (User requested strictly from DB)
        if not pixel_id or not access_token:
            if current_app:
                current_app.logger.warning(f"CAPI Event '{event_name}' skipped: FB_PIXEL_ID or token missing from DB & env.")
            # Still log locally as failed due to configuration
            return _log_to_db(
                workspace_id=workspace_id,
                event_name=event_name,
                action_source=action_source,
                event_source_url=event_source_url,
                user_data_json=user_data_dict,
                custom_data_json=custom_data_dict,
                status='failed',
                error_message="Missing FB_PIXEL_ID or FB_CAPI_TOKEN in Database and environment variables."
            )

        # Skip FacebookAdsApi init
        # FacebookAdsApi.init(access_token=access_token)

        # Build User Data (Hash PII)
        emails = [_hash_data(user_data_dict.get("email"))] if user_data_dict.get("email") else []
        phones = [_hash_data(user_data_dict.get("phone"))] if user_data_dict.get("phone") else []
        
        # Determine names
        first_name = user_data_dict.get("first_name")
        last_name = user_data_dict.get("last_name")
        full_name = user_data_dict.get("name")
        if full_name and not first_name and not last_name:
            parts = full_name.split(" ", 1)
            first_name = parts[0]
            if len(parts) > 1:
                last_name = parts[1]

        fnp = [_hash_data(first_name)] if first_name else []
        lnp = [_hash_data(last_name)] if last_name else []

        user_data = {}
        if emails:
            user_data["em"] = emails
        if phones:
            user_data["ph"] = phones
        if fnp:
            user_data["fn"] = fnp
        if lnp:
            user_data["ln"] = lnp
            
        client_ip = user_data_dict.get("client_ip_address") or user_data_dict.get("ip")
        if client_ip:
            user_data["client_ip_address"] = str(client_ip)
            
        client_ua = user_data_dict.get("client_user_agent") or user_data_dict.get("user_agent")
        if client_ua:
            user_data["client_user_agent"] = str(client_ua)
            
        if user_data_dict.get("fbc"):
            user_data["fbc"] = str(user_data_dict.get("fbc"))
        if user_data_dict.get("fbp"):
            user_data["fbp"] = str(user_data_dict.get("fbp"))
            
        if user_data_dict.get("lead_id"):
            user_data["lead_id"] = str(user_data_dict.get("lead_id"))

        # Build Custom Data
        if custom_data_dict is None:
            custom_data_dict = {}
            
        # Meta Conversions API for CRM requires these fields in Custom Data directly
        # Only inject for non-website (CRM) events; pixel events shouldn't have these
        if action_source != "website":
            custom_data_dict["event_source"] = "crm"
            custom_data_dict["lead_event_source"] = "Sociovia CRM"
        
        event_time_unix = int(time.time())
        event_id = str(uuid.uuid4())

        # Build Final Payload
        payload = {
            "data": [
                {
                    "event_name": event_name,
                    "event_time": event_time_unix,
                    "action_source": action_source,
                    "event_id": event_id,
                    "user_data": user_data,
                    "custom_data": custom_data_dict
                }
            ]
        }
        if event_source_url:
            payload["data"][0]["event_source_url"] = event_source_url

        # Execute API Call
        api_version = os.environ.get("FB_API_VERSION", "v21.0")
        url = f"https://graph.facebook.com/{api_version}/{pixel_id}/events"
        
        response = requests.post(url, params={"access_token": access_token}, json=payload, timeout=10)
        re_json = response.json()
        
        if response.status_code >= 400:
            error_data = re_json.get("error", {})
            error_msg = error_data.get("message", "Unknown Facebook API Error")
            error_code = error_data.get("code")
            error_subcode = error_data.get("error_subcode")
            
            # User-friendly error for revoked tokens
            if error_code == 190:
                current_app.logger.warning(f"CAPI Token Error (Workspce {workspace_id}): {error_msg}")
                raise Exception(f"Facebook account needs re-authorization. Please go to Settings > Conversions API and reconnect your Meta account. (Internal Error: {error_msg})")
                
            raise Exception(f"Facebook Graph API Error: {error_msg} (Code: {error_code}, Subcode: {error_subcode})")
        
        # Save payload status
        return _log_to_db(
            workspace_id=workspace_id,
            event_name=event_name,
            pixel_id=pixel_id,
            action_source=action_source,
            event_source_url=event_source_url,
            user_data_json=user_data_dict,
            custom_data_json=custom_data_dict,
            api_response=re_json,
            status='sent',
            ext_event_id=event_id
        )

    except Exception as e:
        err_str = str(e)
        # Skip full traceback for user-friendly auth/config errors
        is_user_friendly = "re-authorization" in err_str or "Missing FB_PIXEL_ID" in err_str
        
        if current_app:
            current_app.logger.error(f"Failed to send CAPI event {event_name}: {err_str}")
        return _log_to_db(
            workspace_id=workspace_id,
            event_name=event_name,
            pixel_id=pixel_id,
            action_source=action_source,
            event_source_url=event_source_url,
            user_data_json=user_data_dict,
            custom_data_json=custom_data_dict,
            error_message=err_str if is_user_friendly else traceback.format_exc(),
            status='failed'
        )

def _log_to_db(workspace_id, event_name, action_source, event_source_url, user_data_json, custom_data_json, status, pixel_id=None, api_response=None, error_message=None, ext_event_id=None):
    """
    Logs the event to the CapiEvent table.
    Provides the required 'em', 'ph', 'ip', 'user_agent' keys so the /quality API
    can grade it appropriately.
    """
    if not current_app:
        return {"success": status == 'sent', "error": error_message}
        
    db = current_app.db
    CapiEvent = current_app.crm_models.get("CapiEvent")
    if not CapiEvent or not workspace_id:
        if current_app:
            current_app.logger.warning("CapiEvent model or workspace_id missing. Could not log.")
        return {"success": status == 'sent', "error": error_message}

    try:
        event_id = ext_event_id or str(uuid.uuid4())

        # Safely extract minimal structured data for the quality checker
        safe_user_data = {
            "em": user_data_json.get("email"),
            "ph": user_data_json.get("phone"),
            "client_ip_address": user_data_json.get("client_ip_address") or user_data_json.get("ip"),
            "client_user_agent": user_data_json.get("client_user_agent") or user_data_json.get("user_agent")
        }

        record = CapiEvent(
            workspace_id=int(workspace_id) if workspace_id and str(workspace_id).isdigit() else workspace_id,
            event_id=event_id,
            event_name=event_name,
            pixel_id=pixel_id,
            event_time=datetime.utcnow(),
            event_source_url=event_source_url,
            action_source=action_source or "system_generated",
            user_data_json=safe_user_data,
            custom_data_json=custom_data_json,
            status=status,
            api_response=api_response,
            error_message=error_message,
            sent_at=datetime.utcnow() if status == 'sent' else None
        )
        db.session.add(record)
        db.session.commit()
        result = {"success": status == 'sent', "event_id": event_id, "id": record.id}
        if error_message:
            result["error"] = error_message
        return result
    except Exception as e:
        if current_app:
            current_app.logger.error(f"Failed to log CapiEvent to DB: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"success": False, "error": str(e)}
