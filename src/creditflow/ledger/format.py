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