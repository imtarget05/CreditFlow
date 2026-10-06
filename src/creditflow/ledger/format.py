"""Ledger entry dataclass — EXPERIMENTAL / NON-CANONICAL prototype.

Part of ``src/creditflow/ledger/``: a small research prototype of
checkpoint/recovery semantics, NOT the runtime ledger. Canonical runtime
ledger = ``pipeline/storage/ledger.py`` (used by ``backend/app.py``).
Retained because ``tests/creditflow/test_format.py`` exercises it.
"""

from dataclasses import dataclass


@dataclass
class LedgerEntry:
    """Represents a single entry in the creditflow ledger.

    Attributes:
        timestamp: Unix timestamp when the entry was created.
        action: The action performed (e.g., "test", "approve", "reject").
        data: Additional data associated with the action.
        checkpoint: Whether this entry is a checkpoint/state snapshot.
    """

    timestamp: float
    action: str
    data: dict
    checkpoint: bool = False