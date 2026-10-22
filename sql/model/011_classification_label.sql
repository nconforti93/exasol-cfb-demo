-- Reference seed: the division names that were used at the time (model/seasons-eras). CFBD assigns
-- today's classification codes (fbs, fcs, ii, iii) to every season; the model keeps the code and adds
-- the name of the time. IS_RETROACTIVE is TRUE when the code did not exist yet: fbs and fcs before
-- 1978, ii and iii before 1973.
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.CLASSIFICATION_LABEL (
  DIVISION_ERA   VARCHAR(32) UTF8 NOT NULL COMMENT IS 'DIVISION era code from ERA: PRE_NCAA, NCAA_UNDIVIDED, UNIVERSITY_COLLEGE, DIVISION_I_II_III, I_A_I_AA or FBS_FCS',
  CLASSIFICATION VARCHAR(8) UTF8 NOT NULL COMMENT IS 'CFBD classification code: fbs, fcs, ii or iii',
  LABEL          VARCHAR(40) UTF8 NOT NULL COMMENT IS 'Name of that classification in the division era, for example Division I-A',
  IS_RETROACTIVE BOOLEAN NOT NULL COMMENT IS 'TRUE when the classification code did not exist in the era (fbs and fcs before 1978, ii and iii before 1973)'
) COMMENT IS 'One row per division era and CFBD classification code: the division name used at the time and whether the code is retroactive.';

INSERT INTO {MODEL_SCHEMA}."CLASSIFICATION_LABEL" VALUES
  ('PRE_NCAA', 'fbs', 'Major college', TRUE),
  ('PRE_NCAA', 'fcs', 'Small college', TRUE),
  ('PRE_NCAA', 'ii', 'Small college', TRUE),
  ('PRE_NCAA', 'iii', 'Small college', TRUE),
  ('NCAA_UNDIVIDED', 'fbs', 'Major college', TRUE),
  ('NCAA_UNDIVIDED', 'fcs', 'Small college', TRUE),
  ('NCAA_UNDIVIDED', 'ii', 'Small college', TRUE),
  ('NCAA_UNDIVIDED', 'iii', 'Small college', TRUE),
  ('UNIVERSITY_COLLEGE', 'fbs', 'University Division', TRUE),
  ('UNIVERSITY_COLLEGE', 'fcs', 'College Division', TRUE),
  ('UNIVERSITY_COLLEGE', 'ii', 'College Division', TRUE),
  ('UNIVERSITY_COLLEGE', 'iii', 'College Division', TRUE),
  ('DIVISION_I_II_III', 'fbs', 'Division I', TRUE),
  ('DIVISION_I_II_III', 'fcs', 'Division I', TRUE),
  ('DIVISION_I_II_III', 'ii', 'Division II', FALSE),
  ('DIVISION_I_II_III', 'iii', 'Division III', FALSE),
  ('I_A_I_AA', 'fbs', 'Division I-A', FALSE),
  ('I_A_I_AA', 'fcs', 'Division I-AA', FALSE),
  ('I_A_I_AA', 'ii', 'Division II', FALSE),
  ('I_A_I_AA', 'iii', 'Division III', FALSE),
  ('FBS_FCS', 'fbs', 'FBS', FALSE),
  ('FBS_FCS', 'fcs', 'FCS', FALSE),
  ('FBS_FCS', 'ii', 'Division II', FALSE),
  ('FBS_FCS', 'iii', 'Division III', FALSE);
