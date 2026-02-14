"""Anthropic API structured output integration for email classification."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import anthropic

from clawmail.models import (
    ActionType,
    CategoryRule,
    ClassificationResult,
    EmailAction,
    EmailSummary,
    SuggestionsResult,
)


class EmailClassifier:
    """Classifies emails using Claude with structured output."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5",
        max_tokens: int = 1024,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def classify(
        self,
        emails: list[EmailSummary],
        categories: list[CategoryRule],
        system_prompt: str,
    ) -> tuple[list[EmailAction], dict]:
        """Classify a batch of emails using Claude structured output.

        Returns (actions, usage) where actions have rule-derived action/target.
        """
        if not emails:
            return [], {"input_tokens": 0, "output_tokens": 0}

        full_system = self._build_system_prompt(system_prompt, categories)
        user_message = self._build_user_message(emails)

        # ~100 output tokens per email for structured JSON + overhead
        required_tokens = max(self.max_tokens, len(emails) * 100 + 256)

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=required_tokens,
            system=full_system,
            messages=[{"role": "user", "content": user_message}],
            output_format=ClassificationResult,
        )

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        result = response.parsed_output

        # Resolve classifications into actions using config rules
        actions = []
        category_map = {c.name: c for c in categories}
        email_map = {e.uid: e for e in emails}
        now = datetime.now(timezone.utc)

        for c in result.classifications:
            if c.email_uid not in email_map:
                continue
            rule = category_map.get(c.category)
            if not rule:
                continue

            action = rule.action
            target_folder = rule.target_folder

            # If the rule has an age gate, downgrade to "none" for young emails
            if rule.older_than_minutes is not None:
                email_date = email_map[c.email_uid].date
                if email_date is not None:
                    if email_date.tzinfo is None:
                        email_date = email_date.replace(tzinfo=timezone.utc)
                    age_minutes = (now - email_date).total_seconds() / 60
                    if age_minutes < rule.older_than_minutes:
                        action = ActionType.none
                        target_folder = None

            actions.append(
                EmailAction(
                    email_uid=c.email_uid,
                    category=c.category,
                    confidence=c.confidence,
                    reasoning=c.reasoning,
                    action=action,
                    target_folder=target_folder,
                )
            )

        return actions, usage

    def _build_system_prompt(
        self, base_prompt: str, categories: list[CategoryRule]
    ) -> str:
        """Build full system prompt with category descriptions."""
        lines = [base_prompt.strip(), "", "Available categories:"]
        for cat in categories:
            lines.append(f"- {cat.name}: {cat.description}")

        lines.extend(
            [
                "",
                "For each email, assign exactly one category, a confidence score (0-1),",
                "and a brief reasoning. Return results for ALL emails in the batch.",
            ]
        )
        return "\n".join(lines)

    def _build_user_message(self, emails: list[EmailSummary]) -> str:
        """Build user message as JSON array of email summaries."""
        fields = {"uid", "subject", "sender", "date", "snippet", "has_attachments"}
        summaries = [
            e.model_dump(include=fields, mode="json") for e in emails
        ]
        return "Classify the following emails:\n\n" + json.dumps(
            summaries, indent=2, ensure_ascii=False
        )

    def suggest_categories(
        self,
        emails: list[EmailSummary],
        existing_categories: list[CategoryRule],
        actions: list[EmailAction],
        suggestions_prompt: str,
    ) -> tuple[SuggestionsResult, dict]:
        """Suggest new categories based on the email batch and classification results.

        Returns (suggestions, usage).
        """
        system = (
            "You are an email triage assistant. The user has these existing categories:\n"
            + "\n".join(f"- {c.name}: {c.description}" for c in existing_categories)
            + "\n\n"
            + suggestions_prompt.strip()
            + "\n\nOnly suggest categories that are clearly distinct from the existing ones. "
            "If the existing categories already cover everything well, return an empty list."
        )

        # Build user message with both emails and how they were classified
        email_summaries = self._build_user_message(emails)
        email_map = {e.uid: e for e in emails}
        classification_lines = []
        for a in actions:
            subj = email_map[a.email_uid].subject if a.email_uid in email_map else "?"
            classification_lines.append(
                {
                    "uid": a.email_uid,
                    "subject": subj,
                    "category": a.category,
                    "confidence": a.confidence,
                    "reasoning": a.reasoning,
                }
            )
        user_message = (
            email_summaries
            + "\n\nHere is how these emails were classified:\n\n"
            + json.dumps(classification_lines, indent=2, ensure_ascii=False)
        )

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user_message}],
            output_format=SuggestionsResult,
        )

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        return response.parsed_output, usage

    def test_connection(self) -> bool:
        """Test that the Anthropic API key works."""
        try:
            self.client.messages.create(
                model=self.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "Say 'ok'"}],
            )
            return True
        except Exception:
            return False
