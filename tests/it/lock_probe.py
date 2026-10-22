"""A test-only Lua script that takes and releases the run lock from another bundle (hand-off H1).

It is bundled the way another plan's script joins the lock: the namespace constants of
`loader.bundle.render_constants`, then `loader/lua/loadlib.lua`, then its own entry code, which
calls the library's `acquire_lock` and `release_lock`.

`EXECUTE SCRIPT <infra>.TEST_LOCK_PROBE(lock_action, lock_word, lock_value)`:

- `('acquire', kind, args)` returns one row `RUN_ID`: the run id of the new `RUNNING` row;
- `('release', status, run_id)` returns one row `HELD`: 1 when the row was still `RUNNING`.
"""

from __future__ import annotations

from pathlib import Path

from loader import bundle
from vs.host.namespace import Namespace

PROBE_SCRIPT = "TEST_LOCK_PROBE"
RELEASE_DETAIL = "released by the lock probe"

_ENTRY = f"""
local action, word, value = nz(lock_action), nz(lock_word), nz(lock_value)
if action == "acquire" then
  exit({{{{acquire_lock(word, value)}}}}, "RUN_ID DECIMAL(18,0)")
end
if action == "release" then
  local held = release_lock(whole_number(tonumber(value), "run id"), word, "{RELEASE_DETAIL}")
  exit({{{{held and 1 or 0}}}}, "HELD DECIMAL(1,0)")
end
error("lock probe: unknown action " .. tostring(action), 0)
"""


def lock_probe_sql(namespace: Namespace, lua_dir: Path = bundle.LUA_DIR) -> str:
    """The `CREATE OR REPLACE LUA SCRIPT` statement of the probe for `namespace`."""
    library = (lua_dir / bundle.LIBRARY_FILE).read_text(encoding="utf-8")
    return (
        f"CREATE OR REPLACE LUA SCRIPT {namespace.infra_schema}.{PROBE_SCRIPT}"
        "(lock_action, lock_word, lock_value) RETURNS TABLE AS\n"
        f"{bundle.render_constants(namespace)}\n{library}\n{_ENTRY}\n/"
    )
