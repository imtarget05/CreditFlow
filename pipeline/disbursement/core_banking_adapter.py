"""Synthetic core-banking sandbox for local workflow testing.

Bank-grade disbursement orchestration for CreditFlow:

* Maker-checker dual control: the initiator cannot approve the same transaction;
  maker_id and checker_id are tracked on every transaction.
* Idempotency keys: replaying a key returns the cached result with no side
  effects; reusing a key with different parameters raises an error.
* Transaction lifecycle: PENDING -> APPROVED -> COMPLETED | FAILED -> REVERSED.
* Audit trail: every state transition records timestamp, actor, action and
  before/after state.
* VietQR NAPAS 24/7 payload generation via ``pipeline.disbursement.core_banking``.
* Configurable banking sandbox: latency, success/failure/timeout simulation.

The transaction store is an in-memory, thread-safe registry so the adapter is
deterministic and simple to unit test; the public API is store-agnostic.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.security import is_production
from pipeline.disbursement.core_banking import generate_vietqr_disbursement

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_REVERSED = "REVERSED"

_TRANSITIONS: dict[str, set[str]] = {
    STATUS_PENDING: {STATUS_APPROVED},
    STATUS_APPROVED: {STATUS_COMPLETED, STATUS_FAILED},
    STATUS_COMPLETED: {STATUS_REVERSED},
    STATUS_FAILED: set(),
    STATUS_REVERSED: set(),
}


class DisbursementStatus(str, Enum):
    PENDING = STATUS_PENDING
    APPROVED = STATUS_APPROVED
    COMPLETED = STATUS_COMPLETED
    FAILED = STATUS_FAILED
    REVERSED = STATUS_REVERSED


class ReconciliationStatus(str, Enum):
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"
    PENDING = "PENDING"


class DisbursementInitiateRequest(BaseModel):
    application_id: str = Field(..., min_length=1)
    loan_amount: float = Field(..., gt=0)
    account_number: str = Field(..., min_length=1)
    account_name: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=1)
    maker_id: str | None = None


class DisbursementInitiateResponse(BaseModel):
    transaction_id: str
    status: DisbursementStatus = DisbursementStatus.PENDING
    reference_code: str
    qr_quicklink: str
    account_number: str
    account_name: str
    loan_amount: float
    created_at: str


class DisbursementApproveRequest(BaseModel):
    transaction_id: str = Field(..., min_length=1)
    approver_id: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=1)


class DisbursementApproveResponse(BaseModel):
    transaction_id: str
    status: DisbursementStatus = DisbursementStatus.APPROVED
    checker_id: str
    approved_at: str


class DisbursementExecuteRequest(BaseModel):
    transaction_id: str = Field(..., min_length=1)
    idempotency_key: str | None = None


class SandboxTransferResult(BaseModel):
    outcome: str
    bank_reference: str | None = None
    error_message: str | None = None
    delay_seconds: float = 0.0


class DisbursementExecuteResponse(BaseModel):
    transaction_id: str
    status: DisbursementStatus
    bank_reference: str | None = None
    error_message: str | None = None
    completed_at: str | None = None
    delay_seconds: float = 0.0


class DisbursementRollbackRequest(BaseModel):
    transaction_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    approver_id: str = Field(..., min_length=1)
    idempotency_key: str | None = None


class DisbursementRollbackResponse(BaseModel):
    transaction_id: str
    status: DisbursementStatus = DisbursementStatus.REVERSED
    approver_id: str
    reversed_at: str
    reason: str


class TransactionStatusResponse(BaseModel):
    transaction_id: str
    status: DisbursementStatus
    maker_id: str
    checker_id: str | None = None
    reference_code: str | None = None
    bank_reference: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    approved_at: str | None = None
    completed_at: str | None = None
    reversed_at: str | None = None


class AuditEntry(BaseModel):
    timestamp: str
    actor: str
    action: str
    transaction_id: str
    before_state: str | None = None
    after_state: str
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass
class SandboxConfig:
    """Banking sandbox simulation parameters."""

    min_delay_seconds: float = 0.0
    max_delay_seconds: float = 0.0
    success_rate: float = 0.95
    failure_rate: float = 0.04
    timeout_rate: float = 0.01
    enable_network_latency: bool = True

    def __post_init__(self) -> None:
        for name in ("success_rate", "failure_rate", "timeout_rate"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        total = self.success_rate + self.failure_rate + self.timeout_rate
        if total > 1.0:
            raise ValueError("sandbox rates must sum to <= 1.0")
        if self.min_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("delays must be non-negative")
        if self.max_delay_seconds < self.min_delay_seconds:
            raise ValueError("max_delay_seconds must be >= min_delay_seconds")


class CoreBankingAdapter:
    """Demo adapter with maker-checker records and idempotent sandbox calls."""

    def __init__(
        self,
        *,
        default_maker_id: str = "creditflow-system",
        sandbox: SandboxConfig | None = None,
        rng: Callable[[], float] | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        self.default_maker_id = default_maker_id
        self.sandbox = sandbox or SandboxConfig()
        self._rng = rng or (lambda: secrets.randbelow(1_000_000_000) / 1_000_000_000)
        self._lock = threading.RLock()
        self._db_path = Path(db_path) if db_path is not None else None
        # In-memory store (used when no db_path; kept as a read cache too)
        self._transactions: dict[str, dict[str, Any]] = {}
        self._audit_entries: list[dict[str, Any]] = []
        self._idem: dict[str, dict[str, Any]] = {}
        self._external_statements: dict[str, dict[str, Any]] = {}
        if self._db_path is not None:
            from pipeline.storage.ledger import init_db

            init_db(self._db_path)
            init_core_banking_db(self._db_path)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _fingerprint(self, payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def _idem_scope(self, operation: str, idempotency_key: str) -> str:
        return f"corebanking:{operation}:{idempotency_key}"

    def _idem_get(self, scope: str) -> dict[str, Any] | None:
        if self._use_db():
            return self._idem_db_get(scope)
        return self._idem.get(scope)

    def _idem_put(
        self, scope: str, fingerprint: str, response: dict[str, Any]
    ) -> None:
        if self._use_db():
            self._idem_db_put(scope, fingerprint, response)
            return
        self._idem[scope] = {
            "fingerprint": fingerprint,
            "response": response,
            "created_at": self._now(),
        }

    def _audit(
        self,
        transaction_id: str,
        actor: str,
        action: str,
        before_state: str | None,
        after_state: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._use_db():
            self._db_audit_append(
                transaction_id, actor, action, before_state, after_state, details
            )
            return
        self._audit_entries.append(
            {
                "timestamp": self._now(),
                "actor": actor,
                "action": action,
                "transaction_id": transaction_id,
                "before_state": before_state,
                "after_state": after_state,
                "details": details or {},
            }
        )

    def _get(self, transaction_id: str) -> dict[str, Any]:
        if self._use_db():
            tx = self._db_tx_get(transaction_id)
            if tx is None:
                raise LookupError(f"unknown transaction {transaction_id}")
            return tx
        tx = self._transactions.get(transaction_id)
        if tx is None:
            raise LookupError(f"unknown transaction {transaction_id}")
        return tx

    def _simulate_transfer(self) -> SandboxTransferResult:
        cfg = self.sandbox
        delay = 0.0
        if cfg.enable_network_latency:
            lo = cfg.min_delay_seconds
            hi = max(cfg.max_delay_seconds, lo)
            delay = lo + (hi - lo) * self._rng()
        if delay > 0:
            time.sleep(delay)
        roll = self._rng()
        if roll < cfg.timeout_rate:
            return SandboxTransferResult(
                outcome="timeout", error_message="gateway timeout", delay_seconds=delay
            )
        roll -= cfg.timeout_rate
        if roll < cfg.failure_rate:
            return SandboxTransferResult(
                outcome="failed",
                error_message="bank gateway rejected transfer",
                delay_seconds=delay,
            )
        return SandboxTransferResult(
            outcome="completed",
            bank_reference=f"BNK-{uuid.uuid4().hex[:12]}",
            delay_seconds=delay,
        )

    # ------------------------------------------------------------------
    # persistent storage layer (Postgres/SQLite via pipeline.storage.ledger)
    # ------------------------------------------------------------------
    def _use_db(self) -> bool:
        return self._db_path is not None

    def _db_connect(self):
        from pipeline.storage.ledger import _connect

        return _connect(self._db_path)

    def _tx_row_to_dict(self, row) -> dict[str, Any]:
        """Chuyển một dòng core_banking_transactions thành dict nội bộ."""
        return {
            "transaction_id": row["transaction_id"],
            "application_id": row["application_id"],
            "loan_amount": float(row["amount"]),
            "account_number": row["account_number"],
            "account_name": row["account_name"],
            "idempotency_key": row["idempotency_key"],
            "maker_id": row["maker_id"],
            "checker_id": row["checker_id"] or "",
            "status": row["status"],
            "reference_code": row["reference_code"] or "",
            "bank_reference": row["external_reference"] or "",
            "vietqr_payload": {},
            "error_message": row["error_message"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "approved_at": row["approved_at"] or "",
            "completed_at": row["executed_at"] or "",
            "reversed_at": row["reversed_at"] or "",
            "reversal_reason": row["reversal_reason"] or "",
        }

    def _db_tx_get(self, transaction_id: str) -> dict[str, Any] | None:
        with self._db_connect() as conn:
            row = conn.execute(
                "SELECT * FROM core_banking_transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return self._tx_row_to_dict(row) if row is not None else None

    def _db_tx_upsert(self, tx: dict[str, Any]) -> None:
        with self._db_connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM core_banking_transactions WHERE transaction_id = ?",
                (tx["transaction_id"],),
            ).fetchone()
            if exists is None:
                conn.execute(
                    """
                    INSERT INTO core_banking_transactions
                        (transaction_id, application_id, transaction_type, amount,
                         currency, account_number, account_name, error_message,
                         status, maker_id, checker_id, approved_at,
                         executed_at, reversed_at, reversal_reason,
                         reference_code, external_reference,
                         idempotency_key, created_at, updated_at)
                    VALUES (?, ?, 'DISBURSEMENT', ?, 'VND', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tx["transaction_id"],
                        tx["application_id"],
                        float(tx["loan_amount"]),
                        tx["account_number"],
                        tx["account_name"],
                        tx["error_message"],
                        tx["status"],
                        tx["maker_id"],
                        tx["checker_id"] or None,
                        tx["approved_at"] or None,
                        tx["completed_at"] or None,
                        tx["reversed_at"] or None,
                        tx["reversal_reason"] or None,
                        tx["reference_code"] or None,
                        tx["bank_reference"] or None,
                        tx["idempotency_key"],
                        tx["created_at"],
                        tx["updated_at"],
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE core_banking_transactions
                    SET application_id = ?, amount = ?, account_number = ?,
                        account_name = ?, error_message = ?, status = ?,
                        maker_id = ?, checker_id = ?, approved_at = ?,
                        executed_at = ?, reversed_at = ?,
                        reversal_reason = ?, reference_code = ?,
                        external_reference = ?, idempotency_key = ?,
                        updated_at = ?
                    WHERE transaction_id = ?
                    """,
                    (
                        tx["application_id"],
                        float(tx["loan_amount"]),
                        tx["account_number"],
                        tx["account_name"],
                        tx["error_message"],
                        tx["status"],
                        tx["maker_id"],
                        tx["checker_id"] or None,
                        tx["approved_at"] or None,
                        tx["completed_at"] or None,
                        tx["reversed_at"] or None,
                        tx["reversal_reason"] or None,
                        tx["reference_code"] or None,
                        tx["bank_reference"] or None,
                        tx["idempotency_key"],
                        tx["updated_at"],
                        tx["transaction_id"],
                    ),
                )
            conn.commit()

    def _db_audit_append(
        self,
        transaction_id: str,
        actor: str,
        action: str,
        before_state: str | None,
        after_state: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._db_connect() as conn:
            conn.execute(
                """
                INSERT INTO core_banking_audit
                    (transaction_id, action, actor_id, before_state, after_state, timestamp, ip_address)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (transaction_id, action, actor, before_state, after_state, self._now()),
            )
            conn.commit()

    def _idem_db_parse_scope(self, scope: str) -> tuple[str, str]:
        parts = scope.split(":", 2)
        caller_scope = f"{parts[0]}:{parts[1]}" if len(parts) > 2 else scope
        idem_key = parts[2] if len(parts) > 2 else ""
        return caller_scope, idem_key

    def _idem_db_get(self, scope: str) -> dict[str, Any] | None:
        caller_scope, idem_key = self._idem_db_parse_scope(scope)
        with self._db_connect() as conn:
            row = conn.execute(
                "SELECT fingerprint, response FROM idempotency_keys "
                "WHERE caller_scope = ? AND idem_key = ?",
                (caller_scope, idem_key),
            ).fetchone()
        if row is None:
            return None
        try:
            response = json.loads(row["response"]) if row["response"] else {}
        except (ValueError, TypeError):
            response = {}
        return {"fingerprint": row["fingerprint"], "response": response}

    def _idem_db_put(self, scope: str, fingerprint: str, response: dict[str, Any]) -> None:
        caller_scope, idem_key = self._idem_db_parse_scope(scope)
        with self._db_connect() as conn:
            conn.execute(
                """
                INSERT INTO idempotency_keys
                    (caller_scope, idem_key, fingerprint, status, response, expires_at)
                VALUES (?, ?, ?, 'done', ?, ?)
                ON CONFLICT(caller_scope, idem_key) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    status = excluded.status,
                    response = excluded.response,
                    expires_at = excluded.expires_at
                """,
                (
                    caller_scope,
                    idem_key,
                    fingerprint,
                    json.dumps(response, default=str),
                    self._now(),
                ),
            )
            conn.commit()

    def _db_list_transactions(self) -> list[dict[str, Any]]:
        with self._db_connect() as conn:
            rows = conn.execute(
                "SELECT * FROM core_banking_transactions ORDER BY created_at DESC"
            ).fetchall()
        return [self._tx_row_to_dict(r) for r in rows]

    def _db_audit_trail(self, transaction_id: str) -> list[dict[str, Any]]:
        with self._db_connect() as conn:
            rows = conn.execute(
                "SELECT * FROM core_banking_audit WHERE transaction_id = ? "
                "ORDER BY timestamp ASC",
                (transaction_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def initiate_disbursement(
        self,
        application_id: str,
        loan_amount: float,
        account_number: str,
        account_name: str,
        idempotency_key: str,
        maker_id: str | None = None,
    ) -> DisbursementInitiateResponse:
        """Create a PENDING disbursement transaction (maker side).

        Returns the transaction_id. The supplied idempotency_key makes the call
        replay-safe: replaying the same key returns the cached result.
        """
        if is_production():
            raise RuntimeError("SANDBOX_DISABLED_IN_PRODUCTION")
        if not application_id or not idempotency_key:
            raise ValueError("application_id and idempotency_key are required")
        if loan_amount <= 0:
            raise ValueError("loan_amount must be positive")
        if not account_number or not account_name:
            raise ValueError("account_number and account_name are required")

        maker = maker_id or self.default_maker_id
        scope = self._idem_scope("initiate", idempotency_key)
        initiate_payload = {
            "application_id": application_id,
            "loan_amount": loan_amount,
            "account_number": account_number,
            "account_name": account_name,
            "maker_id": maker,
        }

        with self._lock:
            cached = self._idem_get(scope)
            if cached is not None:
                if cached["fingerprint"] != self._fingerprint(initiate_payload):
                    raise ValueError("idempotency_key reused with different parameters")
                return DisbursementInitiateResponse(**cached["response"])

            transaction_id = f"TXN-{uuid.uuid4().hex[:12]}"
            vietqr = generate_vietqr_disbursement(
                contract_code=transaction_id,
                loan_amount=loan_amount,
                account_number=account_number,
                account_name=account_name,
            )
            now = self._now()
            tx: dict[str, Any] = {
                "transaction_id": transaction_id,
                "application_id": application_id,
                "loan_amount": float(loan_amount),
                "account_number": account_number,
                "account_name": account_name,
                "idempotency_key": idempotency_key,
                "maker_id": maker,
                "checker_id": "",
                "status": STATUS_PENDING,
                "reference_code": vietqr.reference_code,
                "bank_reference": "",
                "vietqr_payload": vietqr.model_dump(),
                "error_message": "",
                "created_at": now,
                "updated_at": now,
                "approved_at": "",
                "completed_at": "",
                "reversed_at": "",
                "reversal_reason": "",
            }
            self._transactions[transaction_id] = tx
            if self._use_db():
                self._db_tx_upsert(tx)
            self._audit(
                transaction_id,
                maker,
                "initiate",
                None,
                STATUS_PENDING,
                {
                    "application_id": application_id,
                    "loan_amount": loan_amount,
                    "account_number": account_number,
                    "account_name": account_name,
                },
            )
            response = DisbursementInitiateResponse(
                transaction_id=transaction_id,
                status=DisbursementStatus.PENDING,
                reference_code=vietqr.reference_code,
                qr_quicklink=vietqr.qr_quicklink,
                account_number=account_number,
                account_name=account_name,
                loan_amount=float(loan_amount),
                created_at=now,
            )
            self._idem_put(scope, self._fingerprint(initiate_payload), response.model_dump())
            return response

    def approve_disbursement(
        self,
        transaction_id: str,
        approver_id: str,
        idempotency_key: str,
    ) -> DisbursementApproveResponse:
        """Dual-control approval: transitions PENDING -> APPROVED.

        The approver (checker) must be a different identity from the maker who
        initiated the transaction.
        """
        if is_production():
            raise RuntimeError("SANDBOX_DISABLED_IN_PRODUCTION")
        if not approver_id or not idempotency_key:
            raise ValueError("approver_id and idempotency_key are required")
        scope = self._idem_scope("approve", idempotency_key)
        approve_payload = {"transaction_id": transaction_id, "approver_id": approver_id}

        with self._lock:
            cached = self._idem_get(scope)
            if cached is not None:
                if cached["fingerprint"] != self._fingerprint(approve_payload):
                    raise ValueError("idempotency_key reused with different parameters")
                return DisbursementApproveResponse(**cached["response"])

            tx = self._get(transaction_id)
            if tx["status"] != STATUS_PENDING:
                raise ValueError(
                    f"transaction {transaction_id} is not PENDING (current: {tx['status']})"
                )
            if approver_id == tx["maker_id"]:
                raise ValueError("maker and checker must be different identities")

            tx["status"] = STATUS_APPROVED
            tx["checker_id"] = approver_id
            now = self._now()
            tx["approved_at"] = now
            tx["updated_at"] = now
            if self._use_db():
                self._db_tx_upsert(tx)
            self._audit(
                transaction_id,
                approver_id,
                "approve",
                STATUS_PENDING,
                STATUS_APPROVED,
                {"checker_id": approver_id},
            )

            response = DisbursementApproveResponse(
                transaction_id=transaction_id,
                status=DisbursementStatus.APPROVED,
                checker_id=approver_id,
                approved_at=now,
            )
            self._idem_put(scope, self._fingerprint(approve_payload), response.model_dump())
            return response

    def execute_disbursement(
        self,
        transaction_id: str,
        idempotency_key: str | None = None,
    ) -> DisbursementExecuteResponse:
        """Execute the transfer via the VietQR/NAPAS sandbox.

        Transitions APPROVED -> COMPLETED on success or APPROVED -> FAILED on
        bank/gateway failure or timeout.
        """
        if is_production():
            raise RuntimeError("SANDBOX_DISABLED_IN_PRODUCTION")
        scope = self._idem_scope("execute", idempotency_key) if idempotency_key else ""
        execute_payload = {"transaction_id": transaction_id}

        with self._lock:
            if scope:
                cached = self._idem_get(scope)
                if cached is not None:
                    if cached["fingerprint"] != self._fingerprint(execute_payload):
                        raise ValueError("idempotency_key reused with different parameters")
                    return DisbursementExecuteResponse(**cached["response"])

            tx = self._get(transaction_id)
            if tx["status"] != STATUS_APPROVED:
                raise ValueError(
                    f"transaction {transaction_id} is not APPROVED (current: {tx['status']})"
                )

            result = self._simulate_transfer()
            now = self._now()
            before = tx["status"]
            if result.outcome == "completed":
                tx["status"] = STATUS_COMPLETED
                tx["bank_reference"] = result.bank_reference or ""
                tx["completed_at"] = now
                tx["error_message"] = ""
                self._external_statements[transaction_id] = {
                    "transaction_id": transaction_id,
                    "reference_code": tx["reference_code"],
                    "account_number": tx["account_number"],
                    "account_name": tx["account_name"],
                    "amount": tx["loan_amount"],
                    "posted_at": now,
                    "status": "POSTED",
                }
            else:
                tx["status"] = STATUS_FAILED
                tx["bank_reference"] = ""
                tx["error_message"] = result.error_message or "transfer failed"
            tx["updated_at"] = now
            if self._use_db():
                self._db_tx_upsert(tx)
            after = tx["status"]
            self._audit(
                transaction_id,
                "core-banking-sandbox",
                "execute",
                before,
                after,
                {
                    "outcome": result.outcome,
                    "delay_seconds": result.delay_seconds,
                    "error_message": result.error_message,
                },
            )

            response = DisbursementExecuteResponse(
                transaction_id=transaction_id,
                status=DisbursementStatus(after),
                bank_reference=tx["bank_reference"] or None,
                error_message=tx["error_message"] or None,
                completed_at=tx["completed_at"] or None,
                delay_seconds=result.delay_seconds,
            )
            if scope:
                self._idem_put(
                    scope, self._fingerprint(execute_payload), response.model_dump()
                )
            return response

    def rollback_disbursement(
        self,
        transaction_id: str,
        reason: str,
        approver_id: str,
        idempotency_key: str | None = None,
    ) -> DisbursementRollbackResponse:
        """Reverse a completed disbursement (dual control): COMPLETED -> REVERSED.

        The rollback approver must differ from the transaction's maker.
        """
        if not reason or not approver_id:
            raise ValueError("reason and approver_id are required")
        scope = self._idem_scope("rollback", idempotency_key) if idempotency_key else ""
        rollback_payload = {
            "transaction_id": transaction_id,
            "reason": reason,
            "approver_id": approver_id,
        }

        with self._lock:
            if scope:
                cached = self._idem_get(scope)
                if cached is not None:
                    if cached["fingerprint"] != self._fingerprint(rollback_payload):
                        raise ValueError("idempotency_key reused with different parameters")
                    return DisbursementRollbackResponse(**cached["response"])

            tx = self._get(transaction_id)
            if tx["status"] != STATUS_COMPLETED:
                raise ValueError(
                    f"transaction {transaction_id} is not COMPLETED (current: {tx['status']})"
                )
            if approver_id == tx["maker_id"]:
                raise ValueError("rollback approver must differ from the maker")

            now = self._now()
            tx["status"] = STATUS_REVERSED
            tx["reversed_at"] = now
            tx["reversal_reason"] = reason
            tx["updated_at"] = now
            if self._use_db():
                self._db_tx_upsert(tx)
            statement = self._external_statements.get(transaction_id)
            if statement is not None:
                statement["status"] = "REVERSED"
                statement["reversed_at"] = now
                statement["reversal_reason"] = reason
            self._audit(
                transaction_id,
                approver_id,
                "rollback",
                STATUS_COMPLETED,
                STATUS_REVERSED,
                {"reason": reason, "approver_id": approver_id},
            )

            response = DisbursementRollbackResponse(
                transaction_id=transaction_id,
                status=DisbursementStatus.REVERSED,
                approver_id=approver_id,
                reversed_at=now,
                reason=reason,
            )
            if scope:
                self._idem_put(
                    scope, self._fingerprint(rollback_payload), response.model_dump()
                )
            return response

    def reconcile(self, transaction_id: str) -> ReconciliationResult:
        """Compare internal ledger vs external bank statement.

        Returns MATCHED / MISMATCH / PENDING for the transaction.
        """
        with self._lock:
            tx = self._get(transaction_id)
            internal_amount = float(tx["loan_amount"])
            statement = self._external_statements.get(transaction_id)
            external_amount = float(statement["amount"]) if statement else None
            matched = False
            if (
                tx["status"] == STATUS_COMPLETED
                and statement is not None
                and statement.get("status") == "POSTED"
            ):
                amount_ok = abs(external_amount - internal_amount) <= 0.01
                account_ok = statement.get("account_number") == tx["account_number"]
                matched = amount_ok and account_ok
                status = (
                    ReconciliationStatus.MATCHED
                    if matched
                    else ReconciliationStatus.MISMATCH
                )
            else:
                status = ReconciliationStatus.PENDING
            details = {
                "internal_status": tx["status"],
                "internal_amount": internal_amount,
                "external_status": statement.get("status") if statement else None,
                "external_amount": external_amount,
                "account_number": tx["account_number"],
            }
            return ReconciliationResult(
                transaction_id=transaction_id,
                status=status,
                internal_amount=internal_amount,
                external_amount=external_amount,
                matched=matched,
                details=details,
            )

    def get_transaction_status(self, transaction_id: str) -> TransactionStatusResponse:
        """Return the current status and key fields of a transaction."""
        with self._lock:
            tx = self._get(transaction_id)
            return TransactionStatusResponse(
                transaction_id=tx["transaction_id"],
                status=DisbursementStatus(tx["status"]),
                maker_id=tx["maker_id"],
                checker_id=tx["checker_id"] or None,
                reference_code=tx["reference_code"] or None,
                bank_reference=tx["bank_reference"] or None,
                error_message=tx["error_message"] or None,
                created_at=tx["created_at"],
                updated_at=tx["updated_at"],
                approved_at=tx["approved_at"] or None,
                completed_at=tx["completed_at"] or None,
                reversed_at=tx["reversed_at"] or None,
            )

    def get_audit_trail(self, transaction_id: str) -> list[AuditEntry]:
        """Return all audit entries for a transaction, oldest first."""
        self._get(transaction_id)
        if self._use_db():
            rows = self._db_audit_trail(transaction_id)
            entries = []
            for e in rows:
                entries.append(
                    AuditEntry(
                        timestamp=e["timestamp"],
                        actor=e["actor_id"],
                        action=e["action"],
                        transaction_id=e["transaction_id"],
                        before_state=e["before_state"],
                        after_state=e["after_state"],
                        details={},
                    )
                )
            return entries
        with self._lock:
            entries = [e for e in self._audit_entries if e["transaction_id"] == transaction_id]
        return [AuditEntry(**e) for e in entries]

    def list_transactions(self) -> list[dict[str, Any]]:
        """Return a snapshot of all transactions (mainly for testing/debug)."""
        if self._use_db():
            return self._db_list_transactions()
        with self._lock:
            return [dict(tx) for tx in self._transactions.values()]


class ReconciliationResult(BaseModel):
    transaction_id: str
    status: ReconciliationStatus
    internal_amount: float
    external_amount: float | None = None
    matched: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


def init_core_banking_db(db_path: Path | str | None = None) -> None:
    """Create the core banking tables (idempotent) in SQLite."""
    from pipeline.storage.ledger import _connect, init_db

    path = Path(db_path) if db_path is not None else None
    if path is not None:
        init_db(path)

    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS core_banking_transactions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id    TEXT NOT NULL UNIQUE,
                application_id    TEXT NOT NULL,
                transaction_type  TEXT NOT NULL DEFAULT 'DISBURSEMENT',
                amount            REAL NOT NULL,
                currency          TEXT NOT NULL DEFAULT 'VND',
                account_number    TEXT NOT NULL DEFAULT '',
                account_name      TEXT NOT NULL DEFAULT '',
                error_message     TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL,
                maker_id          TEXT NOT NULL,
                checker_id        TEXT,
                approved_at       TEXT,
                executed_at       TEXT,
                reversed_at       TEXT,
                reversal_reason   TEXT,
                reference_code    TEXT,
                external_reference TEXT,
                idempotency_key   TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cbt_transaction_id ON core_banking_transactions(transaction_id);
            CREATE INDEX IF NOT EXISTS idx_cbt_application_id ON core_banking_transactions(application_id);
            CREATE INDEX IF NOT EXISTS idx_cbt_status ON core_banking_transactions(status);

            CREATE TABLE IF NOT EXISTS core_banking_audit (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT NOT NULL,
                action         TEXT NOT NULL,
                actor_id       TEXT NOT NULL,
                before_state   TEXT,
                after_state    TEXT,
                timestamp      TEXT NOT NULL,
                ip_address     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cba_transaction_id ON core_banking_audit(transaction_id);
            CREATE INDEX IF NOT EXISTS idx_cba_timestamp ON core_banking_audit(timestamp);
            """
        )
