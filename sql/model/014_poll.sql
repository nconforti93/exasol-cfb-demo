-- Reference seed: the polls CFBD publishes (model/ratings-rankings). A poll name that is not here keeps
-- its rows with the code OTHER and a lead of 0 (MODEL_CHECKS: POLL_UNKNOWN).
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.POLL (
  POLL_NAME VARCHAR(100) UTF8 NOT NULL COMMENT IS 'CFBD poll name as it appears in the rankings data',
  POLL_CODE VARCHAR(16) UTF8 NOT NULL COMMENT IS 'Short code: AP, COACHES, CFP, FCS_COACHES, D2_COACHES or D3_COACHES',
  DATA_FROM_SEASON DECIMAL(4,0) COMMENT IS 'First season with CFBD data for the poll; NULL when CFBD does not state it',
  LABEL_LEAD_WEEKS DECIMAL(1,0) NOT NULL COMMENT IS 'Weeks by which the poll label runs ahead of the games it reflects: an AP poll labeled week 10 is released after the games of week 9, so the lead is 1; the committee labels by the week it follows, lead 0. A lead that is too small only delays visibility; a lead that is too large would leak',
  FINAL_BEFORE_POSTSEASON BOOLEAN NOT NULL COMMENT IS 'TRUE when the poll publishes its final ranking before the postseason (the committee selection ranking), so the ranking is visible at the last regular week even if CFBD labels it postseason'
) COMMENT IS 'One row per CFBD poll name: its code, the first season with data, the label lead in weeks and whether its final ranking comes before the postseason.';

INSERT INTO {MODEL_SCHEMA}."POLL" VALUES
  ('AP Top 25', 'AP', 1936, 1, FALSE),
  ('Coaches Poll', 'COACHES', 2001, 1, FALSE),
  ('Playoff Committee Rankings', 'CFP', 2014, 0, TRUE),
  ('FCS Coaches Poll', 'FCS_COACHES', NULL, 1, FALSE),
  ('AFCA Division II Coaches Poll', 'D2_COACHES', NULL, 1, FALSE),
  ('AFCA Division III Coaches Poll', 'D3_COACHES', NULL, 1, FALSE);
