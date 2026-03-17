"""
Interactive Automation Execution Engine
========================================

Executes interactive automation flows when users send messages or click buttons.

This engine:
1. Checks if any active interactive automation matches the incoming message trigger
2. Tracks conversation state (which node the user is at)
3. Sends interactive messages with buttons OR approved WhatsApp templates
4. Handles button click responses and navigates to the next node
5. Supports template chaining (template → template → message → end)
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List

from models import db
from .visual_automation_models import WhatsAppVisualAutomation, WhatsAppConversationState
from .models import WhatsAppAccount, WhatsAppConversation, WhatsAppMessage, WhatsAppTemplate
from .services import WhatsAppService
from notifications import notification_manager

logger = logging.getLogger(__name__)

# Node types that can be sent as messages in the flow
SENDABLE_NODE_TYPES = {"message", "template"}


class InteractiveAutomationEngine:
    """
    Executes interactive automation flows for WhatsApp conversations.
    """
    
    def __init__(self, account_id: int, workspace_id: str):
        """
        Initialize the engine for a specific account.
        
        Args:
            account_id: WhatsApp account ID
            workspace_id: Workspace ID for multi-tenant isolation
        """
        self.account_id = account_id
        self.workspace_id = str(workspace_id)
    
    def process_incoming_message(
        self,
        message_text: str,
        conversation_id: int,
        from_phone: str,
        is_button_reply: bool = False,
        button_payload: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Process an incoming message against interactive automations.
        
        Args:
            message_text: Text content of the message
            conversation_id: Conversation ID
            from_phone: Sender's phone number
            is_button_reply: True if this is a button reply
            button_payload: Button payload/ID if button reply
            
        Returns:
            Dict with automation result or None if no automation triggered
        """
        try:
            print(f"🔍 InteractiveAutomation: Processing message for account={self.account_id}, workspace={self.workspace_id}")
            print(f"   Message: '{message_text}', is_button={is_button_reply}")
            
            # First, check if user has an active conversation state (mid-flow)
            active_state = self._get_active_conversation_state(conversation_id)
            
            if active_state:
                print(f"   ✓ Found active state: {active_state.id}, continuing flow")
                # User is already in a flow - handle button click or text input
                return self._handle_flow_continuation(
                    active_state, message_text, from_phone, is_button_reply, button_payload
                )
            
            # No active state - check if message triggers a new automation
            print(f"   ⌕ No active state, searching for matching automation...")
            automation = self._find_matching_automation(message_text, is_button_reply)
            
            if automation:
                print(f"   ✓ Found matching automation: {automation.id} - {automation.name}")
                return self._start_automation_flow(
                    automation, conversation_id, from_phone
                )
            
            print(f"   ✗ No matching automation found")
            return None
            
        except Exception as e:
            print(f"   ⚠️ Interactive automation error: {e}")
            logger.exception(f"Interactive automation error (non-fatal): {e}")
            return None
    
    def _get_active_conversation_state(self, conversation_id: int) -> Optional[WhatsAppConversationState]:
        """Get active conversation state if user is mid-flow."""
        return WhatsAppConversationState.query.filter_by(
            conversation_id=conversation_id,
            workspace_id=self.workspace_id,
            is_active=True
        ).first()
    
    def _find_matching_automation(
        self, message_text: str, is_button_reply: bool
    ) -> Optional[WhatsAppVisualAutomation]:
        """
        Find an active automation that matches the incoming message.
        """
        # Get all active automations for this account
        print(f"   Querying automations: account_id={self.account_id}, workspace_id='{self.workspace_id}'")
        
        automations = WhatsAppVisualAutomation.query.filter_by(
            account_id=self.account_id,
            workspace_id=self.workspace_id,
            is_active=True,
            status="active"
        ).all()
        
        print(f"   Found {len(automations)} active automations")
        
        # Also check if there are any automations at all for this workspace (for debugging)
        all_automations = WhatsAppVisualAutomation.query.filter_by(
            workspace_id=self.workspace_id
        ).all()
        print(f"   Total automations in workspace: {len(all_automations)}")
        for a in all_automations:
            print(f"     - ID:{a.id} '{a.name}' account={a.account_id} status={a.status} is_active={a.is_active}")
        
        for automation in automations:
            trigger_type = automation.trigger_type
            trigger_config = automation.trigger_config or {}
            
            # Check trigger matching
            if trigger_type == "any_reply":
                # Matches any message (but not button replies in the middle of a flow)
                if not is_button_reply:
                    return automation
                    
            elif trigger_type == "keyword":
                # Check if message contains any of the keywords
                keywords = trigger_config.get("keywords", [])
                if isinstance(keywords, str):
                    keywords = [k.strip() for k in keywords.split(",")]
                    
                message_lower = message_text.lower()
                for keyword in keywords:
                    if keyword.lower() in message_lower:
                        return automation
                        
            elif trigger_type == "exact_match":
                # Exact message match
                expected = trigger_config.get("message", "").lower()
                if message_text.lower() == expected:
                    return automation
        
        return None
    
    def _start_automation_flow(
        self,
        automation: WhatsAppVisualAutomation,
        conversation_id: int,
        from_phone: str,
    ) -> Dict[str, Any]:
        """
        Start a new automation flow for the user.
        """
        nodes = automation.nodes or []
        edges = automation.edges or []
        
        # Find the trigger node
        trigger_node = None
        for node in nodes:
            if node.get("type") == "trigger":
                trigger_node = node
                break
        
        if not trigger_node:
            logger.error(f"Automation {automation.id} has no trigger node")
            return None
        
        # Find the first sendable node connected to the trigger (message or template)
        trigger_id = trigger_node.get("id")
        first_node = None
        
        for edge in edges:
            if edge.get("source") == trigger_id:
                target_id = edge.get("target")
                for node in nodes:
                    if node.get("id") == target_id and node.get("type") in SENDABLE_NODE_TYPES:
                        first_node = node
                        break
                break
        
        if not first_node:
            logger.warning(f"Automation {automation.id} has no message/template node connected to trigger")
            return None
        
        # Create conversation state
        state = WhatsAppConversationState(
            workspace_id=self.workspace_id,
            conversation_id=conversation_id,
            phone_number=from_phone,
            automation_id=automation.id,
            current_node_id=first_node.get("id"),
            is_active=True,
            last_user_message_at=datetime.now(timezone.utc),
        )
        db.session.add(state)
        
        # Increment automation trigger count
        automation.increment_trigger_count()
        db.session.commit()
        
        # Send the first node (message or template)
        return self._send_node_message(
            automation, first_node, from_phone, state
        )
    
    def _handle_flow_continuation(
        self,
        state: WhatsAppConversationState,
        message_text: str,
        from_phone: str,
        is_button_reply: bool,
        button_payload: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """
        Handle continuation of an active flow (button click or text).
        """
        print(f"   -> Flow continuation: state_id={state.id}, automation_id={state.automation_id}")
        print(f"      Current node: {state.current_node_id}")
        print(f"      is_button_reply={is_button_reply}, button_payload={button_payload}")
        
        # Check if this state is stale (older than 24 hours)
        if state.last_user_message_at:
            # Handle timezone-naive datetimes from database
            last_msg_at = state.last_user_message_at
            if last_msg_at.tzinfo is None:
                last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - last_msg_at).total_seconds() / 3600
            print(f"      State age: {age_hours:.1f} hours")
            if age_hours > 24:
                print(f"      ⚠️ State is stale (>24h), completing it")
                state.complete()
                db.session.commit()
                # Now search for a new automation match
                return None
        
        automation = WhatsAppVisualAutomation.query.get(state.automation_id)
        if not automation:
            print(f"      ⚠️ Automation {state.automation_id} not found, completing state")
            state.complete()
            db.session.commit()
            return None
        
        print(f"      Automation: '{automation.name}' (active={automation.is_active})")
        
        nodes = automation.nodes or []
        edges = automation.edges or []
        
        current_node_id = state.current_node_id
        
        # Find the edge for this button click
        next_node_id = None
        
        if is_button_reply and button_payload:
            # Button payload is the button ID - find edge with this sourceHandle
            print(f"      Looking for edge with sourceHandle={button_payload}")
            for edge in edges:
                if edge.get("sourceHandle") == button_payload:
                    next_node_id = edge.get("target")
                    print(f"      ✓ Found edge -> {next_node_id}")
                    break
        else:
            # Text response - check if current node has any "any_reply" type button
            # or if there's a default continuation
            print(f"      Text response: looking for current node {current_node_id}")
            current_node = None
            for node in nodes:
                if node.get("id") == current_node_id:
                    current_node = node
                    break
            
            if current_node and current_node.get("type") == "message":
                buttons = current_node.get("data", {}).get("buttons", [])
                print(f"      Current node has {len(buttons)} buttons")
                for button in buttons:
                    # Check if any button has quick_reply type and matches text
                    label = button.get("label", "")
                    print(f"        Button: '{label}' (checking vs '{message_text}')")
                    if button.get("action", {}).get("type") == "quick_reply":
                        if label.lower() == message_text.lower():
                            # Text matched a button label - treat as button click
                            button_id = button.get("id")
                            for edge in edges:
                                if edge.get("sourceHandle") == button_id:
                                    next_node_id = edge.get("target")
                                    print(f"      ✓ Text matched button, going to {next_node_id}")
                                    break
                            break
            elif current_node and current_node.get("type") == "template":
                # Template nodes - match text against template button labels
                buttons = current_node.get("data", {}).get("buttons", [])
                print(f"      Current template node has {len(buttons)} buttons")
                for idx, button in enumerate(buttons):
                    label = button.get("text", "")
                    print(f"        Template button: '{label}' (checking vs '{message_text}')")
                    if label.lower() == message_text.lower():
                        btn_handle = button.get("handleId") or f"{current_node_id}-btn-{idx}"
                        for edge in edges:
                            if edge.get("sourceHandle") == btn_handle:
                                next_node_id = edge.get("target")
                                print(f"      ✓ Text matched template button, going to {next_node_id}")
                                break
                        break
        
        if not next_node_id:
            # No matching next node - user may have sent unexpected input
            # Clear the state and send a helpful message
            print(f"      ✗ No matching next node found. User sent unexpected input: '{message_text}'")
            print(f"      🧹 Clearing state and sending help message")
            
            # Complete/clear the state so user can start fresh
            state.complete()
            db.session.commit()
            
            # Send a helpful message to the user
            help_message = "Sorry, I didn't understand that response. The conversation has been reset. You can start again by sending your command."
            
            # Get account for sending
            account = WhatsAppAccount.query.get(self.account_id)
            if account:
                service = WhatsAppService(
                    phone_number_id=account.phone_number_id,
                    access_token=account.get_access_token(),
                )
                service.send_text(to=from_phone, text=help_message)
            
            logger.info(f"Cleared state {state.id} after unexpected input")
            return {"state_cleared": True, "reason": "unexpected_input"}
        
        # Find the next node
        next_node = None
        for node in nodes:
            if node.get("id") == next_node_id:
                next_node = node
                break
        
        if not next_node:
            logger.warning(f"Target node {next_node_id} not found")
            return None
        
        # Update state
        state.advance_to_node(next_node_id)
        if button_payload:
            state.record_button_click(button_payload)
        state.last_user_message_at = datetime.now(timezone.utc)
        
        # Check if this is an end node
        if next_node.get("type") == "end":
            state.complete()
            db.session.commit()
            
            # Send end message if configured
            end_message = next_node.get("data", {}).get("message")
            if end_message:
                return self._send_text_message(from_phone, end_message, state.conversation_id)
            return {"completed": True, "message": "Flow completed"}
        
        db.session.commit()
        
        # Send the next message node
        return self._send_node_message(automation, next_node, from_phone, state)
    
    def _send_node_message(
        self,
        automation: WhatsAppVisualAutomation,
        node: Dict[str, Any],
        to_phone: str,
        state: WhatsAppConversationState,
    ) -> Dict[str, Any]:
        """
        Send a node's content to the user.
        Dispatches to the appropriate sender based on node type.
        """
        node_type = node.get("type", "message")
        
        # Template nodes use WhatsApp template API
        if node_type == "template":
            return self._send_template_node_message(automation, node, to_phone, state)
        
        # Default: send as interactive message with buttons
        node_data = node.get("data", {})
        body = node_data.get("body", "")
        header = node_data.get("header")
        footer = node_data.get("footer")
        buttons = node_data.get("buttons", [])
        
        # Get WhatsApp account for sending
        account = WhatsAppAccount.query.get(self.account_id)
        if not account:
            logger.error(f"Account {self.account_id} not found")
            return {"error": "Account not found"}
        
        service = WhatsAppService(
            phone_number_id=account.phone_number_id,
            access_token=account.get_access_token(),
        )
        
        # Filter buttons to only include quick_reply buttons with connections
        # Also collect call/URL buttons to append to message body
        interactive_buttons = []
        call_buttons = []
        url_buttons = []
        
        print(f"      Buttons in node: {len(buttons)}")
        for idx, button in enumerate(buttons):
            print(f"        Button {idx}: {button}")
            
            # Handle different button data structures from the flow builder
            # The frontend might store action differently
            action = button.get("action", {})
            action_type = action.get("type", "quick_reply")  # Default to quick_reply
            
            # Get button label - try multiple possible locations
            button_label = (
                button.get("label") or 
                button.get("title") or 
                button.get("text") or 
                action.get("label") or
                "Button"
            )
            button_id = button.get("id", f"btn_{idx}")
            
            print(f"          action_type={action_type}, label='{button_label}', id={button_id}")
            
            if action_type in ("quick_reply", "reply", None):
                # Only include if button has a target node connected
                for edge in (automation.edges or []):
                    if edge.get("sourceHandle") == button_id:
                        # Ensure title is not empty (Meta requires this)
                        title = button_label if button_label else f"Option {idx + 1}"
                        interactive_buttons.append({
                            "type": "reply",
                            "reply": {
                                "id": button_id,
                                "title": title[:20]  # Max 20 chars for WhatsApp
                            }
                        })
                        print(f"          ✓ Added interactive button: {title[:20]}")
                        break
            elif action_type == "call":
                # Call buttons - append phone number to message body
                phone_number = action.get("phoneNumber") or action.get("phone") or action.get("value")
                if phone_number:
                    call_buttons.append({"label": button_label, "phone": phone_number})
                    print(f"          📞 Call button: {button_label} -> {phone_number}")
            elif action_type == "url":
                # URL buttons - append URL to message body
                url = action.get("url") or action.get("value")
                if url:
                    url_buttons.append({"label": button_label, "url": url})
                    print(f"          🔗 URL button: {button_label} -> {url}")
        
        print(f"      Interactive buttons to send: {interactive_buttons}")
        
        # If no interactive buttons but has call/URL buttons, append them to body
        if not interactive_buttons and (call_buttons or url_buttons):
            # This is a terminal node with action buttons - clear the state
            print(f"      🏁 Terminal node with action buttons, completing state")
            state.complete()
            db.session.commit()
        
        if not body:
            body = "Please select an option:"
        
        # Append call buttons info to body
        for call_btn in call_buttons:
            body += f"\n\n📞 {call_btn['label']}: {call_btn['phone']}"
        
        # Append URL buttons info to body
        for url_btn in url_buttons:
            body += f"\n\n🔗 {url_btn['label']}: {url_btn['url']}"
        
        if interactive_buttons:
            # Send as interactive message with buttons
            result = service.send_interactive_buttons(
                to=to_phone,
                body_text=body,
                buttons=interactive_buttons,
                header_text=header,
                footer_text=footer,
            )
        else:
            # No buttons - send as plain text
            result = service.send_text(to=to_phone, text=body)
        
        if result.get("success"):
            logger.info(f"Sent interactive automation message to {to_phone}")
            
            # Note: message is already saved by WhatsAppService.send_interactive_buttons() or send_text()
            # Just broadcast SSE using the message data from service result
            try:
                message_id = result.get("message_id")
                conversation_id = result.get("conversation_id") or state.conversation_id
                
                # Retrieve the message that was just saved by the service
                msg_record = WhatsAppMessage.query.get(message_id)
                if msg_record:
                    # Broadcast via SSE for real-time inbox update
                    notification_manager.broadcast("whatsapp_message_received", {
                        "message": msg_record.to_dict(),
                        "conversation_id": conversation_id,
                        "account_id": self.account_id,
                        "workspace_id": self.workspace_id
                    })
                    logger.info(f"Broadcasted interactive automation message: {msg_record.id}")
                else:
                    logger.warning(f"Could not find message {message_id} for SSE broadcast")
            except Exception as e:
                logger.error(f"Failed to broadcast automation message: {e}")
            
            # If this was a message with call/URL buttons (no interactive buttons),
            # auto-continue to the next connected node
            if not interactive_buttons and (call_buttons or url_buttons):
                print(f"      ⏩ Auto-continuing to next node after call/URL buttons...")
                # Find any edge from this node (could be from any button or the node itself)
                next_node_id = None
                current_node_id = node.get("id")
                
                # Look for edges from this node
                for edge in (automation.edges or []):
                    if edge.get("source") == current_node_id:
                        next_node_id = edge.get("target")
                        break
                    # Also check if any button has a connected edge
                    for btn in buttons:
                        btn_id = btn.get("id")
                        if edge.get("sourceHandle") == btn_id:
                            next_node_id = edge.get("target")
                            break
                    if next_node_id:
                        break
                
                if next_node_id:
                    # Find the next node
                    next_node = None
                    for n in (automation.nodes or []):
                        if n.get("id") == next_node_id:
                            next_node = n
                            break
                    
                    if next_node:
                        print(f"      ⏩ Found next node: {next_node_id} (type={next_node.get('type')})")
                        
                        # If it's an end node, send the end message
                        if next_node.get("type") == "end":
                            end_message = next_node.get("data", {}).get("message")
                            if end_message:
                                print(f"      🏁 Sending end node message: {end_message[:50]}...")
                                self._send_text_message(to_phone, end_message, state.conversation_id)
                            return {"success": True, "completed": True, "message": "Flow completed"}
                        else:
                            # Send the next message node
                            return self._send_node_message(automation, next_node, to_phone, state)
            
            return {
                "success": True,
                "automation_id": automation.id,
                "node_id": node.get("id"),
                "message_id": result.get("message_id"),
            }
        else:
            logger.error(f"Failed to send automation message: {result.get('error')}")
            return {"success": False, "error": result.get("error")}
    
    def _send_template_node_message(
        self,
        automation: WhatsAppVisualAutomation,
        node: Dict[str, Any],
        to_phone: str,
        state: WhatsAppConversationState,
    ) -> Dict[str, Any]:
        """
        Send a template node's content to the user via WhatsApp Template API.
        
        Template nodes reference approved Meta templates. Quick-reply button
        payloads are mapped to flow edges so the engine can navigate on click.
        """
        node_data = node.get("data", {})
        template_name = node_data.get("templateName", "")
        language_code = node_data.get("languageCode", "en_US")
        template_id = node_data.get("templateId")
        body_params = node_data.get("bodyParams")  # Optional variable values
        header_text_var = node_data.get("headerTextVar")  # Optional header variable
        header_image_url = node_data.get("headerImageUrl")
        
        if not template_name:
            logger.error(f"Template node {node.get('id')} has no templateName")
            return {"error": "Template node missing templateName"}
        
        # Get WhatsApp account for sending
        account = WhatsAppAccount.query.get(self.account_id)
        if not account:
            logger.error(f"Account {self.account_id} not found")
            return {"error": "Account not found"}
        
        service = WhatsAppService(
            phone_number_id=account.phone_number_id,
            access_token=account.get_access_token(),
        )
        
        # Build button payloads for quick_reply buttons
        # Map template buttons to flow edges
        template_buttons = node_data.get("buttons", [])
        button_payloads = []
        has_connected_buttons = False
        
        for idx, btn in enumerate(template_buttons):
            # Check if this button has a connected edge in the flow
            btn_handle = btn.get("handleId") or f"{node.get('id')}-btn-{idx}"
            for edge in (automation.edges or []):
                if edge.get("sourceHandle") == btn_handle:
                    has_connected_buttons = True
                    break
            
            # For quick_reply buttons, set payload to the handle ID so we can
            # match it to the edge when the user clicks
            payload_value = btn.get("payload") or btn_handle
            button_payloads.append({
                "index": idx,
                "type": "quick_reply",
                "value": payload_value,
            })
        
        # If no buttons have connections, this is a terminal template node
        if not has_connected_buttons and template_buttons:
            print(f"      🏁 Terminal template node, completing state")
            state.complete()
            db.session.commit()
        
        # Send the template
        print(f"      📋 Sending template: {template_name} (lang={language_code})")
        
        result = service.send_template_with_builder(
            to=to_phone,
            template_name=template_name,
            language_code=language_code,
            body_params=body_params,
            header_text=header_text_var,
            header_image_url=header_image_url,
            button_payloads=button_payloads if button_payloads else None,
        )
        
        if result.get("success"):
            logger.info(f"Sent template automation message '{template_name}' to {to_phone}")
            
            # Broadcast SSE for real-time inbox update
            try:
                message_id = result.get("message_id")
                conversation_id = result.get("conversation_id") or state.conversation_id
                
                msg_record = WhatsAppMessage.query.get(message_id)
                if msg_record:
                    notification_manager.broadcast("whatsapp_message_received", {
                        "message": msg_record.to_dict(),
                        "conversation_id": conversation_id,
                        "account_id": self.account_id,
                        "workspace_id": self.workspace_id
                    })
            except Exception as e:
                logger.error(f"Failed to broadcast template automation message: {e}")
            
            return {
                "success": True,
                "automation_id": automation.id,
                "node_id": node.get("id"),
                "message_id": result.get("message_id"),
                "template_name": template_name,
            }
        else:
            logger.error(f"Failed to send template automation message: {result.get('error')}")
            return {"success": False, "error": result.get("error")}
    
    def _send_text_message(self, to_phone: str, text: str, conversation_id: int = None) -> Dict[str, Any]:
        """Send a simple text message and broadcast via SSE."""
        account = WhatsAppAccount.query.get(self.account_id)
        if not account:
            return {"error": "Account not found"}
        
        service = WhatsAppService(
            phone_number_id=account.phone_number_id,
            access_token=account.get_access_token(),
        )
        
        result = service.send_text(to=to_phone, text=text)
        
        # Save and broadcast if we have conversation_id
        if result.get("success") and conversation_id:
            try:
                msg_record = WhatsAppMessage(
                    conversation_id=conversation_id,
                    wamid=result.get("message_id"),
                    direction="outgoing",
                    type="text",
                    content=text,
                    status="sent",
                )
                db.session.add(msg_record)
                db.session.commit()
                
                # Broadcast via SSE for real-time inbox update
                notification_manager.broadcast("whatsapp_message_received", {
                    "message": msg_record.to_dict(),
                    "conversation_id": conversation_id,
                    "account_id": self.account_id,
                    "workspace_id": self.workspace_id
                })
            except Exception as e:
                logger.error(f"Failed to save/broadcast text message: {e}")
        
        return {
            "success": result.get("success", False),
            "message_id": result.get("message_id"),
        }


def process_interactive_automation(
    account: WhatsAppAccount,
    conversation: WhatsAppConversation,
    message_text: str,
    from_phone: str,
    is_button_reply: bool = False,
    button_payload: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Convenience function to process a message against interactive automations.
    
    Called from the webhook handler.
    
    Args:
        account: WhatsApp account
        conversation: Conversation object
        message_text: Text content of the message
        from_phone: Sender's phone number
        is_button_reply: True if this is a button reply
        button_payload: Button ID/payload if button reply
        
    Returns:
        Dict with result or None if no automation triggered
    """
    try:
        # Check if interactive_flows automation is disabled for this contact
        from .automation_models import is_automation_disabled_for_contact
        
        if is_automation_disabled_for_contact(account.workspace_id, conversation.id, "interactive_flows"):
            logger.debug(f"Interactive flows disabled for conversation {conversation.id}")
            print(f"   ⏸️ Interactive flows disabled for this contact (conversation {conversation.id})")
            return None
        
        engine = InteractiveAutomationEngine(
            account_id=account.id,
            workspace_id=account.workspace_id
        )
        
        return engine.process_incoming_message(
            message_text=message_text,
            conversation_id=conversation.id,
            from_phone=from_phone,
            is_button_reply=is_button_reply,
            button_payload=button_payload,
        )
        
    except Exception as e:
        logger.exception(f"Interactive automation processing error: {e}")
        return None
