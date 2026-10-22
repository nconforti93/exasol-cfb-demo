# exasol-cfb-demo

A college football analytics platform on Exasol, where the source is a REST API. A Virtual Schema generated from CFBD's OpenAPI spec exposes the whole CollegeFootballData API as SQL. All history (back to 1869) is persisted, and one `REFRESH_WEEK` call per week updates everything. The app shows playoff odds (Monte Carlo simulation in parallel in-database Python UDFs), weekly score predictions, the biggest upsets and head-to-head matchups, plus an AI chat over MCP.

Status: planning. See [`specs/mission.md`](specs/mission.md), [`specs/architecture.md`](specs/architecture.md) and [`docs/roadmap.md`](docs/roadmap.md).

Data provided by [CollegeFootballData.com](https://collegefootballdata.com). This project is not affiliated with the NCAA or the College Football Playoff.
