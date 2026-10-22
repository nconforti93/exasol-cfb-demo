# Feature: Release check: no secret, no raw data, a license and the credit

The repository is meant to go public under exasol-labs. Before anyone decides to publish it, one command must prove four things about every tracked file and every commit in its history: no secret is in it, no raw CFBD data is in it, it carries an MIT license, and it credits CollegeFootballData.com. The check reports where a problem is without ever printing the secret it found. A fast subset runs with every `make lint`. Publishing itself, and any rewrite of history, stay with the owner.

## Background

- **Commands:** `make release-check` (`scripts/release_check.py`, full: tracked files and history, plus the known-secret scan) and `make lint`, which runs `scripts/release_check.py --fast` (tracked files only, pattern and data rules, no deployment files, no history).
- **Findings** are `FAIL` or `WARN`, each with the rule id, the path, the line (or, in history, the commit and path), and a masked excerpt: a secret is shown as its rule name and length, never as its value or a prefix of it. The exit code is 2 when any `FAIL` was found, else 0. A report is also written to `cache/release-check/<UTC time>.json` with the same masking.
- **Secret rules** (FAIL):
  - `private-key`: a PEM private-key header;
  - `aws-key`: an AWS access key id;
  - `anthropic-key`: an Anthropic key prefix followed by a key body, except the documented test marker `sk-ant-test-CFBCHATMARKER-0000`;
  - `bearer`: `Bearer` followed by a token of 20 or more characters, except the placeholder `Bearer <token>`;
  - `sql-password`: `IDENTIFIED BY` followed by a string literal, except the documented placeholders (`'<password>'`, `'<your key>'`, `'<key>'`) and generated literals in code (`{password!q}`, a bound parameter);
  - `secret-file`: a tracked `.env`, `secrets.json`, `deployment.json`, `connection-instructions.txt` or `*.pem`.
- **Known-secret rule** (`known-secret`, FAIL; full check only): the values that must never appear are read at run time from their only homes, kept in memory, and searched for in every tracked file and every blob of the history:
  - `dbPassword` of `~/.exasol/personal/deployments/local/secrets.json` and `.../default/secrets.json`;
  - the AWS deployment's host address and certificate fingerprint from `.../default/deployment.json`;
  - the values of `CFB_APP_PASSWORD`, `CFB_CONSOLE_PASSWORD`, `CFB_CHAT_PASSWORD` and `ANTHROPIC_API_KEY` in `.env`.

  A file it cannot read is reported as `WARN` (that value was not checked). The CFBD key is never read: it exists only in the database connection, and the check opens no database session at all.
- **Raw data rules** (FAIL): a tracked path under `cache/`; a tracked `*.cfbd.json`, `*.csv.gz`, `*.parquet` or `manifest.json` of a snapshot or kit; a tracked file over 1 MB outside the allowlist (`vs/catalog/catalog.json`, `vs/openapi/cfbd-openapi-*.json`, `uv.lock`, `app/frontend/package-lock.json`); a tracked JSON or CSV file with more than 50 records carrying CFBD game fields (`homeTeam` and `awayTeam`, or `HOME_TEAM` and `AWAY_TEAM`) outside `tests/synthetic/`. In the full check the same rules run over every path and blob ever committed on any branch.
- **Release rules** (FAIL): `LICENSE` exists and is the MIT license text; `README.md` contains "Data provided by CollegeFootballData.com" and states that the project is not affiliated with the NCAA or the College Football Playoff; the app's footer source contains the same credit.
- **Hygiene rules** (WARN): absolute home paths (`/home/<user>`, `C:\Users\<user>`), OneDrive folder names, agent scratchpad paths (`/tmp/claude-`), and e-mail addresses in tracked file contents (commit metadata is not checked).
- If `gitleaks` is installed, the full check also runs it on the history and lists its findings as `WARN`; its absence is noted and does not fail the check.
- The examples run the command against throwaway git repositories created by the test in a temporary directory, with fake deployment files and a fake `.env` under a temporary home.

## Scenarios

### Scenario: A secret in a tracked file fails the check without being printed

* *GIVEN* a temporary repository with a tracked `notes.md` holding a fake Anthropic key on line 7 and a tracked `setup.sql` with `IDENTIFIED BY 'hunter2-hunter2'`
* *WHEN* the check runs with `--fast`
* *THEN* it MUST report `FAIL anthropic-key notes.md:7` and `FAIL sql-password setup.sql:<line>` and exit with 2
* *AND* neither the output nor the report file MUST contain the key, the password, or any 8 consecutive characters of either

### Scenario: A known secret anywhere in history fails the full check

* *GIVEN* a temporary repository where one commit added the fake local `dbPassword` to `docs/old.md` and a later commit removed it, and a fake `secrets.json` under the temporary home
* *WHEN* `make release-check` runs
* *THEN* it MUST report `FAIL known-secret` with the commit id and the path `docs/old.md`, naming the secret as "local dbPassword"
* *AND* the output MUST NOT contain the password
* *AND* the `--fast` check on the same repository MUST pass that rule, because the current tree is clean

### Scenario: Raw API data in the tree or in history fails the check

* *GIVEN* a temporary repository with a tracked `cache/replay/games/x.cfbd.json` in an old commit, and a tracked `docs/games.json` holding 60 records with `homeTeam` and `awayTeam`
* *WHEN* `make release-check` runs
* *THEN* it MUST report the recording as `FAIL` with its commit and path, and `docs/games.json` as `FAIL` naming the game fields and the record count
* *AND* the same 60 records under `tests/synthetic/` MUST NOT be reported

### Scenario: A missing license or credit fails the check

* *GIVEN* a temporary repository without `LICENSE`, and with a `README.md` that does not credit CollegeFootballData.com
* *WHEN* the check runs with `--fast`
* *THEN* it MUST report `FAIL license` and `FAIL credit`, naming the expected text
* *AND* with an MIT `LICENSE`, the credit and the NCAA/CFP statement in `README.md` and the credit in the footer source, both rules MUST pass

### Scenario: Placeholders and documented test markers pass

* *GIVEN* a temporary repository whose tracked files hold `IDENTIFIED BY '<password>'`, `Bearer <token>`, the marker `sk-ant-test-CFBCHATMARKER-0000` and `CREATE USER … IDENTIFIED BY {password!q}` in Python source
* *WHEN* the check runs with `--fast`
* *THEN* no `FAIL` MUST be reported for them

### Scenario: Personal paths and e-mail addresses are warnings

* *GIVEN* a temporary repository with a tracked file naming `/home/someone/project`, a `/tmp/claude-1002/` path and an e-mail address
* *WHEN* the check runs with `--fast`
* *THEN* each MUST be reported as `WARN` with its path and line, and the exit code MUST be 0 when nothing else failed

### Scenario: The fast check runs with every lint and reads no secret

* *GIVEN* the project repository and a temporary home without any deployment or `.env` file
* *WHEN* `make lint` runs
* *THEN* it MUST run `scripts/release_check.py --fast` after the linters and fail when it fails
* *AND* the fast check MUST NOT open any file under the home's `.exasol` directory or `.env`, and MUST NOT connect to a database
