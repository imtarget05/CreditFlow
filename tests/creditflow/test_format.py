"""Tests for the ledger format module."""

def test_ledger_format_new():
    """Test that a new LedgerEntry can be created with action='test'."""
    from src.creditflow.ledger.format import LedgerEntry

    entry = LedgerEntry(timestamp=1.0, action="test", data={})
    assert entry.action == "test"