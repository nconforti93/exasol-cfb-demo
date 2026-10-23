# Feature: Playoff selection rules

Each simulated season ends the way the real one does: conferences crown champions, a resume ranking stands in for the selection committee, and a 12-team field is selected, seeded and played out under the season's rules. The 2026 rules drive the live odds. The rules are simplified on purpose, and the simplifications are written down, so the presenter can say exactly what the odds assume.

## Background

- `CFB_SIM.SEASON_RULES`: a seeded reference table with one row per season that may be simulated. Only 2026 is seeded; a run for any other season is refused (`sim-engine/sim-runs`). Columns and the 2026 values:
  - `AUTO_BID_RULE` `POWER_CHAMPIONS_PLUS_G6` (engine code 2): the champions of the four `P4` conferences, plus one Group of Six bid. The alternative `TOP_CHAMPIONS` (code 1) gives the bids to the `AUTO_BIDS` best-ranked conference champions of any tier, the 2024–2025 rule;
  - `AUTO_BIDS` 5;
  - `G6_BID` `TEAM` (code 2): the best-ranked team of a `G6` conference, champion or not. `CHAMPION` (code 1) restricts it to the best-ranked `G6` champion;
  - `GUARANTEE_SCHOOL` 'Notre Dame' and `GUARANTEE_RANK` 12: the school is selected when it is ranked within the top 12. The snapshot resolves it to the engine's `guarantee_team_idx` (−1 when the school is not on the roster);
  - `MIN_CONFERENCE_TEAMS` 6;
  - `SELECTION_AT` 2026-12-06 12:00 UTC, selection Sunday. Games that start later are not simulated (Army–Navy, 12 December 2026);
  - `RULE_NOTE`: the source of the reading and the date it was checked.
- **Tiers:** a team's tier comes from `CFB_MODEL.CONFERENCE_TIER` through `TEAM_SEASON_AS_OF`: `P4`, `G6` (every other FBS conference from 2024, the Pac-12 included), `IND` for FBS independents, and `NON_FBS` for every opponent that is not FBS. Non-FBS teams are never ranked or selected. An FBS team is eligible when it has at least one game in the season.
- **Conference champions:**
  - A conference with fewer than `MIN_CONFERENCE_TEAMS` FBS teams has no champion; its teams compete at-large, like independents.
  - A conference that holds a championship game in the season (`HAS_CCG`, from `CFB_MODEL.CONFERENCE_CHAMPIONSHIP`) plays it between the top two of its simulated standings, on a neutral site. Known pairings are not used.
  - Any other conference crowns the top team of its standings.
- **Standings** (simplified), best first: conference win percentage, exact; head-to-head, only for a tie of exactly two teams that met (when a pair met twice, the later game decides); conference point differential; rating; team index.
- **Resume ranking** (the committee stand-in): `w_win_pct` × W / (W + L) + `w_sos` × `SOS` + `w_pd` × `CAPPED_PD` / (W + L) + `w_rating` × `RATING`, with the prototype's weights 100, 2.0, 0.5 and 0 (`SIM_PARAMS`, origin `ASSUMED`). W and L count the games so far, the remaining games and the championship game; `CAPPED_PD` caps each margin at `margin_cap` (24). Ties go to the higher rating, then the lower team index.
- **Selection:** the automatic bids first, taken even when ranked outside the top 12; the guaranteed school counts as an automatic bid. The remaining places go at-large to the best-ranked teams not yet selected, up to 12.
- **Seeding:** straight by resume rank among the selected teams, seeds 1 to 12; seeds 1–4 get byes, champions or not (the rule since 2025).
- **Bracket:** fixed, no reseeding. First round 5–12, 6–11, 7–10 and 8–9 at the better seed's home; quarterfinals 1 against the 8–9 winner, 2 against 7–10, 3 against 6–11, 4 against 5–12; semifinals 1–8–9–4–5–12 against 2–7–10–3–6–11 bracket halves; final. From the quarterfinals on, games are neutral.
- The scenarios run `engine/core.py` on crafted models whose ratings make the resume order known in advance.

## Scenarios

### Scenario: The four power champions and the best Group of Six team get in, even outside the top 12

* *GIVEN* a crafted 2026 model whose resume ranks the other three `P4` champions 1st, 2nd and 5th, the ACC champion 15th, the best `G6` team 18th and the best `G6` champion 22nd
* *WHEN* the engine selects the field with `POWER_CHAMPIONS_PLUS_G6` and `G6_BID` `TEAM`
* *THEN* the field MUST hold the four `P4` champions, the 18th-ranked `G6` team and the seven best-ranked other teams
* *AND* the 11th- and 12th-ranked teams MUST be left out
* *AND* with `G6_BID` `CHAMPION` the 22nd-ranked `G6` champion MUST take the `G6` bid instead

### Scenario: The guaranteed school gets in within the top 12, and only there

* *GIVEN* a crafted model with an independent 'Notre Dame' ranked 12th, the `G6` bid ranked 20th, one `P4` champion ranked 16th and the other three in the top 4
* *WHEN* the engine selects the field
* *THEN* 'Notre Dame' MUST be selected
* *AND* the 11th-ranked team MUST be left out
* *AND* with 'Notre Dame' ranked 13th instead, it MUST be left out

### Scenario: Seeding is straight by rank, and the top four get byes

* *GIVEN* a crafted model in which the resume ranks a non-champion 2nd and a power champion 6th
* *WHEN* the engine seeds the field
* *THEN* the non-champion MUST be seed 2 with a bye, and the power champion seed 6 without one
* *AND* seed 5 MUST host seed 12 in the first round, and the winner MUST meet seed 4 in the quarterfinal

### Scenario: With the five-best-champions rule a Group of Six champion can take a power champion's place

* *GIVEN* a crafted model in which the ACC champion is ranked below two `G6` champions
* *WHEN* the engine selects the field with `TOP_CHAMPIONS` and `AUTO_BIDS` 5
* *THEN* both `G6` champions MUST get automatic bids
* *AND* the ACC champion MUST get in only at-large

### Scenario: Small conferences crown no champion, and conferences without a title game crown their leader

* *GIVEN* a crafted model with a 4-team conference, and a 7-team conference without `HAS_CCG`
* *WHEN* the engine simulates 1,000 seasons
* *THEN* the 4-team conference MUST have no `C` tally
* *AND* in every season the 7-team conference's champion MUST be the top team of its simulated standings
