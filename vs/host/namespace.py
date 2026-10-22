"""Object names of the real deployment and of prefixed (test) namespaces.

The real deployment is created and owned by `SYS` ([20]); it has a reader role `CFB_READER`. A
prefixed namespace keeps a dedicated owner user (`CFB_TEST_OWNER` for `TEST_`), so the script-bound
connection grant and the key-protection tests still apply to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REAL_CONNECTION = "CFBD_API"
MOCK_CONNECTION = "CFBD_API_MOCK"
FETCH_SCRIPT_NAME = "CFBD_FETCH"
SYS_OWNER = "SYS"

_PREFIX_PATTERN = re.compile(r"(?:[A-Z][A-Z0-9_]*_)?")


class NamespaceError(ValueError):
    """A namespace prefix cannot form valid object names."""


@dataclass(frozen=True)
class Namespace:
    """Names of one deployment: `""` is the real one, `"TEST_"` the integration-test one."""

    prefix: str = ""

    def __post_init__(self) -> None:
        if not _PREFIX_PATTERN.fullmatch(self.prefix):
            raise NamespaceError(
                f"prefix {self.prefix!r} must be empty or UPPER_CASE words ending in '_', "
                "for example 'TEST_'"
            )

    @property
    def infra_schema(self) -> str:
        return f"{self.prefix}CFB_INFRA"

    @property
    def meta_schema(self) -> str:
        return f"{self.prefix}CFB_META"

    @property
    def core_schema(self) -> str:
        return f"{self.prefix}CFB_CORE"

    @property
    def stage_schema(self) -> str:
        """The loader's staging schema: one normally empty table per planned mirror."""
        return f"{self.prefix}CFB_STAGE"

    @property
    def restore_schema(self) -> str:
        """The landing schema of a restore; it exists only while a restore runs."""
        return f"{self.prefix}CFB_RESTORE"

    @property
    def vs_name(self) -> str:
        return f"{self.prefix}CFBD_VS"

    @property
    def is_real(self) -> bool:
        return not self.prefix

    @property
    def owner_user(self) -> str:
        """`SYS` for the real deployment, the dedicated `CFB_<prefix>OWNER` user otherwise."""
        return SYS_OWNER if self.is_real else f"CFB_{self.prefix}OWNER"

    @property
    def reader_role(self) -> str:
        """The role that may `SELECT` on the virtual schema (created by `make grants`)."""
        return f"CFB_{self.prefix}READER"

    @property
    def fetch_script(self) -> str:
        return f"{self.infra_schema}.{FETCH_SCRIPT_NAME}"

    @property
    def default_connection(self) -> str:
        return MOCK_CONNECTION if self.prefix else REAL_CONNECTION

    def resolve_connection(self, requested: str | None) -> str:
        """`requested`, else the default connection; see `check_connection`."""
        return self.check_connection(requested or self.default_connection)

    def check_connection(self, connection: str) -> str:
        """`connection` itself; a prefixed namespace never gets the real one, also not as an
        empty name, which the adapter treats as the real default."""
        if self.prefix and connection.strip().upper() in ("", REAL_CONNECTION):
            raise NamespaceError(
                f"namespace {self.prefix!r} must not use the real connection {REAL_CONNECTION}"
            )
        return connection
