-- Reference table with seed rows: one statement for the table, one for the rows.
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.ERA (
  ERA_KIND VARCHAR(32) UTF8 NOT NULL COMMENT IS 'DIVISION or POSTSEASON; a ; and -- inside a comment',
  ERA_CODE VARCHAR(32) UTF8 NOT NULL COMMENT IS 'Era code, e.g. ''BCS''',
  FIRST_SEASON DECIMAL(4,0) NOT NULL COMMENT IS 'First season of the era',
  CONSTRAINT ERA_PK PRIMARY KEY (ERA_KIND, ERA_CODE)
) COMMENT IS 'One row per era kind and season range; seeded from git.';

INSERT INTO {MODEL_SCHEMA}."ERA" VALUES
  ('DIVISION', 'PRE_NCAA', 1869),  -- a line comment; with a semicolon
  ('POSTSEASON', 'BCS', 1998);
