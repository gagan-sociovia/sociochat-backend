"""
Template Node Executor
======================

Executes template nodes in interactive automations.

Handles:
- Template sending with variable substitution
- Button payload encoding for flow routing
- 24-hour window checking with template fallback

When a template with quick reply buttons is sent, the button payloads
encode the automation_id and target_node_id. When user clicks a button,
the webhook handler decodes this payload to route to the correct next node.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Callable

logger = logging.getLogger(__name__)


class TemplateNodeExecutor:
    """
    Executes template nodes and encodes button payloads for flow routing.
    
    The key innovation: We encode flow routing information (automation_id + target_node_id)
    in the button payload. When Meta sends us the button click webhook, we decode it
    to continue the flow.
    """
    
    PAYLOAD_PREFIX = "iflow_"  # Prefix to identify our flow payloads
    
    def __init__(
        self, 
        workspace_id: str, 
        account_id: int, 
        send_template_fn: Callable[[str, str, str, List], Any]
    ):
        """
        Initialize the template node executor.
        
        Args:
            workspace_id: Workspace ID for multi-tenant isolation
            account_id: WhatsApp account ID
            send_template_fn: Function to send template messages
                Signature: (phone_number, template_name, language_code, components) -> result
        """
        self.workspace_id = workspace_id
        self.account_id = account_id
        self.send_template = send_template_fn
    
    def execute_template_node(
        self,
        state,  # WhatsAppConversationState
        node: Dict[str, Any],
        runtime_variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute a template node - sends the template with encoded button payloads.
        
        The button payloads encode the automation_id and target_node_id so
        when user clicks, we can route to the correct next node.
        
        Args:
            state: Current conversation state
            node: Node definition with template configuration
            runtime_variables: Dynamic values to substitute in template
            
        Returns:
            Dict with:
                - success: bool
                - template_sent: str (template name)
                - wait_for_input: bool (True if has quick reply buttons)
                - next_node_id: str or None
                - error: str (if failed)
        """
        node_id = node.get('id')
        node_data = node.get('data', {})
        
        template_name = node_data.get('templateName')
        template_language = node_data.get('templateLanguage', 'en_US')
        template_variables = node_data.get('templateVariables', {})
        button_mappings = node_data.get('buttonMappings', [])
        
        if not template_name:
            logger.warning(f"[TemplateNode] Node {node_id} has no template name")
            return {'success': False, 'error': 'Template name not specified'}
        
        try:
            # Build template components with variable substitution
            components = self._build_components(
                template_variables,
                button_mappings,
                state.automation_id,
                runtime_variables or {}
            )
            
            logger.info(
                f"[TemplateNode] Sending template '{template_name}' to {state.phone_number} "
                f"(automation={state.automation_id}, node={node_id})"
            )
            
            # Send the template
            result = self.send_template(
                state.phone_number,
                template_name,
                template_language,
                components
            )
            
            # Check if there are quick reply buttons that can trigger next nodes
            quick_reply_buttons = [b for b in button_mappings if b.get('buttonType') == 'quick_reply']
            has_routing_buttons = len(quick_reply_buttons) > 0
            
            logger.info(
                f"[TemplateNode] Template '{template_name}' sent successfully. "
                f"Has routing buttons: {has_routing_buttons}"
            )
            
            return {
                'success': True,
                'template_sent': template_name,
                'wait_for_input': has_routing_buttons,  # Wait if buttons exist
                'next_node_id': None,  # Determined by button click
                'message_result': result
            }
            
        except Exception as e:
            logger.error(f"[TemplateNode] Error sending template '{template_name}': {e}")
            return {'success': False, 'error': str(e)}
    
    def _build_components(
        self,
        template_variables: Dict[str, Any],
        button_mappings: List[Dict],
        automation_id: int,
        runtime_variables: Dict[str, Any]
    ) -> List[Dict]:
        """
        Build template components with button payloads for flow routing.
        
        Args:
            template_variables: Variable configuration from node data
            button_mappings: Button-to-node mappings
            automation_id: Current automation ID
            runtime_variables: Dynamic runtime values
            
        Returns:
            List of component dicts ready for WhatsApp API
        """
        components = []
        
        # Header component (if has image/video/document/text)
        if 'header' in template_variables and template_variables['header']:
            header = template_variables['header']
            header_param = self._build_header_param(header, runtime_variables)
            if header_param:
                components.append({
                    'type': 'header',
                    'parameters': [header_param]
                })
        
        # Body component (text parameters)
        if 'body' in template_variables and template_variables['body']:
            body_params = []
            for var in template_variables['body']:
                value = self._substitute_variable(var.get('value', ''), runtime_variables)
                body_params.append({'type': 'text', 'text': str(value)})
            
            if body_params:
                components.append({
                    'type': 'body',
                    'parameters': body_params
                })
        
        # Button components - CRITICAL: Encode next node in payload for quick_reply buttons
        for mapping in button_mappings:
            button_type = mapping.get('buttonType', 'quick_reply')
            button_index = mapping.get('buttonIndex', 0)
            target_node_id = mapping.get('targetNodeId')
            
            if button_type == 'quick_reply':
                # Encode the flow routing info in payload
                payload = self._encode_button_payload(automation_id, target_node_id)
                
                components.append({
                    'type': 'button',
                    'sub_type': 'quick_reply',
                    'index': str(button_index),
                    'parameters': [{
                        'type': 'payload',
                        'payload': payload
                    }]
                })
            
            elif button_type == 'url':
                # URL buttons may need dynamic suffix
                url_suffix = mapping.get('urlSuffix', '')
                if url_suffix:
                    url_suffix = self._substitute_variable(url_suffix, runtime_variables)
                    components.append({
                        'type': 'button',
                        'sub_type': 'url',
                        'index': str(button_index),
                        'parameters': [{
                            'type': 'text',
                            'text': url_suffix
                        }]
                    })
            
            # Call buttons don't need additional parameters
        
        return components
    
    def _encode_button_payload(self, automation_id: int, target_node_id: Optional[str]) -> str:
        """
        Encode automation routing info into button payload.
        
        Format: iflow_{automation_id}_{target_node_id}
        
        Args:
            automation_id: ID of the automation flow
            target_node_id: ID of the target node, or None for END
            
        Returns:
            Encoded payload string
        """
        node_part = target_node_id if target_node_id else 'END'
        return f"{self.PAYLOAD_PREFIX}{automation_id}_{node_part}"
    
    @classmethod
    def decode_button_payload(cls, payload: str) -> Optional[Dict[str, Any]]:
        """
        Decode button payload to get automation routing info.
        
        This is called by the webhook handler when a button click is received.
        
        Args:
            payload: The button payload string from webhook
            
        Returns:
            Dict with 'automation_id' and 'target_node_id', or None if not our payload
        """
        if not payload or not payload.startswith(cls.PAYLOAD_PREFIX):
            return None
        
        try:
            remainder = payload[len(cls.PAYLOAD_PREFIX):]
            parts = remainder.split('_', 1)
            
            if len(parts) >= 2:
                automation_id = int(parts[0])
                target_node_id = parts[1] if parts[1] != 'END' else None
                
                return {
                    'automation_id': automation_id,
                    'target_node_id': target_node_id
                }
        except (ValueError, IndexError) as e:
            logger.warning(f"[TemplateNode] Failed to decode payload '{payload}': {e}")
        
        return None
    
    @classmethod
    def is_flow_button_payload(cls, payload: str) -> bool:
        """
        Quick check if a payload is from our flow system.
        
        Args:
            payload: The button payload string
            
        Returns:
            True if this is an interactive flow payload
        """
        return payload and payload.startswith(cls.PAYLOAD_PREFIX)
    
    @classmethod
    def build_template_components(
        cls,
        template,
        automation_id: int,
        button_mappings: Dict[str, str],
        variables: Dict[str, Any] = None,
    ) -> List[Dict]:
        """
        Build template components with encoded button payloads.
        
        This static method is used by InteractiveAutomationEngine to prepare
        template components without needing to instantiate the executor.
        
        Args:
            template: WhatsAppTemplate model instance
            automation_id: ID of the automation flow
            button_mappings: Dict mapping button text/index to target node IDs
            variables: Optional variables for substitution
            
        Returns:
            List of component dicts ready for WhatsApp API
        """
        components = []
        variables = variables or {}
        
        if not template or not template.components:
            return components
        
        # Process each component from the template
        for comp in template.components:
            comp_type = comp.get("type", "").upper()
            
            if comp_type == "HEADER":
                # Handle header parameters (image, video, document, text)
                header_format = comp.get("format", "TEXT")
                if header_format == "IMAGE" and "header_image" in variables:
                    components.append({
                        "type": "header",
                        "parameters": [{
                            "type": "image",
                            "image": {"link": variables["header_image"]}
                        }]
                    })
                elif header_format == "VIDEO" and "header_video" in variables:
                    components.append({
                        "type": "header",
                        "parameters": [{
                            "type": "video",
                            "video": {"link": variables["header_video"]}
                        }]
                    })
                elif header_format == "DOCUMENT" and "header_document" in variables:
                    components.append({
                        "type": "header",
                        "parameters": [{
                            "type": "document",
                            "document": {
                                "link": variables["header_document"],
                                "filename": variables.get("header_filename", "document")
                            }
                        }]
                    })
                elif header_format == "TEXT":
                    # Text headers with variables
                    example = comp.get("example", {}).get("header_text", [])
                    if example:
                        params = []
                        for idx, _ in enumerate(example):
                            var_key = f"header_{idx}"
                            value = variables.get(var_key, "")
                            params.append({"type": "text", "text": str(value)})
                        if params:
                            components.append({"type": "header", "parameters": params})
            
            elif comp_type == "BODY":
                # Body text parameters
                text = comp.get("text", "")
                # Count placeholders like {{1}}, {{2}}, etc.
                import re
                placeholders = re.findall(r'\{\{(\d+)\}\}', text)
                if placeholders:
                    params = []
                    for idx in placeholders:
                        var_key = f"body_{idx}"
                        value = variables.get(var_key, variables.get(f"body{idx}", ""))
                        params.append({"type": "text", "text": str(value)})
                    if params:
                        components.append({"type": "body", "parameters": params})
            
            elif comp_type == "BUTTONS":
                # Handle button components
                buttons = comp.get("buttons", [])
                for idx, btn in enumerate(buttons):
                    btn_type = btn.get("type", "").upper()
                    
                    if btn_type == "QUICK_REPLY":
                        # Encode flow routing info in quick reply button payload
                        btn_text = btn.get("text", "")
                        
                        # Find target node for this button
                        # Try matching by index or by button text
                        target_node_id = (
                            button_mappings.get(str(idx)) or 
                            button_mappings.get(btn_text) or
                            button_mappings.get(f"button_{idx}")
                        )
                        
                        # Encode the payload
                        payload = f"{cls.PAYLOAD_PREFIX}{automation_id}_{target_node_id if target_node_id else 'END'}"
                        
                        components.append({
                            "type": "button",
                            "sub_type": "quick_reply",
                            "index": str(idx),
                            "parameters": [{
                                "type": "payload",
                                "payload": payload
                            }]
                        })
                    
                    elif btn_type == "URL":
                        # URL button with dynamic suffix
                        url_suffix_key = f"url_suffix_{idx}"
                        if url_suffix_key in variables:
                            components.append({
                                "type": "button",
                                "sub_type": "url",
                                "index": str(idx),
                                "parameters": [{
                                    "type": "text",
                                    "text": variables[url_suffix_key]
                                }]
                            })
                    
                    # PHONE_NUMBER buttons don't need additional parameters
        
        return components
    
    def _substitute_variable(self, template: str, variables: Dict[str, Any]) -> str:
        """
        Replace {{var}} placeholders with actual values.
        
        Supports:
            - {{variable_name}} - Simple replacement
            - Static values (no placeholders)
            
        Args:
            template: String possibly containing {{placeholders}}
            variables: Dict of variable_name -> value
            
        Returns:
            String with placeholders replaced
        """
        if not template or '{{' not in template:
            return template
        
        result = template
        for key, value in variables.items():
            placeholder = f"{{{{{key}}}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(value) if value is not None else '')
        
        return result
    
    def _build_header_param(self, header: Dict, variables: Dict) -> Optional[Dict]:
        """
        Build header parameter (image, video, document, or text).
        
        Args:
            header: Header configuration with 'type' and 'value'
            variables: Runtime variables for substitution
            
        Returns:
            Header parameter dict, or None if invalid
        """
        header_type = header.get('type', 'text')
        value = header.get('value', '')
        
        if not value:
            return None
        
        # Substitute any variables in the value
        value = self._substitute_variable(value, variables)
        
        if header_type == 'image':
            return {'type': 'image', 'image': {'link': value}}
        elif header_type == 'video':
            return {'type': 'video', 'video': {'link': value}}
        elif header_type == 'document':
            filename = header.get('filename', 'document')
            return {'type': 'document', 'document': {'link': value, 'filename': filename}}
        else:
            return {'type': 'text', 'text': value}


# Helper function for external use
def decode_flow_button_payload(payload: str) -> Optional[Dict[str, Any]]:
    """
    Convenience function to decode a flow button payload.
    
    Usage:
        from whatsapp.template_node_executor import decode_flow_button_payload
        
        decoded = decode_flow_button_payload(button_payload)
        if decoded:
            automation_id = decoded['automation_id']
            target_node_id = decoded['target_node_id']
    """
    return TemplateNodeExecutor.decode_button_payload(payload)


def is_flow_button_payload(payload: str) -> bool:
    """
    Convenience function to check if a payload is from our flow system.
    
    Usage:
        from whatsapp.template_node_executor import is_flow_button_payload
        
        if is_flow_button_payload(button_payload):
            # Handle as flow button
        else:
            # Handle as regular button
    """
    return TemplateNodeExecutor.is_flow_button_payload(payload)
