"""Check the shared ledger for unrecorded loader runs before live app actions."""

from __future__ import annotations

from app.backend.operator import OperatorDisabled, OperatorPath
from app.backend.settings import Settings
from loader import guard
from vs.host import ledger


def startup(settings: Settings, operator: OperatorPath, book: ledger.Ledger) -> None:
    if not operator.enabled:
        raise guard.ReconcilePending("the operator path is disabled")
    try:
        with operator.connection() as connection:
            guard.reconcile(
                connection,
                settings.namespace,
                book,
                settings.ledger_plan,
                target=settings.target,
            )
    except OperatorDisabled as error:
        raise guard.ReconcilePending("the operator path is disabled") from error
