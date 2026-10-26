-- Reference seed: the playoff selection rules of every season that may be simulated
-- (sim-engine/selection-rules). Only 2026 is seeded; a run for another season is refused.
-- Replaced on every deploy; a corrected reading of the rules is one edit here.
CREATE OR REPLACE TABLE {SIM_SCHEMA}.SEASON_RULES (
  SEASON               DECIMAL(4,0) NOT NULL COMMENT IS 'Season the rules apply to',
  AUTO_BID_RULE        VARCHAR(30) ASCII NOT NULL COMMENT IS 'Automatic bids: POWER_CHAMPIONS_PLUS_G6 (the champions of the four P4 conferences plus one Group of Six bid) or TOP_CHAMPIONS (the AUTO_BIDS best-ranked conference champions of any tier, the 2024-2025 rule)',
  AUTO_BIDS            DECIMAL(2,0) NOT NULL COMMENT IS 'Number of automatic bids under TOP_CHAMPIONS; unused under POWER_CHAMPIONS_PLUS_G6',
  G6_BID               VARCHAR(10) ASCII NOT NULL COMMENT IS 'Who takes the Group of Six bid: TEAM (the best-ranked team of a G6 conference, champion or not) or CHAMPION (the best-ranked G6 champion)',
  GUARANTEE_SCHOOL     VARCHAR(100) UTF8 COMMENT IS 'School selected whenever it is ranked within GUARANTEE_RANK (Notre Dame); NULL for no guarantee',
  GUARANTEE_RANK       DECIMAL(2,0) NOT NULL COMMENT IS 'Rank within which the guaranteed school gets in',
  MIN_CONFERENCE_TEAMS DECIMAL(2,0) NOT NULL COMMENT IS 'Fewest FBS teams a conference needs to crown a champion; a smaller one competes at-large',
  SELECTION_AT         TIMESTAMP NOT NULL COMMENT IS 'Selection day, UTC: games that start later are not simulated, and a point whose cutoff is not before it is refused',
  RULE_NOTE            VARCHAR(1000) UTF8 NOT NULL COMMENT IS 'The source of this reading of the rules and the date it was checked'
) COMMENT IS 'One row per season that may be simulated, holding its College Football Playoff selection rules. Replaced from git on every deploy.';

INSERT INTO {SIM_SCHEMA}."SEASON_RULES" VALUES
  (2026, 'POWER_CHAMPIONS_PLUS_G6', 5, 'TEAM', 'Notre Dame', 12, 6, '2026-12-06 12:00:00',
   'Checked 2026-10-26 against collegefootballplayoff.com/sports/2026/8/9/bracket-place-holder and /sports/2016/10/24/selection-committee-protocol: 12 teams; ACC, Big Ten, Big 12 and SEC champions plus the highest-ranked TEAM from the American, Conference USA, MAC, Mountain West, Pac-12 or Sun Belt (champion not required); Notre Dame automatic if in the final top 12; remaining places at-large; top four seeds receive byes. Selection Day is 2026-12-06, noon-3 p.m. ET. The 12:00 UTC cutoff is a conservative model boundary before Selection Day announcements, not an official CFP deadline.');
