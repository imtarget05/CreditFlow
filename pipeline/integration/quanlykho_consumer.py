"""Consume QuanLyKho inventory events without bypassing maker-checker."""

from collections.abc import Awaitable, Callable
from typing import Any


class MakerCheckerRequired(RuntimeError):
    """Raised when production cannot create an authenticated approval request."""


async def handle_inventory_event(
    event: dict[str, Any],
    *,
    create_maker_checker_request: Callable[[dict[str, Any]], Awaitable[str]] | None,
    environment: str,
) -> dict[str, str]:
    event_type = str(event.get("event_type", ""))
    if event_type != "inventory.request.approved":
        return {"status": "ignored"}

    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise TypeError("inventory event payload must be an object")
    loan_application_id = str(payload.get("loan_application_id", "")).strip()
    if not loan_application_id:
        raise ValueError("inventory event requires loan_application_id")

    if environment.strip().lower() != "production":
        return {
            "status": "simulation_preview",
            "loan_application_id": loan_application_id,
        }
    if create_maker_checker_request is None:
        raise MakerCheckerRequired(
            "production inventory events require an authenticated maker-checker request"
        )

    request_id = await create_maker_checker_request(
        {
            "loan_application_id": loan_application_id,
            "collateral_id": payload.get("collateral_id"),
            "source_event_id": str(event.get("event_id", "")),
            "source_aggregate_id": str(event.get("aggregate_id", "")),
            "status": "PENDING_CHECKER",
        }
    )
    return {"status": "pending_maker_checker", "request_id": str(request_id)}
