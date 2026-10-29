CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.WHATIF_OUTCOMES (
  RUN_ID COMMENT IS 'The run that simulated the season',
  SIM_ID COMMENT IS 'Simulated season within the run',
  OUTCOMES COMMENT IS 'One character per remaining game at PATH_POS of SIM_RUN_GAME: 1 home win, 0 away win; NULL when no game remains'
) AS
SELECT s."RUN_ID", s."SIM_ID", s."OUTCOMES"
  FROM {SIM_SCHEMA}."SIM_PATH" s
  JOIN {SIM_SCHEMA}."SIM_RUN" r ON r."RUN_ID" = s."RUN_ID" AND r."RUN_STATUS" = 'DONE'
COMMENT IS 'One row per DONE run and simulated season: its string of remaining-game outcomes.';
