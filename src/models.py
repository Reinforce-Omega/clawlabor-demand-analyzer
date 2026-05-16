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


class SchemaField(BaseModel):
    name: str
    type: str
    description: str
    required: bool = True


class IOSchema(BaseModel):
    fields: list[SchemaField]


class LLMAnalysis(BaseModel):
    category: str
    complexity: Literal["low", "medium", "high"]
    required_capabilities: list[str]
    suggested_agent_name: str
    suggested_agent_description: str
    input_schema: IOSchema
    output_schema: IOSchema
    priority_score: float = Field(ge=0.0, le=1.0)
    reasoning: str


# OpenAI function-calling tool definition — kept in sync with LLMAnalysis.
ANALYSIS_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "analyze_demand",
        "description": (
            "Analyze an unmet user demand and return a structured agent proposal "
            "matching the marketplace schema."
        ),
        "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Demand category, e.g. 'data processing', 'content generation'",
            },
            "complexity": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "required_capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Skills the ideal agent would need",
            },
            "suggested_agent_name": {"type": "string"},
            "suggested_agent_description": {"type": "string"},
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
                "description": "0–1 estimate of demand urgency/value",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief rationale for the proposal",
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
