CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.WHATIF_TEAMS (
  RUN_ID COMMENT IS 'The run this snapshot row belongs to',
  TEAM_ID COMMENT IS 'CFBD team id',
  SCHOOL COMMENT IS 'School name, as in CFB_MODEL',
  CONFERENCE COMMENT IS 'Conference name; NULL for an independent or a non-FBS team',
  TIER COMMENT IS 'P4, G6, IND (FBS independent) or NON_FBS',
  IS_ELIGIBLE COMMENT IS 'TRUE for an FBS team with at least one game in the season; only these can be selected'
) AS
SELECT s."RUN_ID", s."TEAM_ID", s."SCHOOL", s."CONFERENCE", s."TIER",
       s."IS_ELIGIBLE"
  FROM {SIM_SCHEMA}."SIM_RUN_TEAM" s
  JOIN {SIM_SCHEMA}."SIM_RUN" r ON r."RUN_ID" = s."RUN_ID" AND r."RUN_STATUS" = 'DONE'
COMMENT IS 'One row per DONE run and roster team: the roster used by a what-if.';
