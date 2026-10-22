-- One row per HTTP request a virtual-schema query planned, written by consumers via vs/host/fetchlog.py.
-- REQ_SOURCE carries the name of the virtual-schema metadata column (SOURCE itself is an Exasol reserved word).
-- Rendered by vs/host/metaddl.py: {META_SCHEMA} becomes CFB_META, or TEST_CFB_META in the test namespace.

CREATE TABLE {META_SCHEMA}.FETCH_LOG (
  LOGGED_AT TIMESTAMP NOT NULL,
  CONTEXT VARCHAR(200) UTF8,
  TABLE_NAME VARCHAR(128) UTF8 NOT NULL,
  SLICE_KEY VARCHAR(2000) UTF8 NOT NULL,
  REQ_SOURCE VARCHAR(8) UTF8 NOT NULL,
  HTTP_STATUS DECIMAL(3,0),
  ATTEMPTS DECIMAL(3,0),
  MS DECIMAL(9,0),
  ROWS_RETURNED DECIMAL(18,0) NOT NULL,
  REQUESTED_AT TIMESTAMP
);
