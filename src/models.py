from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


@dataclass
class DemandEvent:
    message_id: str
    agent_id: str
    description: str
    signal_type: str
    created_at: str
    delivery_count: int = 1


# Capability taxonomy — mirrors clawlabor-api/app/core/capability_categories.py.
# Kept inline (not imported) because this service is an independent deployable.
CategorySlug = Literal[
    "research_analysis",
    "writing_content",
    "code_engineering",
    "data_automation",
    "design_image",
    "audio_video",
    "business_ops",
    "legal_finance",
    "education_coaching",
    "agent_integration",
    "other",
]

CATEGORY_SLUGS: tuple[str, ...] = (
    "research_analysis",
    "writing_content",
    "code_engineering",
    "data_automation",
    "design_image",
    "audio_video",
    "business_ops",
    "legal_finance",
    "education_coaching",
    "agent_integration",
    "other",
)

CATEGORY_LABELS: dict[str, str] = {
    "research_analysis": "Research & Analysis",
    "writing_content": "Writing & Content",
    "code_engineering": "Code & Engineering",
    "data_automation": "Data & Automation",
    "design_image": "Design & Image",
    "audio_video": "Audio & Video",
    "business_ops": "Business & Ops",
    "legal_finance": "Legal & Finance",
    "education_coaching": "Education & Coaching",
    "agent_integration": "Agent Integration",
    "other": "Other",
}


class SchemaField(BaseModel):
    name: str
    type: str
    description: str
    required: bool = True


class IOSchema(BaseModel):
    fields: list[SchemaField]


class DemandRewrite(BaseModel):
    """Stage-1 output: generic task shape derived from raw demand description.

    Mirrors the match-side query rewrite so demand signals and SKU retrieval
    speak the same vocabulary.
    """

    source_language: str
    normalized_task: str
    category_hints: list[CategorySlug] = Field(default_factory=list)
    capability_terms: list[str] = Field(default_factory=list)
    desired_outputs: list[str] = Field(default_factory=list)
    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    negative_capability_terms: list[str] = Field(default_factory=list)
    constraints: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)


class LLMAnalysis(BaseModel):
    """Stage-2 output: generic SKU proposal derived from the rewrite."""

    category: CategorySlug
    complexity: Literal["low", "medium", "high"]
    required_capabilities: list[str]
    suggested_agent_name: str
    suggested_agent_description: str
    input_schema: IOSchema
    output_schema: IOSchema
    priority_score: float = Field(ge=0.0, le=1.0)
    reasoning: str


# ---------------------------------------------------------------------------
# OpenAI function-calling tool definitions
# ---------------------------------------------------------------------------

REWRITE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "rewrite_demand",
        "description": (
            "Normalize a raw buyer demand description into generic, "
            "instance-noise-free retrieval terms for the marketplace."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_language": {
                    "type": "string",
                    "description": "ISO 639-1 code of the original description, or 'mixed'.",
                },
                "normalized_task": {
                    "type": "string",
                    "description": "One concise English sentence preserving the user's desired outcome.",
                },
                "category_hints": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(CATEGORY_SLUGS)},
                    "description": "0-3 capability category slugs that may apply. Best fit first.",
                },
                "capability_terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-10 generic English capability phrases. No SKU ids, no proper nouns.",
                },
                "desired_outputs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "0-5 English output goal phrases.",
                },
                "input_artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "0-5 generic input artifact/modality phrases (e.g. 'PDF document', 'image URL').",
                },
                "output_artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "0-5 generic output artifact phrases (e.g. 'markdown report', 'structured JSON').",
                },
                "negative_capability_terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "0-5 phrases for clearly irrelevant capabilities.",
                },
                "constraints": {
                    "type": "object",
                    "description": (
                        "Task-shape constraints only (language, format, framework, deadline, "
                        "platform, count). No instance keys like 'repository', 'ticket', 'customer'."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
            },
            "required": [
                "source_language",
                "normalized_task",
                "category_hints",
                "capability_terms",
                "desired_outputs",
                "input_artifacts",
                "output_artifacts",
                "negative_capability_terms",
                "constraints",
                "confidence",
            ],
        },
    },
}


ANALYSIS_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "propose_agent",
        "description": (
            "Propose a generic marketplace SKU/agent that would fulfill the "
            "rewritten demand. Use ONLY the rewrite — never reference instance "
            "noise (proper nouns, filenames, URLs, dates, ticket ids)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": list(CATEGORY_SLUGS),
                    "description": (
                        "MUST be the first entry of rewrite.category_hints when present; "
                        "otherwise 'other'."
                    ),
                },
                "complexity": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
                "required_capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "3-8 generic capability phrases the ideal agent needs. "
                        "Derived from rewrite.capability_terms; no proper nouns."
                    ),
                },
                "suggested_agent_name": {
                    "type": "string",
                    "description": (
                        "Generic agent name. MUST NOT contain proper nouns "
                        "(company names, product names, person names, brand names). "
                        "Bad: 'DeepSeek Activity Summarizer'. Good: 'Company Activity Summarizer'."
                    ),
                },
                "suggested_agent_description": {
                    "type": "string",
                    "description": (
                        "Generic agent description — describes the capability shape, "
                        "not the specific instance that triggered this demand."
                    ),
                },
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fields": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "type": {
                                        "type": "string",
                                        "description": "e.g. 'string', 'number', 'boolean', 'url'",
                                    },
                                    "description": {"type": "string"},
                                    "required": {"type": "boolean"},
                                },
                                "required": ["name", "type", "description", "required"],
                            },
                        }
                    },
                    "required": ["fields"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "fields": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "type": {
                                        "type": "string",
                                        "description": "e.g. 'string', 'number', 'url'",
                                    },
                                    "description": {"type": "string"},
                                },
                                "required": ["name", "type", "description"],
                            },
                        }
                    },
                    "required": ["fields"],
                },
                "priority_score": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "0–1 estimate of demand urgency/value.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief rationale referencing the rewrite, not raw description.",
                },
            },
            "required": [
                "category",
                "complexity",
                "required_capabilities",
                "suggested_agent_name",
                "suggested_agent_description",
                "input_schema",
                "output_schema",
                "priority_score",
                "reasoning",
            ],
        },
    },
}
