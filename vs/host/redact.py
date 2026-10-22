"""Keep generated and configured passwords out of error text.

pyexasol puts the query text into its errors, and `{password!q}` substitutes the password into the
query on the client, so a failing `CREATE USER` would otherwise print the password. Code that
sends a secret to the database re-raises through `scrub` (or formats through `redact`).
"""

from __future__ import annotations

from collections.abc import Iterable

REDACTION = "[REDACTED]"
_QUOTES = ('"', "'")


def redact(text: str, secrets: Iterable[str | None]) -> str:
    """`text` with every secret, raw or with its quotes doubled for SQL, replaced."""
    forms: set[str] = set()
    for secret in secrets:
        if not secret:
            continue
        forms.add(secret)
        forms.update(secret.replace(quote, quote * 2) for quote in _QUOTES)
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, REDACTION)
    return text


def scrub(error: BaseException, secrets: Iterable[str | None]) -> BaseException:
    """`error` itself when no secret is in it or in its cause chain, else a clean stand-in.

    The stand-in is a `RuntimeError` naming the original type and the redacted first line (plus
    the rest of the message), with no `__cause__` and a suppressed `__context__`.
    """
    known = [secret for secret in secrets if secret]
    if not any(_mentions(chained, known) for chained in _chain(error)):
        return error
    message = redact(f"{type(error).__name__}: {error}", known)
    clean = RuntimeError(message)
    clean.__suppress_context__ = True
    return clean


def _chain(error: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _mentions(error: BaseException, secrets: list[str]) -> bool:
    texts = [str(error), repr(error), *(str(arg) for arg in error.args)]
    texts += [str(value) for value in vars(error).values() if isinstance(value, str)]
    return any(secret in text for secret in secrets for text in texts)
