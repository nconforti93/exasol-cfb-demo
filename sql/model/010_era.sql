-- Reference seed: the eras of college football (model/seasons-eras). Replaced on every deploy; edit
-- it in a pull request, never in the database. LAST_SEASON NULL means the era is still open.
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.ERA (
  ERA_KIND     VARCHAR(16) UTF8 NOT NULL COMMENT IS 'Kind of era: DIVISION (governing divisions), POSTSEASON (bowls, BCS, playoff format), CHAMPION (how a national champion is decided) or OVERTIME (whether a game can end in a tie)',
  ERA_CODE     VARCHAR(32) UTF8 NOT NULL COMMENT IS 'Code of the era, unique within its kind, for example BCS or I_A_I_AA',
  FIRST_SEASON DECIMAL(4,0) NOT NULL COMMENT IS 'First season of the era',
  LAST_SEASON  DECIMAL(4,0) COMMENT IS 'Last season of the era; NULL while the era is open. Eras of one kind are contiguous',
  CFP_TEAMS    DECIMAL(2,0) COMMENT IS 'POSTSEASON eras only, else NULL: number of teams in the College Football Playoff, 0 before 2014, 4 in 2014-2023, 12 from 2024. The seeding rules of the 12-team playoff belong to Phase 4''s selection configuration',
  TIES_POSSIBLE BOOLEAN COMMENT IS 'OVERTIME eras only, else NULL: TRUE when a game can end in a tie (before 1996 at the FBS level; lower divisions adopted overtime in 1981)',
  DESCRIPTION  VARCHAR(200) UTF8 NOT NULL COMMENT IS 'One sentence that says what the era was'
) COMMENT IS 'One row per era kind and season range, curated knowledge that CFBD does not provide: division structure, postseason format, champion selection and overtime rules. Replaced from git on every deploy.';

INSERT INTO {MODEL_SCHEMA}."ERA" VALUES
  ('DIVISION', 'PRE_NCAA', 1869, 1905, NULL, NULL, 'Before the NCAA existed; intercollegiate play without divisions'),
  ('DIVISION', 'NCAA_UNDIVIDED', 1906, 1955, NULL, NULL, 'NCAA founded; one undivided top level of major and small colleges'),
  ('DIVISION', 'UNIVERSITY_COLLEGE', 1956, 1972, NULL, NULL, 'University Division and College Division'),
  ('DIVISION', 'DIVISION_I_II_III', 1973, 1977, NULL, NULL, 'Divisions I, II and III'),
  ('DIVISION', 'I_A_I_AA', 1978, 2005, NULL, NULL, 'Division I split into I-A and I-AA'),
  ('DIVISION', 'FBS_FCS', 2006, NULL, NULL, NULL, 'Football Bowl Subdivision and Football Championship Subdivision'),
  ('POSTSEASON', 'NO_BOWLS', 1869, 1900, 0, NULL, 'No bowl games'),
  ('POSTSEASON', 'BOWLS', 1901, 1991, 0, NULL, 'Bowl games without a title game; champions picked by selectors and polls'),
  ('POSTSEASON', 'BOWL_COALITION', 1992, 1994, 0, NULL, 'Bowl Coalition'),
  ('POSTSEASON', 'BOWL_ALLIANCE', 1995, 1997, 0, NULL, 'Bowl Alliance'),
  ('POSTSEASON', 'BCS', 1998, 2013, 0, NULL, 'Bowl Championship Series with a national title game'),
  ('POSTSEASON', 'CFP_4', 2014, 2023, 4, NULL, 'College Football Playoff with four teams'),
  ('POSTSEASON', 'CFP_12', 2024, NULL, 12, NULL, 'College Football Playoff with twelve teams'),
  ('CHAMPION', 'SELECTORS', 1869, 1935, NULL, NULL, 'National champion named by selectors'),
  ('CHAMPION', 'POLLS', 1936, 1997, NULL, NULL, 'National champion decided by the AP and coaches polls'),
  ('CHAMPION', 'BCS_GAME', 1998, 2013, NULL, NULL, 'National champion decided in the BCS title game'),
  ('CHAMPION', 'CFP', 2014, NULL, NULL, NULL, 'National champion decided by the College Football Playoff'),
  ('OVERTIME', 'TIES', 1869, 1995, NULL, TRUE, 'Games can end in a tie'),
  ('OVERTIME', 'OVERTIME', 1996, NULL, NULL, FALSE, 'Overtime decides every FBS game');
