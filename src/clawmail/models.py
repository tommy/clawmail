"""Pydantic models shared across modules."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    none = "none"
    flag = "flag"
    move = "move"
    trash = "trash"
    archive = "archive"


class EmailSummary(BaseModel):
    """Parsed email ready for classification."""

    uid: int
    message_id: str = ""
    subject: str = ""
    sender: str = ""
    date: datetime | None = None
    snippet: str = Field(default="", description="Body truncated to ~500 chars")
    has_attachments: bool = False
    flags: list[str] = Field(default_factory=list)


class CategoryRule(BaseModel):
    """A single triage category from config."""

    name: str
    description: str = ""
    action: ActionType = ActionType.none
    target_folder: str | None = None
    older_than_minutes: int | None = None


class EmailClassification(BaseModel):
    """What Claude returns: just the classification, no action details."""

    email_uid: int = Field(description="UID of the email being classified")
    category: str = Field(description="Category name from the rules")
    confidence: float = Field(ge=0, le=1, description="Confidence score 0-1")
    reasoning: str = Field(description="Brief explanation of classification")


class ClassificationResult(BaseModel):
    """Top-level model for structured output from Claude."""

    classifications: list[EmailClassification]


class CategorySuggestion(BaseModel):
    """A suggested new category Claude thinks would be useful."""

    name: str = Field(description="Short snake_case name for the category")
    description: str = Field(description="What emails this category would match")
    suggested_action: ActionType = Field(
        description="Recommended action: none, flag, move, trash, or archive"
    )
    example_uids: list[int] = Field(
        default_factory=list,
        description="UIDs from the current batch that would fit this category",
    )
    reasoning: str = Field(description="Why this category would be useful")


class SuggestionsResult(BaseModel):
    """Structured output for category suggestions."""

    suggestions: list[CategorySuggestion]


class EmailAction(BaseModel):
    """Resolved action: classification + rule-derived action/target."""

    email_uid: int
    category: str
    confidence: float
    reasoning: str
    action: ActionType
    target_folder: str | None = None
