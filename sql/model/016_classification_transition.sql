-- Reference seed, empty unless open question Q6 shows that CFBD's game codes are today's codes. If so,
-- it lists every team and season range since 1978 whose classification differs from today's code,
-- reviewed like the tier table. CLASSIFICATION_TRANSITION is read first, before the codes on games and
-- the memberships (model/teams-conferences), so Q6 only changes these rows, never a view.
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.CLASSIFICATION_TRANSITION (
  TEAM_NAME      VARCHAR(100) UTF8 NOT NULL COMMENT IS 'CFBD school name of the team (TEAM.SCHOOL)',
  FIRST_SEASON   DECIMAL(4,0) NOT NULL COMMENT IS 'First season of the range (1978 or later)',
  LAST_SEASON    DECIMAL(4,0) NOT NULL COMMENT IS 'Last season of the range; ranges of one team do not overlap',
  CLASSIFICATION VARCHAR(8) UTF8 NOT NULL COMMENT IS 'Classification the team had in the range: fbs, fcs, ii or iii',
  NOTE           VARCHAR(200) UTF8 COMMENT IS 'Why the row exists, for example the transition seasons of a move up to FBS'
) COMMENT IS 'One row per team (CFBD school name) and season range whose classification differs from the code CFBD records on its games. Empty unless Q6 says otherwise.';
