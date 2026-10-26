-- Reference seed: the power tier of a conference by season (model/teams-conferences). Conference
-- names are CFBD's. A NULL name is the default for every other FBS conference. LAST_SEASON NULL
-- means open. Before 1998 there is no tier. MODEL_CHECKS reports a power conference of this table
-- that no team-season of a season uses (POWER_CONFERENCE_MISSING), so a renamed conference stops
-- the refresh.
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.CONFERENCE_TIER (
  CONFERENCE_NAME    VARCHAR(100) UTF8 COMMENT IS 'CFBD conference name; NULL means every other FBS conference, which takes the default tier of the era',
  FIRST_SEASON       DECIMAL(4,0) NOT NULL COMMENT IS 'First season of the range',
  LAST_SEASON        DECIMAL(4,0) COMMENT IS 'Last season of the range; NULL while open',
  TIER               VARCHAR(16) UTF8 NOT NULL COMMENT IS 'Tier code: AQ (1998-2013 automatic qualifier), NON_AQ (1998-2013 other), P5 (2014-2023), G5 (2014-2023 other), P4 (2024 on), G6 (2024 on other) or INDEPENDENT',
  IS_POWER_CONFERENCE BOOLEAN NOT NULL COMMENT IS 'TRUE for the tiers AQ, P5 and P4'
) COMMENT IS 'One row per conference name (NULL = every other FBS conference) and season range: its power tier in that range. Replaced from git on every deploy.';

INSERT INTO {MODEL_SCHEMA}."CONFERENCE_TIER" VALUES
  ('ACC', 1998, 2013, 'AQ', TRUE),
  ('ACC', 2014, 2023, 'P5', TRUE),
  ('ACC', 2024, NULL, 'P4', TRUE),
  ('Big Ten', 1998, 2013, 'AQ', TRUE),
  ('Big Ten', 2014, 2023, 'P5', TRUE),
  ('Big Ten', 2024, NULL, 'P4', TRUE),
  ('Big 12', 1998, 2013, 'AQ', TRUE),
  ('Big 12', 2014, 2023, 'P5', TRUE),
  ('Big 12', 2024, NULL, 'P4', TRUE),
  ('SEC', 1998, 2013, 'AQ', TRUE),
  ('SEC', 2014, 2023, 'P5', TRUE),
  ('SEC', 2024, NULL, 'P4', TRUE),
  ('Big East', 1998, 2012, 'AQ', TRUE),
  ('American Athletic', 2013, 2013, 'AQ', TRUE),
  ('Pac-10', 1998, 2010, 'AQ', TRUE),
  ('Pac-12', 2011, 2013, 'AQ', TRUE),
  ('Pac-12', 2014, 2023, 'P5', TRUE),
  ('FBS Independents', 1998, NULL, 'INDEPENDENT', FALSE),
  (NULL, 1998, 2013, 'NON_AQ', FALSE),
  (NULL, 2014, 2023, 'G5', FALSE),
  (NULL, 2024, NULL, 'G6', FALSE);
