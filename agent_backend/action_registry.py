"""
Action Registry — Plugin system for Agent actions.
====================================================

Provides:
- BaseAction abstract class
- ActionRegistry singleton for action registration & lookup
- Auto-discovery of action modules in actions/ folder

New features just drop a file in actions/ with @action_registry.register(...)
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


class BaseAction(ABC):
    """
    Abstract base class that every agent action must inherit from.

    Subclass must define:
        name:            str   – unique human-readable identifier
        domain:          str   – domain group (template, drip, bulk, …)
        action:          str   – verb inside the domain (list, create, …)
        required_params: list  – params that *must* be present to execute
        optional_params: list  – params the user *may* supply
        description:     str   – one-line help text shown to Gemini / user
    """

    name: str = ""
    domain: str = ""
    action: str = ""
    required_params: List[str] = []
    optional_params: List[str] = []
    description: str = ""

    # ---- Validation helpers ------------------------------------------------

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Check whether all *required_params* are present.

        Returns (is_valid, list_of_missing_param_names).
        """
        missing = [p for p in self.required_params if p not in params or params[p] is None]
        return (len(missing) == 0), missing

    def get_missing_params_prompt(self, missing: List[str]) -> str:
        """
        Return a human-readable question asking the user
        to supply the *missing* params.

        Override in subclasses for domain-specific phrasing.
        """
        if not missing:
            return ""
        nice = ", ".join(f"**{m}**" for m in missing)
        return f"I still need the following information: {nice}. Could you provide them?"

    # ---- Execution ---------------------------------------------------------

    @abstractmethod
    def execute(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run the action.

        Args:
            params:  Validated param dict.
            context: Extra context — workspace_id, account_id, user info, …

        Returns dict with at least:
            status:  'success' | 'error'
            message: human-readable summary
        Optional keys:
            data, navigate_to, ui_action, next_step
        """
        ...

    # ---- Serialisation (for /capabilities endpoint) ------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "action": self.action,
            "description": self.description,
            "required_params": self.required_params,
            "optional_params": self.optional_params,
        }


# ============================================================
# Registry singleton
# ============================================================

class ActionRegistry:
    """
    Thread-safe singleton that maps (domain, action) → BaseAction instance.
    """

    _instance: Optional["ActionRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._registry = {}
        return cls._instance

    _registry: Dict[str, Dict[str, BaseAction]]

    # ---- Registration ------------------------------------------------------

    def register(self, domain: str, action: str):
        """
        Decorator for registering an action class.

        Usage::

            @action_registry.register(domain="drip", action="create")
            class CreateDripAction(BaseAction):
                ...
        """

        def decorator(cls):
            instance = cls()
            instance.domain = domain
            instance.action = action
            if not instance.name:
                instance.name = f"{domain}_{action}"
            self._registry.setdefault(domain, {})[action] = instance
            logger.info("Registered action  %s/%s  → %s", domain, action, cls.__name__)
            return cls

        return decorator

    # ---- Lookup ------------------------------------------------------------

    def get(self, domain: str, action: str) -> Optional[BaseAction]:
        return self._registry.get(domain, {}).get(action)

    def list_actions(self, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return serialised list of registered actions (for /capabilities)."""
        actions = []
        for d, acts in self._registry.items():
            if domain and d != domain:
                continue
            for a, handler in acts.items():
                actions.append(handler.to_dict())
        return actions

    def list_domains(self) -> List[str]:
        return list(self._registry.keys())

    def get_schema_for_intent_parser(self) -> List[Dict[str, Any]]:
        """
        Build a compact schema that the intent parser can inject into
        Gemini's system prompt so it knows what actions are available.
        """
        schema = []
        for domain, acts in self._registry.items():
            for action_name, handler in acts.items():
                schema.append({
                    "domain": domain,
                    "action": action_name,
                    "description": handler.description,
                    "required_params": handler.required_params,
                    "optional_params": handler.optional_params,
                })
        return schema


# Module-level singleton
action_registry = ActionRegistry()
