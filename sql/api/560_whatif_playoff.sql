CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.WHATIF_PLAYOFF (
  RUN_ID COMMENT IS 'The run that simulated the season',
  SIM_ID COMMENT IS 'Simulated season within the run',
  TEAM_ID COMMENT IS 'CFBD team id of a team in the playoff field',
  SEED COMMENT IS 'Playoff seed 1 to 12',
  BYE COMMENT IS 'TRUE for seeds 1 to 4, which skip the first round',
  REACHED_ROUND COMMENT IS 'Furthest round reached: 1 first round, 2 quarterfinal, 3 semifinal, 4 final',
  CHAMPION COMMENT IS 'TRUE when the team won the national championship'
) AS
SELECT s."RUN_ID", s."SIM_ID", s."TEAM_ID", s."SEED", s."BYE",
       s."REACHED_ROUND", s."CHAMPION"
  FROM {SIM_SCHEMA}."SIM_TEAM" s
  JOIN {SIM_SCHEMA}."SIM_RUN" r ON r."RUN_ID" = s."RUN_ID" AND r."RUN_STATUS" = 'DONE'
COMMENT IS 'One row per DONE run, simulated season and playoff team: the field and its round result.';
