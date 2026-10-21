"""Shared contracts for the external integrations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..schemas import DocRecord, ExecutionReceipt


class IntegrationError(Exception):
    """Raised when an external integration cannot complete the action."""


class MissingCredentialsError(IntegrationError):
    """Raised when the credentials required for a live integration are absent."""


@runtime_checkable
class DocumentationAppender(Protocol):
    """Appends a structured record to a documentation service."""

    name: str
    simulated: bool

    def append(self, record: DocRecord, markdown: str) -> ExecutionReceipt:
        ...


@runtime_checkable
class EmailDrafter(Protocol):
    """Creates (never sends) an email draft."""

    name: str
    simulated: bool

    def create_draft(self, subject: str, body: str, recipient: str | None = None) -> ExecutionReceipt:
        ...
