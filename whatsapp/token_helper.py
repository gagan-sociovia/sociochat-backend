"""
WhatsApp Token Helper - Centralized Access Token Retrieval
==========================================================

Provides a single source of truth for getting WhatsApp access tokens.
Use this module throughout the codebase to ensure consistent token handling.

Usage:
    from whatsapp.token_helper import get_account_with_token, get_valid_account_for_workspace

    # Get account by ID (with token validation)
    account, error = get_account_with_token(account_id)
    if error:
        return jsonify({"success": False, "error": error}), 400
    
    # Get a working account for a workspace
    account, error = get_valid_account_for_workspace(workspace_id)
"""

import logging
from typing import Tuple, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def get_account_with_token(account_id: int) -> Tuple[Optional['WhatsAppAccount'], Optional[str]]:
    """
    Get a WhatsApp account by ID and verify it has a valid token.
    
    Args:
        account_id: The account ID to fetch
        
    Returns:
        Tuple of (account, error_message)
        - If successful: (account, None)
        - If failed: (None, error_message)
    """
    from .models import WhatsAppAccount
    
    account = WhatsAppAccount.query.get(account_id)
    
    if not account:
        return None, "Account not found"
    
    if not account.is_active:
        # Try to find an alternative active account in the same workspace
        alt_account = WhatsAppAccount.query.filter_by(
            workspace_id=account.workspace_id,
            is_active=True
        ).filter(
            WhatsAppAccount.access_token_encrypted.isnot(None)
        ).first()
        
        if alt_account:
            logger.info(f"Account {account_id} is inactive, using alternative account {alt_account.id}")
            account = alt_account
        else:
            return None, "Account is inactive. Please reconnect your WhatsApp Business Account."
    
    access_token = account.get_access_token()
    if not access_token:
        # Try to find an alternative account with token
        alt_account = WhatsAppAccount.query.filter_by(
            workspace_id=account.workspace_id,
            is_active=True
        ).filter(
            WhatsAppAccount.access_token_encrypted.isnot(None)
        ).first()
        
        if alt_account:
            logger.info(f"Account {account_id} has no token, using alternative account {alt_account.id}")
            return alt_account, None
        
        return None, "Account has no access token. Please reconnect your WhatsApp Business Account."
    
    # Check token expiry if available
    if account.token_expires_at:
        if account.token_expires_at < datetime.now(timezone.utc):
            return None, "Access token has expired. Please reconnect your WhatsApp Business Account."
    
    return account, None


def get_valid_account_for_workspace(workspace_id: str) -> Tuple[Optional['WhatsAppAccount'], Optional[str]]:
    """
    Get a valid WhatsApp account for a workspace.
    Returns the first active account with a valid token.
    
    Args:
        workspace_id: The workspace ID
        
    Returns:
        Tuple of (account, error_message)
    """
    from .models import WhatsAppAccount
    
    account = WhatsAppAccount.query.filter_by(
        workspace_id=workspace_id,
        is_active=True
    ).filter(
        WhatsAppAccount.access_token_encrypted.isnot(None)
    ).first()
    
    if not account:
        return None, "No active WhatsApp account found for this workspace. Please connect a WhatsApp Business Account."
    
    access_token = account.get_access_token()
    if not access_token:
        return None, "Account has no access token. Please reconnect your WhatsApp Business Account."
    
    return account, None


def get_token_for_account(account_id: int) -> Tuple[Optional[str], Optional[str]]:
    """
    Get just the access token for an account (convenience method).
    
    Args:
        account_id: The account ID
        
    Returns:
        Tuple of (token, error_message)
    """
    account, error = get_account_with_token(account_id)
    if error:
        return None, error
    return account.get_access_token(), None


def migrate_resources_to_active_account(old_account_id: int, workspace_id: str) -> dict:
    """
    Migrate flows and templates from an inactive account to the active one.
    
    Args:
        old_account_id: The inactive account ID
        workspace_id: The workspace ID
        
    Returns:
        dict with migration results
    """
    from .models import WhatsAppAccount, WhatsAppFlow, WhatsAppTemplate
    from models import db
    
    # Find active account
    active_account = WhatsAppAccount.query.filter_by(
        workspace_id=workspace_id,
        is_active=True
    ).filter(
        WhatsAppAccount.access_token_encrypted.isnot(None)
    ).first()
    
    if not active_account:
        return {"success": False, "error": "No active account found"}
    
    if active_account.id == old_account_id:
        return {"success": True, "message": "Account is already active", "migrated_flows": 0, "migrated_templates": 0}
    
    # Migrate flows
    flows_migrated = WhatsAppFlow.query.filter_by(account_id=old_account_id).update(
        {"account_id": active_account.id}
    )
    
    # Migrate templates
    templates_migrated = WhatsAppTemplate.query.filter_by(account_id=old_account_id).update(
        {"account_id": active_account.id}
    )
    
    db.session.commit()
    
    logger.info(f"Migrated {flows_migrated} flows and {templates_migrated} templates from account {old_account_id} to {active_account.id}")
    
    return {
        "success": True,
        "old_account_id": old_account_id,
        "new_account_id": active_account.id,
        "migrated_flows": flows_migrated,
        "migrated_templates": templates_migrated
    }
