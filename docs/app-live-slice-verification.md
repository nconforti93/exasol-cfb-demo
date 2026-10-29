# Live map, team and league slice — 2026-10-26

The Phase 6 app serves its first pages from the AWS Exasol database at
`http://127.0.0.1:8000/`. The normal startup privilege check passed on AWS.
The server is bound to loopback and uses `CFB_APP_WEB`, which reads the fixed
`CFB_API` views. These routes do not reference `CFBD_VS` or make CFBD calls.

| Check | Observed |
|---|---|
| Built SPA at `/` | HTTP 200 |
| `/api/points`, `/api/teams` | HTTP 200, 8 visible points, 138 FBS teams |
| `/api/teams/map`, `/api/team/{id}` | HTTP 200, 138 mapped teams, 12 games for the sampled team |
| `/api/league/week`, `/api/league/stats` | HTTP 200, 56 games at the selected point, 5 road-warrior rows |
| Browser SQL button | Map query id found in the same browser's `/api/inspector`; its plan answered HTTP 200 with `no_virtual_table: true` and 0 live calls |

The selected current model point is still after week 7. Week 8 cannot advance
until its remaining non-FBS games are complete and the weekly refresh runs; the
league page is explicit about its selected point. The snapshot preview at port
5173 remains available, but port 8000 reads the database on each request.

Local Exasol route queries also returned real data in a temporary smoke, but
normal local app startup is blocked by inherited `PUBLIC` SELECT and EXECUTE
grants on the unrelated `PREPROC_RT` schema. The startup check correctly
refuses that extra access. No privilege or object outside the project schemas
was changed. The AWS app does not have this blocker.

Verification: 8 focused backend shell tests; 4 focused live-route tests; 1
inspector test; Ruff and strict mypy on changed Python files; HTTP smoke of
the running AWS app. The frontend build was already present. No CFBD request
was issued by this verification.
