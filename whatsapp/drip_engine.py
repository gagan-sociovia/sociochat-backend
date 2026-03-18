import logging
import json
import re
from datetime import datetime, timezone, timedelta
from models import db
from .models import WhatsAppAccount
from .drip_models import WhatsAppDripCampaign, WhatsAppDripStep, WhatsAppDripEnrollment
from .services import WhatsAppService

logger = logging.getLogger(__name__)


def extract_step_params(variables: dict, step_order: int) -> list:
    """
    Extract template parameters for a specific step from enrollment variables.
    
    Supports two naming conventions:
    1. Named: step_1_Name, step_1_OrderID, step_1_Link -> ["value1", "value2", "value3"]
    2. Numeric: step_1_1, step_1_2, step_1_3 -> ["value1", "value2", "value3"]
    
    Returns list of parameter values in order.
    """
    if not variables:
        return []
    
    params = []
    prefix = f"step_{step_order}_"
    
    # Collect all columns that match this step
    step_columns = {}
    for key, value in variables.items():
        if key.lower().startswith(prefix.lower()):
            suffix = key[len(prefix):]
            step_columns[suffix] = str(value) if value else ""
    
    # If no step-specific columns found, and this is step 1 (common for bulk),
    # try looking for direct numeric keys ("1", "2", "3") or generic keys
    # This supports the Bulk Messaging UI which sends params as {"1": "val", "2": "val"}
    if not step_columns and step_order == 1:
        # Check for direct numeric keys
        numeric_params = {}
        for key, value in variables.items():
            # If key is digit ("1", "2")
            if key.isdigit():
                 numeric_params[int(key)] = str(value)
            # If key is like "param_1" (CSV upload often gives this)
            elif key.startswith("param_") and key[6:].isdigit():
                 numeric_params[int(key[6:])] = str(value)
        
        # If we found numeric params, sort by number and return in order
        if numeric_params:
            for i in sorted(numeric_params.keys()):
                params.append(numeric_params[i])
            return params
    
    # Otherwise, use named columns in alphabetical order
    # This preserves a predictable order for columns like Name, OrderID, Link
    # Only if we found step_columns
    if step_columns:
        for suffix in sorted(step_columns.keys()):
            params.append(step_columns[suffix])
    
    # Fallback: if we still have nothing, and variables has items, maybe just return values in order?
    # Risky if random keys, so better to rely on mapping or above logic.
    
    return params


def extract_step_params_dict(variables: dict, step_order: int) -> dict:
    """
    Extract named template parameters for a specific step.
    
    Returns dictionary {param_name: value}
    
    Supports:
    1. step_1_name, step_1_order_id -> {"name": value, "order_id": value}
    2. Direct keys for step 1: {"1": value, "2": value} or {"name": value}
    """
    if not variables:
        return {}
    
    params = {}
    prefix = f"step_{step_order}_"
    
    # Collect all columns that match this step (step_N_ prefix)
    for key, value in variables.items():
        if key.lower().startswith(prefix.lower()):
            param_name = key[len(prefix):]
            params[param_name] = str(value) if value else ""
    
    # If no step-specific columns found and this is step 1 (common for bulk),
    # use direct keys from variables
    if not params and step_order == 1:
        for key, value in variables.items():
            # Skip internal keys
            if key.startswith("_"):
                continue
            params[key] = str(value) if value else ""
            
    return params


def process_drip_campaigns():
    """
    Main scheduler function to process due drip steps.
    Should be called every minute by APScheduler or test.py scheduler.

    Uses FOR UPDATE SKIP LOCKED so multiple Gunicorn workers can run
    this concurrently without processing the same enrollment twice.
    """
    try:
        now = datetime.now(timezone.utc)

        # 1. Fetch due enrollments — SKIP LOCKED prevents duplicate
        #    processing across multiple Gunicorn workers.
        #    Each worker claims unclaimed rows; already-locked rows are skipped.
        #    We fetch one by one in a loop or process as a batch but with careful locking.
        
        # Strategy: Fetch IDs of due enrollments first, then process each individually
        # to keep transactions short and locks granular.
        due_enrollments = (
            WhatsAppDripEnrollment.query
            .filter(
                WhatsAppDripEnrollment.status == "active",
                WhatsAppDripEnrollment.next_run_at <= now
            )
            .with_for_update(skip_locked=True)
            .limit(50)
            .all()
        )

        if not due_enrollments:
            return

        # Immediately mark them as 'processing' or a temporary state if we had one,
        # but since we don't want to change schema right now, we'll process them
        # in the current transaction or one-by-one with fresh locks.
        
        # To avoid the 'commit releases all locks' issue:
        # We process each enrollment in a nested-like fashion or just be careful.
        # The best way in SQLAlchemy for this is to keep the IDs and re-fetch with lock
        # inside the loop with its own commit.
        
        enrollment_ids = [e.id for e in due_enrollments]
        # Release the initial batch lock by committing (we will re-lock individually)
        db.session.commit()

        logger.info(f"Processing {len(enrollment_ids)} drip enrollments...")

        for eid in enrollment_ids:
            try:
                # Re-fetch with lock for THIS specific row
                enrollment = WhatsAppDripEnrollment.query.with_for_update(skip_locked=True).get(eid)
                if not enrollment or enrollment.status != "active":
                    continue

                # Check campaign status before processing
                campaign = WhatsAppDripCampaign.query.get(enrollment.campaign_id)
                # Skip if not active/running (e.g. scheduled, drafted, paused)
                if not campaign or campaign.status not in ["active", "running"]:
                    continue

                process_single_enrollment(enrollment)
                # Commit releases only THIS row's lock
                db.session.commit()
            except Exception as e:
                logger.error(f"Failed to process enrollment {eid}: {e}")
                db.session.rollback()

    except Exception as e:
        logger.exception(f"Drip scheduler error: {e}")
        db.session.rollback()
    finally:
        db.session.remove()


# Note: Duplicate process_single_enrollment removed for production readiness.
# The advanced version starting at line 270 is the one being used.


def trigger_campaign_now(campaign_id):
    logger.info(f"___TRIGGER_CAMPAIGN_NOW called for ID {campaign_id}___")
    try:
        from .drip_models import WhatsAppDripCampaign, WhatsAppDripEnrollment
        campaign = (
            WhatsAppDripCampaign.query
            .filter_by(id=campaign_id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found or already being processed")
            return

        # If it was scheduled, mark it running
        logger.info(f"Campaign {campaign_id} STATUS is {campaign.status}")
        if campaign.status == 'scheduled':
            campaign.status = 'running'
            campaign.trigger_value = None # Clear scheduled time
            db.session.commit()
            logger.info(f"Campaign {campaign_id} marked as RUNNING")
        elif campaign.status == 'running':
            # Already running (from Send Now), just clear trigger_value if set
            if campaign.trigger_value:
                campaign.trigger_value = None
                db.session.commit()
            logger.info(f"Campaign {campaign_id} already RUNNING, processing enrollments...")
        else:
            # If it's drafted or something else, we might still want to commit the lock release if we checked it
            db.session.commit()

        # Fetch IDs and process individually to avoid the 'commit releases all' lock issue
        enrollments = WhatsAppDripEnrollment.query.filter_by(
            campaign_id=campaign_id,
            status="active"
        ).all()
        
        enrollment_ids = [e.id for e in enrollments]
        logger.info(f"Found {len(enrollment_ids)} active enrollments for campaign {campaign_id}")
        
        for eid in enrollment_ids:
            try:
                enrollment = (
                    WhatsAppDripEnrollment.query
                    .filter_by(id=eid, status="active")
                    .with_for_update(skip_locked=True)
                    .first()
                )
                if not enrollment:
                    continue
                    
                logger.info(f"Processing enrollment {enrollment.id} (Phone: {enrollment.phone_number})...")
                process_single_enrollment(enrollment)
                db.session.commit()
            except Exception as e:
                logger.error(f"Failed to process enrollment {eid}: {e}")
                db.session.rollback()
                
    except Exception as e:
        logger.exception(f"Error triggering campaign {campaign_id}: {e}")
        db.session.rollback()
    finally:
        db.session.remove()



def process_single_enrollment(enrollment: WhatsAppDripEnrollment):
    # Defensive Check: Verify it hasn't been processed by another worker
    if enrollment.status != "active":
        logger.info(f"DRIP ENGINE - Skipping enrollment {enrollment.id} because status is {enrollment.status}")
        return

    campaign = WhatsAppDripCampaign.query.get(enrollment.campaign_id)
    if not campaign or campaign.status not in ["active", "running"]:
        # Pause enrollment if campaign paused/deleted
        enrollment.status = "paused"
        enrollment.status_reason = f"Campaign status {campaign.status} not active/running"
        return

    # Determine next step
    next_step_order = enrollment.current_step_order + 1
    
    step = WhatsAppDripStep.query.filter_by(
         campaign_id=enrollment.campaign_id,
         step_order=next_step_order
    ).first()
    
    if not step:
         # No more steps -> Complete
         print(f"DEBUG: Enrollment {enrollment.id}: No step found for order {next_step_order}. Marking completed.")
         enrollment.status = "completed"
         enrollment.next_run_at = None
         campaign.completed_count = (campaign.completed_count or 0) + 1
         return

    # Get account for sending
    account = WhatsAppAccount.query.get(campaign.account_id)
    if not account:
        logger.error(f"Account {campaign.account_id} not found for drip {campaign.id}")
        enrollment.status = "failed"
        enrollment.status_reason = f"Account {campaign.account_id} not found"
        return
    
    # Extract template parameters from variables
    variables = enrollment.variables or {}
    logger.info(f"DRIP ENGINE - Enrollment {enrollment.id} Variables: {json.dumps(variables) if variables else 'None'}") # DEBUG LOG
    
    # Check if template uses named parameters
    # We need to fetch the template to know its schema
    from .models import WhatsAppTemplate
    template_record = WhatsAppTemplate.query.filter_by(
        name=step.template_name,
        language=step.language
    ).first()
    
    is_named_template = False
    if template_record and template_record.components:
        # Check components schema for named params or named usage in example
        # This is a heuristic - ideally we'd store a flag
        components_str = str(template_record.components) # Lazy check
        if "parameter_name" in components_str or "body_text_named_params" in components_str:
            is_named_template = True
            
    # Fallback: Check body text for named variables (non-numeric like {{name}})
    if template_record and not is_named_template and template_record.body_text:
        # Matches {{name}}, {{first_name}} but NOT {{1}}, {{12}}
        if re.search(r'\{\{\s*[a-zA-Z_]+\w*\s*\}\}', template_record.body_text):
            is_named_template = True
            
    components = []
    params = [] # Initialize params list for audit log
    
    # Extract Header (Image/Video/Document/Text)
    header_image = variables.get("header_image_url")
    header_video = variables.get("header_video_url")
    header_document = variables.get("header_document_url")
    header_text = variables.get("header_text")
    
    if header_image:
        components.append({
            "type": "header",
            "parameters": [{"type": "image", "image": {"link": str(header_image)}}]
        })
    elif header_video:
        components.append({
            "type": "header",
            "parameters": [{"type": "video", "video": {"link": str(header_video)}}]
        })
    elif header_document:
        components.append({
            "type": "header",
            "parameters": [{"type": "document", "document": {"link": str(header_document)}}]
        })
    elif header_text:
        components.append({
            "type": "header",
            "parameters": [{"type": "text", "text": str(header_text)}]
        })
    
    if is_named_template and template_record:
        # NAMED PARAMETERS LOGIC
        # 1. Extract raw variables for this step (e.g. {"var_1": "John", "var_2": "123"} or {"name": "John"})
        raw_step_params = extract_step_params_dict(variables, step.step_order)
        
        # 2. Determine expected parameter names from template body
        # Parse body text to find {{name}}, {{order_id}} in order
        expected_params = []
        if template_record.body_text:
            # Regex to find {{...}}
            matches = re.findall(r'\{\{([^}]+)\}\}', template_record.body_text)
            # Dedup while preserving order
            seen = set()
            for m in matches:
                clean_m = m.strip()
                if clean_m not in seen:
                    expected_params.append(clean_m)
                    seen.add(clean_m)
        
        # 3. Map raw params to expected names - ROBUST STRATEGY
        final_named_params = {}
        used_raw_keys = set()
        
        # Pass 1: Exact Name Matches
        for key, val in raw_step_params.items():
            if key in expected_params:
                final_named_params[key] = val
                used_raw_keys.add(key)
                
        # Pass 2: Explicit numbered keys (e.g. "1", "var_1") mapped to corresponding expected param index
        for key, val in raw_step_params.items():
            if key in used_raw_keys:
                continue
                
            match = re.search(r'^(\d+)$|^var_(\d+)$|^variable_(\d+)$', key.lower())
            if match:
                # Extract the number
                num_str = next((g for g in match.groups() if g), None)
                if num_str:
                    idx = int(num_str) - 1 # 1-based to 0-based
                    if 0 <= idx < len(expected_params):
                        target_param = expected_params[idx]
                        if target_param not in final_named_params:
                            final_named_params[target_param] = val
                            used_raw_keys.add(key)
        
        logger.info(f"Step {step.step_order} PARAM MAPPING: Raw={raw_step_params.keys()} -> Expected={expected_params} -> Final={final_named_params}")
        
        if final_named_params:
            # Build body component with parameter_name
            body_parameters = []
            for name, value in final_named_params.items():
                body_parameters.append({
                    "type": "text",
                    "parameter_name": name,
                    "text": str(value)
                })
                params.append(f"{name}={value}") # For audit log
                
            components.append({"type": "body", "parameters": body_parameters})
            
    else:
        # POSITIONAL PARAMETERS LOGIC (Legacy)
        params = extract_step_params(variables, step.step_order)
        logger.info(f"Step {step.step_order} POSITIONAL params extracted: {params}")
        
        if params:
            # Build standard body parameters
            body_parameters = [{"type": "text", "text": str(p)} for p in params]
            components.append({"type": "body", "parameters": body_parameters})
    
    # Store what we're sending for audit
    enrollment.last_sent_params = {
        "step": step.step_order,
        "template": step.template_name,
        "params": params,
        "sent_at": datetime.now(timezone.utc).isoformat()
    }
        
    service = WhatsAppService(db.session, account.phone_number_id, account.get_access_token())
    
    # Send Template with components
    result = service.send_template(
        to=enrollment.phone_number,
        template_name=step.template_name,
        language_code=step.language,
        components=components,
        campaign_id=campaign.id,
    )
    
    if result.get("success"):
        # Advance state
        enrollment.current_step_order = step.step_order
        enrollment.status_reason = None  # Clear any previous error
        
        # Calculate next run time based on NEXT step's delay
        next_next_step = WhatsAppDripStep.query.filter_by(
             campaign_id=enrollment.campaign_id,
             step_order=step.step_order + 1
        ).first()
        
        if next_next_step:
            enrollment.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=next_next_step.delay_seconds)
        else:
            # No next step, mark complete
            enrollment.status = "completed"
            enrollment.next_run_at = None
            campaign.completed_count = (campaign.completed_count or 0) + 1
            
            # CHECK FOR CAMPAIGN COMPLETION
            # If all enrollments are either completed or failed, mark campaign as completed
            # This handles the "Auto-complete" requirement
            total = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign.id).count()
            finished = WhatsAppDripEnrollment.query.filter(
                WhatsAppDripEnrollment.campaign_id == campaign.id,
                WhatsAppDripEnrollment.status.in_(["completed", "failed", "blocked_missing_data"])
            ).count()
            
            if finished >= total and total > 0:
                campaign.status = "completed"
                logger.info(f"Campaign {campaign.id} COMPLETED (All {total} enrollments finished)")

        logger.info(f"[DRIP] Campaign {campaign.id} Step {step.step_order} sent to {enrollment.phone_number}")
        
    else:
        error_msg = result.get("error", "Unknown error")
        logger.error(f"Drip send failed: {result}")
        
        # Check if it's a parameter mismatch error
        if "132000" in str(result.get("error_code", "")) or "params" in error_msg.lower():
            enrollment.status = "blocked_missing_data"
            enrollment.status_reason = f"Template parameter mismatch: {error_msg}"
            logger.warning(f"Enrollment {enrollment.id} blocked - missing data for template")
        else:
            # Other error - retry later
            enrollment.next_run_at = datetime.now(timezone.utc) + timedelta(hours=1)
            enrollment.status_reason = f"Send failed: {error_msg}"


def process_due_drip_enrollments():
    """
    Process all drip enrollments that are due for their next step.
    This should be called periodically (e.g., every minute) by the scheduler.
    
    Finds enrollments where:
    - status = 'active'
    - next_run_at <= now
    - campaign is active or running

    Uses FOR UPDATE SKIP LOCKED so multiple Gunicorn workers can run
    this concurrently without processing the same enrollment twice.
    """
    try:
        now = datetime.now(timezone.utc)
        
        # Find all due enrollments — SKIP LOCKED prevents duplicates
        due_enrollments = (
            WhatsAppDripEnrollment.query
            .filter(
                WhatsAppDripEnrollment.status == "active",
                WhatsAppDripEnrollment.next_run_at != None,
                WhatsAppDripEnrollment.next_run_at <= now
            )
            .with_for_update(skip_locked=True)
            .limit(100)
            .all()
        )
        
        if not due_enrollments:
            return {"processed": 0, "success": 0, "failed": 0}
        
        enrollment_ids = [e.id for e in due_enrollments]
        db.session.commit() # Release batch lock

        logger.info(f"[DRIP_PROCESSOR] Found {len(enrollment_ids)} due enrollments to process")
        
        processed = 0
        success_count = 0
        failed_count = 0
        
        for eid in enrollment_ids:
            try:
                enrollment = (
                    WhatsAppDripEnrollment.query
                    .filter_by(id=eid, status="active")
                    .with_for_update(skip_locked=True)
                    .first()
                )
                if not enrollment:
                    continue

                # Check if campaign is still active
                campaign = WhatsAppDripCampaign.query.get(enrollment.campaign_id)
                if not campaign or campaign.status not in ["active", "running"]:
                    logger.debug(f"Skipping enrollment {enrollment.id} - campaign not active")
                    continue
                
                logger.info(f"[DRIP_PROCESSOR] Processing enrollment {enrollment.id} (Phone: {enrollment.phone_number})")
                process_single_enrollment(enrollment)
                processed += 1
                
                # Check result
                if enrollment.status in ["completed", "active"]:
                    success_count += 1
                else:
                    failed_count += 1
                
                # Commit per-enrollment so row locks are released quickly
                db.session.commit()
                    
            except Exception as e:
                logger.error(f"[DRIP_PROCESSOR] Failed to process enrollment {eid}: {e}")
                db.session.rollback()
                failed_count += 1
        
        logger.info(f"[DRIP_PROCESSOR] Completed: {processed} processed, {success_count} success, {failed_count} failed")
        return {"processed": processed, "success": success_count, "failed": failed_count}
        
    except Exception as e:
        logger.exception(f"[DRIP_PROCESSOR] Error processing due enrollments: {e}")
        db.session.rollback()
        return {"processed": 0, "success": 0, "failed": 0, "error": str(e)}
    finally:
        db.session.remove()
