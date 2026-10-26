-- Conference dimension (model/teams-conferences): one row per conference id of CFB_CORE.CONFERENCES, newest
-- fetch first (REQ_AT, then the later slice key, then the later row). Membership, divisions and member
-- counts depend on the season and are a GROUP BY over TEAM_SEASON, so the stored MEMBER_COUNT is not exposed.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.CONFERENCE (
  CONFERENCE_ID   COMMENT IS 'CFBD conference id',
  CONFERENCE_NAME COMMENT IS 'CFBD conference name; the name that games, rankings and memberships carry and that the tier seeds use',
  SHORT_NAME      COMMENT IS 'Short name as CFBD reports it',
  ABBREVIATION    COMMENT IS 'Abbreviation as CFBD reports it',
  CLASSIFICATION  COMMENT IS 'Classification of the conference as CFBD reports it (fbs, fcs, ii or iii); as of the last load of the conference list',
  FETCHED_AT      COMMENT IS 'When the conference row shown was fetched (REQ_AT, UTC)'
) AS
SELECT "ID",
       "NAME",
       "SHORT_NAME",
       "ABBREVIATION",
       "CLASSIFICATION",
       "REQ_AT"
  FROM {CORE_SCHEMA}."CONFERENCES"
QUALIFY ROW_NUMBER() OVER (
          PARTITION BY "ID"
          ORDER BY "REQ_AT" DESC, "REQ_SLICE" DESC, "ROW_NO" DESC) = 1
COMMENT IS 'One row per conference of CFB_CORE.CONFERENCES (newest fetch wins): its CFBD name, short name, abbreviation and classification. Members, divisions and counts depend on the season: group TEAM_SEASON.';
