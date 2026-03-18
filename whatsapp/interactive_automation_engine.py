"""
Interactive Automation Execution Engine
========================================

Executes interactive automation flows when users send messages or click buttons.

This engine:
1. Checks if any active interactive automation matches the incoming message trigger
2. Tracks conversation state (which node the user is at)
3. Sends interactive messages with buttons
4. Handles button click responses and navigates to the next node
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List

from models import db
from .visual_automation_models import WhatsAppVisualAutomation, WhatsAppConversationState
from .models import WhatsAppAccount, WhatsAppConversation, WhatsAppMessage
from .services import WhatsAppService
from .template_node_executor import TemplateNodeExecutor
from notifications import notification_manager

logger = logging.getLogger(__name__)


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
            try:
                db.session.rollback()
            except Exception:
                pass
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
        
        # Find the first node connected to the trigger (message or template)
        trigger_id = trigger_node.get("id")
        first_node = None
        
        for edge in edges:
            if edge.get("source") == trigger_id:
                target_id = edge.get("target")
                for node in nodes:
                    if node.get("id") == target_id and node.get("type") in ("message", "template"):
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
        if first_node.get("type") == "template":
            return self._send_template_node(
                automation, first_node, from_phone, state
            )
        else:
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
            # Check if this is a template button payload (iflow_ prefix)
            decoded = TemplateNodeExecutor.decode_button_payload(button_payload)
            if decoded:
                decoded_automation_id = decoded.get('automation_id')
                decoded_node_id = decoded.get('target_node_id')
                print(f"      🎯 Decoded template button: automation={decoded_automation_id}, target={decoded_node_id}")
                
                # Verify the automation ID matches
                if str(decoded_automation_id) == str(automation.id):
                    if decoded_node_id is None:
                        # Template button pointed to flow end (target_node_id is None means END)
                        print(f"      ✓ Template button -> END, completing flow")
                        state.complete()
                        db.session.commit()
                        return {"flow_completed": True, "reason": "template_end_button"}
                    else:
                        next_node_id = decoded_node_id
                        print(f"      ✓ Template button -> {next_node_id}")
                else:
                    print(f"      ⚠️ Decoded automation ID {decoded_automation_id} doesn't match current {automation.id}")
            
            # Regular button payload - find edge with this sourceHandle
            if not next_node_id:
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
            else:
                print(f"      ⚠️ Current node not found or not a message node")
        
        if not next_node_id:
            # No matching next node - check if button has a special action (like send_document)
            # that should be executed even without a connection
            if is_button_reply and button_payload:
                button_action = self._get_button_action(nodes, current_node_id, button_payload)
                if button_action and button_action.get("type") == "send_document":
                    print(f"      🔄 Button has send_document action - executing without edge")
                    self._execute_button_action(
                        nodes=nodes,
                        current_node_id=current_node_id,
                        button_payload=button_payload,
                        to_phone=from_phone,
                        conversation_id=state.conversation_id
                    )
                    
                    # After sending document, look for a default edge from this node
                    # (an edge from the node itself, not from a specific button)
                    for edge in edges:
                        if edge.get("source") == current_node_id and not edge.get("sourceHandle"):
                            next_node_id = edge.get("target")
                            print(f"      ✓ Found default edge -> {next_node_id}")
                            break
                    
                    # If still no next node, complete the flow gracefully
                    if not next_node_id:
                        print(f"      🏁 No next node after send_document, completing flow")
                        state.complete()
                        db.session.commit()
                        return {"completed": True, "document_sent": True, "message": "Document sent, flow completed"}
            
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
        
        # Execute button action if this was a button click
        if is_button_reply and button_payload:
            self._execute_button_action(
                nodes=nodes,
                current_node_id=current_node_id,
                button_payload=button_payload,
                to_phone=from_phone,
                conversation_id=state.conversation_id
            )
        
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
        
        # Check if this is a template node
        if next_node.get("type") == "template":
            db.session.commit()
            return self._send_template_node(automation, next_node, from_phone, state)
        
        db.session.commit()
        
        # Send the next message node
        return self._send_node_message(automation, next_node, from_phone, state)
    
    def _get_button_action(
        self,
        nodes: List[Dict[str, Any]],
        current_node_id: str,
        button_payload: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the action configuration for a specific button.
        Returns the action dict or None if not found.
        """
        # Find the current node
        current_node = None
        for node in nodes:
            if node.get("id") == current_node_id:
                current_node = node
                break
        
        if not current_node or current_node.get("type") != "message":
            return None
        
        # Find the clicked button
        buttons = current_node.get("data", {}).get("buttons", [])
        for button in buttons:
            if button.get("id") == button_payload:
                return button.get("action", {})
        
        return None

    def _execute_button_action(
        self,
        nodes: List[Dict[str, Any]],
        current_node_id: str,
        button_payload: str,
        to_phone: str,
        conversation_id: Optional[int] = None
    ) -> None:
        """
        Execute button-specific action when user clicks a button.
        Handles send_document actions and other special button behaviors.
        """
        # Find the current node
        current_node = None
        for node in nodes:
            if node.get("id") == current_node_id:
                current_node = node
                break
        
        if not current_node or current_node.get("type") != "message":
            return
        
        # Find the clicked button
        buttons = current_node.get("data", {}).get("buttons", [])
        clicked_button = None
        for button in buttons:
            if button.get("id") == button_payload:
                clicked_button = button
                break
        
        if not clicked_button:
            print(f"      Button {button_payload} not found in node {current_node_id}")
            return
        
        action = clicked_button.get("action", {})
        action_type = action.get("type")
        
        print(f"      Executing button action: {action_type}")
        
        if action_type == "send_document":
            # Send the document attached to this button
            document_url = action.get("documentUrl")
            document_filename = action.get("documentFilename", "document.pdf")
            document_caption = action.get("documentCaption", "")
            
            if document_url:
                print(f"      Sending document: {document_url} ({document_filename})")
                account = WhatsAppAccount.query.get(self.account_id)
                if account:
                    service = WhatsAppService(
                        phone_number_id=account.phone_number_id,
                        access_token=account.get_access_token(),
                    )
                    result = service.send_document(
                        to=to_phone,
                        document_url=document_url,
                        caption=document_caption,
                        filename=document_filename
                    )
                    print(f"      Document send result: {result}")
            else:
                print(f"      ⚠️ send_document action has no documentUrl")
    
    def _send_node_message(
        self,
        automation: WhatsAppVisualAutomation,
        node: Dict[str, Any],
        to_phone: str,
        state: WhatsAppConversationState,
    ) -> Dict[str, Any]:
        """
        Send a message node's content to the user.
        Supports text, image, video, and document headers.
        """
        node_data = node.get("data", {})
        body = node_data.get("body", "")
        header = node_data.get("header")
        footer = node_data.get("footer")
        buttons = node_data.get("buttons", [])
        
        # Header media support - try both camelCase and snake_case keys
        header_image_url = node_data.get("headerImageUrl") or node_data.get("header_image_url")
        header_video_url = node_data.get("headerVideoUrl") or node_data.get("header_video_url")
        header_document_url = node_data.get("headerDocumentUrl") or node_data.get("header_document_url")
        header_document_filename = node_data.get("headerDocumentFilename") or node_data.get("header_document_filename")
        
        # Debug: Log the node data and extracted headers
        print(f"      [DEBUG] Node data keys: {list(node_data.keys())}")
        print(f"      [DEBUG] Full node_data: {node_data}")
        print(f"      [DEBUG] header_image_url: {header_image_url}")
        print(f"      [DEBUG] header_video_url: {header_video_url}")
        print(f"      [DEBUG] header_document_url: {header_document_url}")
        
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
            
            if action_type in ("quick_reply", "reply", "send_document", None):
                # Include interactive buttons - quick_reply buttons need a target node connection
                # send_document buttons work with or without a connection (they send a document)
                has_connection = False
                for edge in (automation.edges or []):
                    if edge.get("sourceHandle") == button_id:
                        has_connection = True
                        break
                
                # For send_document, always include the button even without a connection
                # For quick_reply, only include if there's a connection
                if has_connection or action_type == "send_document":
                    # Ensure title is not empty (Meta requires this)
                    title = button_label if button_label else f"Option {idx + 1}"
                    interactive_buttons.append({
                        "type": "reply",
                        "reply": {
                            "id": button_id,
                            "title": title[:20]  # Max 20 chars for WhatsApp
                        }
                    })
                    print(f"          ✓ Added interactive button: {title[:20]} (action={action_type})")
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
                header_text=header if not (header_image_url or header_video_url or header_document_url) else None,
                header_image_url=header_image_url,
                header_video_url=header_video_url,
                header_document_url=header_document_url,
                header_document_filename=header_document_filename,
                footer_text=footer,
            )
        else:
            # No buttons - send as plain text (or image if header has media)
            if header_image_url:
                # Send image with caption
                result = service.send_image(to=to_phone, image_url=header_image_url, caption=body)
            elif header_video_url:
                # Send video with caption
                result = service.send_video(to=to_phone, video_url=header_video_url, caption=body)
            elif header_document_url:
                # Send document with caption
                result = service.send_document(to=to_phone, document_url=header_document_url, caption=body, filename=header_document_filename)
            else:
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
                try:
                    db.session.rollback()
                except Exception:
                    pass
        
        return {
            "success": result.get("success", False),
            "message_id": result.get("message_id"),
        }
    
    def _send_template_node(
        self,
        automation: WhatsAppVisualAutomation,
        node: Dict[str, Any],
        to_phone: str,
        state: WhatsAppConversationState,
    ) -> Dict[str, Any]:
        """
        Send a template node's message to the user.
        
        Template nodes have:
        - template_id: ID of the template to use
        - template_name: Name of the template
        - button_mappings: Dict mapping button IDs to target node IDs for flow routing
        - variables: Optional variables to substitute in template
        """
        node_data = node.get("data", {})
        template_id = node_data.get("template_id")
        template_name = node_data.get("template_name")
        button_mappings = node_data.get("button_mappings", {})
        variables = node_data.get("variables", {})
        
        print(f"    📋 Sending template node: {template_name} (ID: {template_id})")
        print(f"       Button mappings: {button_mappings}")
        
        if not template_id and not template_name:
            logger.error(f"Template node {node.get('id')} has no template configured")
            return {"error": "No template configured"}
        
        # Get WhatsApp account for sending
        account = WhatsAppAccount.query.get(self.account_id)
        if not account:
            logger.error(f"Account {self.account_id} not found")
            return {"error": "Account not found"}
        
        # Get template from database to get full details
        from .whatsapp_templates import WhatsAppTemplate
        
        template = None
        if template_id:
            template = WhatsAppTemplate.query.filter_by(
                id=template_id,
                workspace_id=self.workspace_id
            ).first()
        elif template_name:
            template = WhatsAppTemplate.query.filter_by(
                name=template_name,
                workspace_id=self.workspace_id,
                status='APPROVED'
            ).first()
        
        if not template:
            logger.error(f"Template {template_id or template_name} not found")
            return {"error": "Template not found"}
        
        # Prepare runtime variables
        runtime_variables = self._get_runtime_variables(to_phone, state)
        runtime_variables.update(variables)
        
        # Build template components with encoded button payloads
        components = TemplateNodeExecutor.build_template_components(
            template=template,
            automation_id=automation.id,
            button_mappings=button_mappings,
            variables=runtime_variables,
        )
        
        # Send template
        service = WhatsAppService(
            phone_number_id=account.phone_number_id,
            access_token=account.get_access_token(),
        )
        
        result = service.send_template(
            to=to_phone,
            template_name=template.name,
            language_code=template.language or "en",
            components=components,
        )
        
        if result.get("success"):
            logger.info(f"Sent template {template.name} to {to_phone}")
            
            # Check if this template has no quick reply buttons (end of flow)
            has_quick_reply_buttons = False
            if template.components:
                for comp in template.components:
                    if comp.get("type") == "BUTTONS":
                        for btn in comp.get("buttons", []):
                            if btn.get("type") == "QUICK_REPLY":
                                has_quick_reply_buttons = True
                                break
                    if has_quick_reply_buttons:
                        break
            
            if not has_quick_reply_buttons:
                # No quick reply buttons = flow ends here
                print(f"      🏁 Template has no quick reply buttons, completing flow")
                state.complete()
                db.session.commit()
            
            return {
                "success": True,
                "automation_id": automation.id,
                "node_id": node.get("id"),
                "message_id": result.get("message_id"),
                "template": template.name,
            }
        else:
            logger.error(f"Failed to send template: {result.get('error')}")
            return {"success": False, "error": result.get("error")}
    
    def _get_runtime_variables(
        self,
        to_phone: str,
        state: WhatsAppConversationState,
    ) -> Dict[str, str]:
        """Get runtime variables for template substitution."""
        variables = {
            "phone": to_phone,
        }
        
        # Try to get contact/conversation info
        try:
            conversation = WhatsAppConversation.query.get(state.conversation_id)
            if conversation:
                variables["contact_name"] = conversation.contact_name or ""
                variables["customer_name"] = conversation.contact_name or ""
        except Exception as e:
            logger.debug(f"Could not get conversation info: {e}")
        
        return variables


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
        try:
            db.session.rollback()
        except Exception:
            pass
        return None
