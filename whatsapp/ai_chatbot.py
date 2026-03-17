"""
WhatsApp AI Chatbot
===================

AI-powered conversational responses for WhatsApp automation.
Production-grade implementation with RAG integration.

Key Design Principles:
- FAIL-SAFE: AI errors never break message flow
- WORKSPACE-ISOLATED: RAG context scoped per workspace
- CONCISE: Responses optimized for WhatsApp (short, no markdown)
- MULTI-LANGUAGE: Detects and responds in user's language

Uses Google Generative AI for:
- Intent Classification
- Response Generation (with RAG context)
"""

import os
import re
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from pathlib import Path

# New GenAI SDK
from google import genai
from google.genai.types import HttpOptions, GenerateContentConfig

logger = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================

# Model for chat responses
DEFAULT_MODEL = os.environ.get("TEXT_MODEL", "gemini-2.0-flash-exp")

# RAG configuration
RAG_INDEX_BASE_DIR = Path(os.environ.get("KNOWLEDGE_INDEX_DIR", "faiss_indexes"))
RAG_CONFIDENCE_THRESHOLD = 0.5
RAG_TOP_K = 5

# Safety settings not needed for new SDK in same format
# We'll configure them in the call if needed

# Default system prompt - optimized for WhatsApp business conversations
DEFAULT_SYSTEM_PROMPT = """You are a helpful WhatsApp assistant for a business.

COMMUNICATION STYLE:
- Be polite, professional, and concise
- Keep responses SHORT (under 150 characters is ideal for WhatsApp)
- Use simple language, avoid jargon
- Be friendly but professional
- If you don't know something, admit it honestly

FORMATTING RULES (CRITICAL):
- NO markdown: no **, *, _, `, ##, - bullets
- Plain text only
- No emojis unless the customer uses them first
- Use commas for lists, not bullet points

LANGUAGE MATCHING (CRITICAL):
- Always match the customer's language
- If they write in Hindi → reply in Hindi
- If they write in Hinglish → reply in Hinglish  
- If they write in Telugu → reply in Telugu
- If they write in Tinglish → reply in Tinglish
- If they write in English → reply in English

BEHAVIOR:
- For complex issues, suggest speaking with a human agent
- Never share sensitive information like passwords or payment details
- If asked about something not in your knowledge, say you'll check and get back"""


# ============================================================
# GenAI Configuration
# ============================================================

_genai_client = None


def get_genai_client():
    """Get or initialize the GenAI client (Vertex mode)."""
    global _genai_client
    
    if _genai_client:
        return _genai_client
    
    project = os.environ.get("GCP_PROJECT") or os.environ.get("PROJECT_ID") or "angular-sorter-473216-k8"
    location = os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
    
    try:
        logger.info(f"Initializing Vertex AI Client: project={project}, location={location}")
        _genai_client = genai.Client(
            http_options=HttpOptions(api_version="v1"),
            project=project,
            location=location,
            vertexai=True,
        )
        logger.info("✅ GenAI Client initialized (Vertex mode)")
        return _genai_client
    except Exception as e:
        logger.error(f"❌ GenAI Client init failed: {e}")
        return None


# ============================================================
# Data Classes
# ============================================================

@dataclass
class ChatResponse:
    """Result of AI chat response generation."""
    message: str
    success: bool = True
    error: Optional[str] = None
    tokens_used: int = 0
    model_used: str = DEFAULT_MODEL
    response_time_ms: int = 0
    used_rag: bool = False
    rag_chunks: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": self.message,
            "success": self.success,
            "error": self.error,
            "tokens_used": self.tokens_used,
            "model_used": self.model_used,
            "response_time_ms": self.response_time_ms,
            "used_rag": self.used_rag,
            "rag_chunks": self.rag_chunks,
        }


@dataclass
class AIConfig:
    """AI configuration for an account."""
    enabled: bool = False
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    model: str = DEFAULT_MODEL
    max_tokens: int = 256
    temperature: float = 0.7
    fallback_message: str = "I'm sorry, I couldn't process your request. A team member will assist you soon."
    context_messages: int = 5
    # RAG Configuration
    use_rag: bool = True
    rag_top_k: int = 5
    rag_confidence_threshold: float = 0.5
    workspace_id: Optional[str] = None


@dataclass
class IntentResult:
    """Result of intent classification."""
    intent: str
    confidence: float = 1.0
    success: bool = True
    error: Optional[str] = None
    response_time_ms: int = 0
    entities: Dict[str, Any] = field(default_factory=dict)
    language: str = "en"
    missing_params: List[str] = field(default_factory=list)
    clarification_needed: bool = False
    friendly_message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "success": self.success,
            "error": self.error,
            "response_time_ms": self.response_time_ms,
            "entities": self.entities,
            "language": self.language,
            "missing_params": self.missing_params,
            "clarification_needed": self.clarification_needed,
            "friendly_message": self.friendly_message,
        }


# ============================================================
# Intent Classification
# ============================================================

INTENT_CLASSIFICATION_PROMPT = """Analyze the message and output a JSON object:
{{
    "intent": "<category>",
    "entities": {{"key": "value"}},
    "language": "<detected language code>",
    "friendly_message": "<brief acknowledgment>"
}}

INTENT CATEGORIES:
- greeting: Hello, hi, good morning
- support: Technical help, issues, problems
- sales: Pricing, buy, purchase
- info: General questions, features
- complaint: Unhappy, refund request
- appointment: Schedule, book, demo
- order_status: Where is my order, tracking
- payment: Payment issues, invoice
- faq: Common questions
- other: Unclear

MESSAGE: "{message}"

JSON:"""

INTENT_TYPES = ["greeting", "support", "sales", "info", "complaint", "appointment", "order_status", "payment", "faq", "other"]


def classify_intent(message: str, model_name: Optional[str] = None) -> IntentResult:
    """Classify the intent of a customer message. FAIL-SAFE."""
    import time
    start_time = time.time()
    
    try:
        client = get_genai_client()
        if not client:
            return IntentResult(intent="other", success=False, error="API not configured")
        
        prompt = INTENT_CLASSIFICATION_PROMPT.format(message=message[:300])
        
        response = client.models.generate_content(
            model=model_name or DEFAULT_MODEL,
            contents=prompt,
            config=GenerateContentConfig(
                max_output_tokens=256,
                temperature=0.1
            )
        )
        
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        if not response.text:
            return IntentResult(intent="other", success=False, error="Empty response", response_time_ms=elapsed_ms)
        
        text = response.text.strip()
        if "```" in text:
            text = re.sub(r'```json\s*|\s*```', '', text)
        
        try:
            result = json.loads(text)
        except:
            match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if match:
                result = json.loads(match.group())
            else:
                return IntentResult(intent="other", confidence=0.5, response_time_ms=elapsed_ms)
        
        intent = result.get("intent", "other").lower()
        if intent not in INTENT_TYPES:
            intent = "other"
        
        return IntentResult(
            intent=intent,
            confidence=result.get("confidence", 0.9),
            entities=result.get("entities", {}),
            language=result.get("language", "en"),
            friendly_message=result.get("friendly_message", ""),
            success=True,
            response_time_ms=elapsed_ms
        )
        
    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.exception(f"Intent classification failed: {e}")
        return IntentResult(intent="other", success=False, error=str(e), response_time_ms=elapsed_ms)


# ============================================================
# RAG Integration
# ============================================================

def get_rag_module():
    """Import RAG module safely."""
    try:
        import sys
        parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        import rag
        return rag
    except ImportError as e:
        logger.warning(f"RAG module not available: {e}")
        return None


def get_rag_context(query: str, workspace_id: int, top_k: int = 5, threshold: float = 0.25) -> Tuple[List[Dict], bool]:
    """
    Retrieve relevant context from knowledge base using Qdrant Cloud.
    Uses workspace_id for multi-tenant isolation.
    
    FAIL-SAFE: Returns empty on any error.
    """
    if not workspace_id:
        return [], False
    
    rag = get_rag_module()
    if not rag:
        return [], False
    
    try:
        # Use cloud retrieval with context window
        results, stats = rag.retrieve_with_context_window(
            query=query,
            workspace_id=int(workspace_id),
            top_k=top_k,
            score_threshold=threshold
        )
        
        if not results:
            return [], False
        
        # Log for debugging
        logger.info(f"RAG retrieved {len(results)} chunks for workspace_id={workspace_id}, timing={stats}")
        
        top_score = results[0].get("score", 0)
        return results, top_score >= threshold
        
    except Exception as e:
        logger.warning(f"RAG retrieval failed: {e}")
        return [], False


def build_rag_enhanced_message(user_message: str, rag_chunks: List[Dict]) -> str:
    """
    Build message with RAG context and multilingual script-matching rules.
    Includes strict no-hallucination instructions.
    """
    if not rag_chunks:
        return user_message
    
    context_parts = []
    for i, c in enumerate(rag_chunks):
        text = c.get('text', '')
        source = c.get('source', 'Unknown')
        score = c.get('score', 0)
        if text:
            # Only include top-scoring chunks for cleaner context
            if score >= 0.3:
                context_parts.append(f"[{source}]:\n{text}")
    
    # Limit context to avoid overwhelming the model
    context_str = "\n---\n".join(context_parts[:5])  # Max 5 chunks
    
    return f"""### KNOWLEDGE BASE (Use ONLY this information):
{context_str}

### CUSTOMER QUESTION: {user_message}

### STRICT RULES:
1. Answer STRICTLY from the knowledge above
2. If information is NOT in the knowledge base, respond: "I don't have that information. Please contact our team."
3. Maximum 50 words, 2-3 sentences
4. NO emojis, NO markdown formatting
5. Plain text only
6. CRITICAL - Match user's language and script:
   - If user writes in English → Reply in English
   - If user writes in Hindi script (देवनागरी) → Reply in Hindi
   - If user writes in Hinglish (kya hai, aap, kitna) → Reply in Hinglish
   - If user writes in Telugu script (తెలుగు) → Reply in Telugu  
   - If user writes in Tinglish (emi, ela, meeru) → Reply in Tinglish
7. Be friendly and professional

RESPONSE:"""


# ============================================================
# AI Chatbot Class
# ============================================================

class WhatsAppAIChatbot:
    """AI-powered chatbot for WhatsApp conversations."""
    
    def __init__(self, config: Optional[AIConfig] = None):
        self.config = config or AIConfig()
        self.client = None
        self._initialized = False
        self._init_error: Optional[str] = None
        self._initialize()
    
    def _initialize(self):
        """Initialize GenAI Vertex Client."""
        try:
            self.client = get_genai_client()
            if not self.client:
                self._init_error = "API not configured"
                return
            
            self._initialized = True
            logger.info(f"AI Chatbot initialized: model={self.config.model}")
            
        except Exception as e:
            self._init_error = str(e)
            logger.exception(f"AI Chatbot init failed: {e}")
    
    def is_available(self) -> bool:
        return self._initialized and self.client is not None
    
    def generate_response(self, message: str, context: Optional[List[Dict[str, str]]] = None) -> ChatResponse:
        """
        Generate AI response with RAG integration. FAIL-SAFE.
        
        Enhanced behavior:
        - Uses system_prompt from config for Gemini instruction
        - Returns fallback_message when RAG confidence is too low
        - No hallucination: only answers from knowledge base
        """
        import time
        start_time = time.time()
        
        if not self.is_available():
            return ChatResponse(message=self.config.fallback_message, success=False, error=self._init_error)
        
        try:
            # RAG Retrieval
            rag_chunks = []
            high_conf = False
            max_score = 0
            
            if self.config.use_rag and self.config.workspace_id:
                rag_chunks, high_conf = get_rag_context(
                    query=message,
                    workspace_id=self.config.workspace_id,
                    top_k=self.config.rag_top_k,
                    threshold=self.config.rag_confidence_threshold
                )
                
                # Get max score for logging
                if rag_chunks:
                    max_score = max(c.get('score', 0) for c in rag_chunks)
                
                logger.info(f"RAG result: chunks={len(rag_chunks)}, high_conf={high_conf}, max_score={max_score:.3f}, threshold={self.config.rag_confidence_threshold}")
            
            # CRITICAL: If RAG is enabled but no high-confidence results,
            # return fallback to avoid hallucination
            if self.config.use_rag and self.config.workspace_id and not high_conf:
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(f"RAG confidence too low ({max_score:.3f}), returning fallback message")
                
                # Check if it's a greeting - allow simple responses
                greeting_patterns = ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 
                                    'good evening', 'namaste', 'namaskar', 'vannakkam']
                is_greeting = any(g in message.lower() for g in greeting_patterns)
                
                if is_greeting:
                    # Allow AI to respond to greetings naturally
                    pass  # Continue to AI response
                else:
                    return ChatResponse(
                        message=self.config.fallback_message,
                        success=True,  # Not an error, just low confidence
                        response_time_ms=elapsed_ms,
                        used_rag=True,
                        rag_chunks=len(rag_chunks),
                        error=f"RAG confidence {max_score:.3f} below threshold {self.config.rag_confidence_threshold}"
                    )
            
            # Build enhanced message with RAG context
            enhanced_message = message
            if high_conf and rag_chunks:
                enhanced_message = build_rag_enhanced_message(message, rag_chunks)
            
            # Build effective system prompt
            # Combine user's custom system prompt with our RAG rules
            effective_system_prompt = self.config.system_prompt
            if not effective_system_prompt or effective_system_prompt == DEFAULT_SYSTEM_PROMPT:
                effective_system_prompt = DEFAULT_SYSTEM_PROMPT
            
            # Add anti-hallucination rules if using RAG
            if self.config.use_rag and high_conf:
                effective_system_prompt += """

CRITICAL RULES (NEVER VIOLATE):
- Answer ONLY from the provided knowledge base context
- If the answer is not in the knowledge base, say "I don't have that information"
- NEVER make up information, prices, policies, or features
- Keep responses under 50 words for WhatsApp
- No emojis unless user uses them
- Match user's language (Hindi, Hinglish, Telugu, English)"""
            
            response = self.client.models.generate_content(
                model=self.config.model,
                contents=enhanced_message, 
                config=GenerateContentConfig(
                    max_output_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    system_instruction=effective_system_prompt,
                )
            )
            
            response_text = self._clean_response(response.text.strip()) if response.text else ""
            
            # Safety: If response is empty or too short, use fallback
            if not response_text or len(response_text) < 5:
                return ChatResponse(
                    message=self.config.fallback_message,
                    success=False,
                    error="Empty response from AI"
                )
            
            if len(response_text) > 1000:
                response_text = response_text[:997] + "..."
            
            tokens = 0 # Usage metadata handling differs in new SDK
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            return ChatResponse(
                message=response_text,
                success=True,
                tokens_used=tokens,
                model_used=self.config.model,
                response_time_ms=elapsed_ms,
                used_rag=len(rag_chunks) > 0,
                rag_chunks=len(rag_chunks),
            )
            
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.exception(f"AI response failed: {e}")
            return ChatResponse(message=self.config.fallback_message, success=False, error=str(e), response_time_ms=elapsed_ms)
    
    def _clean_response(self, text: str) -> str:
        """Remove markdown formatting."""
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        text = re.sub(r'~~(.+?)~~', r'\1', text)
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
        return text.strip()


# ============================================================
# Factory Functions
# ============================================================

def create_ai_chatbot(config_dict: Optional[Dict[str, Any]] = None) -> WhatsAppAIChatbot:
    """Factory function to create AI chatbot."""
    if config_dict:
        config = AIConfig(
            enabled=config_dict.get("enabled", False),
            system_prompt=config_dict.get("system_prompt", DEFAULT_SYSTEM_PROMPT),
            model=config_dict.get("model", DEFAULT_MODEL),
            max_tokens=config_dict.get("max_tokens", 256),
            temperature=config_dict.get("temperature", 0.7),
            fallback_message=config_dict.get("fallback_message", AIConfig.fallback_message),
            context_messages=config_dict.get("context_messages", 5),
            use_rag=config_dict.get("use_rag", True),
            rag_top_k=config_dict.get("rag_top_k", 5),
            rag_confidence_threshold=config_dict.get("rag_confidence_threshold", 0.5),
            workspace_id=config_dict.get("workspace_id"),
        )
    else:
        config = AIConfig()
    
    return WhatsAppAIChatbot(config=config)


def generate_ai_response(
    message: str,
    system_prompt: Optional[str] = None,
    context: Optional[List[Dict[str, str]]] = None,
    fallback_message: Optional[str] = None,
    workspace_id: Optional[str] = None,
    use_rag: bool = True,
) -> ChatResponse:
    """Convenience function to generate AI response."""
    config = AIConfig(
        enabled=True,
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        fallback_message=fallback_message or AIConfig.fallback_message,
        use_rag=use_rag,
        workspace_id=workspace_id,
    )
    
    chatbot = WhatsAppAIChatbot(config=config)
    return chatbot.generate_response(message=message, context=context)


# ============================================================
# Testing
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("Testing AI Chatbot...")
    
    print("\n1. Testing intent classification...")
    r = classify_intent("Hello, what services do you offer?")
    print(f"   Intent: {r.intent}, Success: {r.success}")
    
    print("\n2. Testing response generation...")
    resp = generate_ai_response("Hello!", system_prompt="You are a helpful assistant.")
    print(f"   Success: {resp.success}")
    print(f"   Message: {resp.message}")
    
    print("\nDone!")
