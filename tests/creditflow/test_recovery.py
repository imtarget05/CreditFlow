"""Tests for the ledger recovery module."""

import os
import tempfile

from src.creditflow.ledger.format import LedgerEntry
from src.creditflow.ledger.recovery import recover_ledger


def test_recovery_on_restart():
    """Test that data survives restart via checkpoint recovery."""
    from src.creditflow.ledger.checkpoint import save_checkpoint

    # Create some test entries
    entry1 = LedgerEntry(timestamp=1.0, action="approve", data={"amount": 100}, checkpoint=False)
    entry2 = LedgerEntry(timestamp=2.0, action="reject", data={"amount": 200}, checkpoint=False)
    entry3 = LedgerEntry(timestamp=3.0, action="approve", data={"amount": 300}, checkpoint=True)

    # Save checkpoint with entries
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        save_checkpoint([entry1, entry2, entry3], tmp_path)

        # Recover ledger on restart
        recovered = recover_ledger(tmp_path)

        # Verify data survived restart
        assert len(recovered) == 3, f"Expected 3 recovered entries, got {len(recovered)}"

        # Verify the non-checkpoint entries are present
        assert recovered[0] == entry1, "First entry should match"
        assert recovered[1] == entry2, "Second entry should match"

        # The checkpoint entry should also be recovered
        assert recovered[2].checkpoint == True, "Checkpoint entry should be recovered"

    finally:
        os.remove(tmp_path)