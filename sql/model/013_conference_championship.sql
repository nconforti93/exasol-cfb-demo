-- Reference seed: the seasons in which a conference held a conference championship game (model/games).
-- CFBD does not mark these games. Conference names are CFBD's, FBS conferences only. LAST_SEASON NULL
-- means the conference still holds one. CONFERENCE_CHAMPIONSHIP_GAME looks for the game only inside
-- these ranges. The Pac-12 held none in 2024 and 2025 (two members); the rebuilt eight-team Pac-12
-- plays one again from 2026.
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.CONFERENCE_CHAMPIONSHIP (
  CONFERENCE_NAME VARCHAR(100) UTF8 NOT NULL COMMENT IS 'CFBD conference name',
  FIRST_SEASON    DECIMAL(4,0) NOT NULL COMMENT IS 'First season in which the conference held a championship game (1992 or later)',
  LAST_SEASON     DECIMAL(4,0) COMMENT IS 'Last season in which it held one; NULL while it still does'
) COMMENT IS 'One row per conference name and season range in which it held a conference championship game. Replaced from git on every deploy.';

INSERT INTO {MODEL_SCHEMA}."CONFERENCE_CHAMPIONSHIP" VALUES
  ('SEC', 1992, NULL),
  ('Western Athletic', 1996, 1998),
  ('Big 12', 1996, 2010),
  ('Big 12', 2017, NULL),
  ('Mid-American', 1997, NULL),
  ('ACC', 2005, NULL),
  ('Conference USA', 2005, NULL),
  ('Big Ten', 2011, NULL),
  ('Pac-12', 2011, 2023),
  ('Pac-12', 2026, NULL),
  ('Mountain West', 2013, NULL),
  ('American Athletic', 2015, NULL),
  ('Sun Belt', 2018, NULL);
