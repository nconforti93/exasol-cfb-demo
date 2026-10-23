# Feature: Release check: no secret, no raw data, a license and the credit

The repository is meant to go public under exasol-labs. Before anyone decides to publish it, one command must prove four things about every tracked file and every commit in its history: no secret is in it, no raw CFBD data is in it, it carries an MIT license, and it credits CollegeFootballData.com. The check reports where a problem is without ever printing the secret it found. A fast subset runs with every `make lint`. That subset also holds the demo freeze: while rehearsal kits depend on the stored-data definitions, a change to them fails the lint of every session. Publishing itself, and any rewrite of history, stay with the owner.

## Background

- **Commands:**
  - `make release-check` (`scripts/release_check.py`, full): tracked files and history, plus the known-secret scan.
  - `make lint` runs `scripts/release_check.py --fast`: tracked files only, the pattern, data, release, hygiene and freeze rules. It reads no deployment file, no home-directory file and no history.
  - `make demo-freeze FROM=<date> UNTIL=<date>` writes `demo/freeze.yaml`: the dates, and the current git tree id of each frozen path.
- **Findings** are `FAIL` or `WARN`. Each has the rule id, the path, the line (or, in history, the commit and path), and a masked excerpt: a secret is shown as its rule name and length, never as its value or a prefix of it. The exit code is 2 when any `FAIL` was found, else 0. A report is also written to `cache/release-check/<UTC time>.json` with the same masking.
- **Secret rules** (FAIL):
  - `private-key`: a PEM private-key header;
  - `aws-key`: an AWS access key id;
  - `anthropic-key`: an Anthropic key prefix followed by a key body, except the documented test marker `sk-ant-test-CFBCHATMARKER-0000`;
  - `bearer`: `Bearer` followed by a token of 20 or more characters, except the placeholder `Bearer <token>`;
  - `sql-password`: `IDENTIFIED BY` followed by a string literal, except the documented placeholders (`'<password>'`, `'<your key>'`, `'<key>'`) and generated literals in code (`{password!q}`, a bound parameter);
  - `secret-file`: a tracked `.env`, `secrets.json`, `deployment.json`, `connection-instructions.txt`, `.exapump/config.toml`, `claude_desktop_config.json` or `*.pem`.
- **Known-secret rule** (`known-secret`, FAIL; full check only). The values that must never appear are read at run time from their homes, kept in memory only, and searched for in every tracked file and every blob of the history. The homes:
  - every deployment under `~/.exasol/personal/deployments/` (today `local`, `default` and `test`):
    - each string value of 8 or more characters in its `secrets.json` (the `dbPassword` among them);
    - the host addresses and certificate fingerprints in its `deployment.json`;
    - each value that follows a password label (`password`, case-insensitive, then `:` or `=`) in its `connection-instructions.txt`;
    - the key body of its `node_access.pem` and of any other `*.pem`. The body is compared with whitespace removed on both sides, so a key pasted with different line breaks is still found; any 64-character run of the body counts;
  - `~/.exapump/config.toml`, parsed in memory (never printed or copied): every value whose key ends in `password`, `bfs_write_password` and `bfs_read_password` among them;
  - `~/.exasol-starter-kit/credentials/*`: each file's trimmed content, or for a JSON file each string value of 8 or more characters;
  - the Claude Desktop configuration (`CLAUDE_DESKTOP_CONFIG`, else the first `claude_desktop_config.json` under `/mnt/c/Users/*/AppData/Roaming/Claude/` on WSL): the `EXA_PASSWORD` of its `exasol-cfb` entry;
  - the values of `CFB_APP_PASSWORD`, `CFB_CONSOLE_PASSWORD`, `CFB_CHAT_PASSWORD` and `ANTHROPIC_API_KEY` in `.env`.

  A home that is missing or unreadable is reported as `WARN`, naming the home and that its values were not checked. The check opens no database session at all.
- **The CFBD key** exists only in the database connection, and the check never reads it from there. With `--cfbd-key-prompt` (owner only, on a terminal), the check asks for the key once, at a prompt that does not echo, keeps it in memory for this run and searches for it like a known secret. Without the flag, the report states "CFBD key: pattern-checked only (`bearer`)".
- **Raw data rules** (FAIL):
  - a tracked path under `cache/`;
  - a tracked `*.cfbd.json`, `*.csv.gz`, `*.parquet`, or `manifest.json` of a snapshot or kit;
  - a tracked file over 1 MB outside the allowlist (`vs/catalog/catalog.json`, `vs/openapi/cfbd-openapi-*.json`, `uv.lock`, `app/frontend/package-lock.json`);
  - a tracked JSON or CSV file with more than 50 records carrying CFBD game fields (`homeTeam` and `awayTeam`, or `HOME_TEAM` and `AWAY_TEAM`) outside `tests/synthetic/`.

  In the full check the same rules run over every path and blob ever committed on any branch.
- **Release rules** (FAIL):
  - `LICENSE` exists and is the MIT license text;
  - `README.md` contains "Data provided by CollegeFootballData.com" and states that the project is not affiliated with the NCAA or the College Football Playoff;
  - the app's footer source contains the same credit.
- **Freeze rule** (`freeze`, FAIL in both checks). While `demo/freeze.yaml` exists and today (or `--now`) lies between its `from` and `until` dates, each frozen path must have the git tree id pinned for it.
  - The frozen paths are the definitions that kit snapshots depend on: `vs/catalog/`, `sql/core/`, `sql/meta/load_log.sql`, `sql/meta/fetch_log.sql` and `loader/load-plan.yaml`.
  - A finding names the path, both tree ids, the kits at risk, and the way out: a migration that also re-fingerprints the existing snapshots, decided by the owner.
  - After `until` the rule passes, with a `WARN` to remove the file.
- **Hygiene rules** (WARN): absolute home paths (`/home/<user>`, `C:\Users\<user>`), OneDrive folder names, agent scratchpad paths (`/tmp/claude-`), and e-mail addresses in tracked file contents. Commit metadata is not checked.
- If `gitleaks` is installed, the full check also runs it on the history and lists its findings as `WARN`. Its absence is noted and does not fail the check.
- The examples run the command against throwaway git repositories created by the test in a temporary directory, with fake home files (deployments, exapump, starter kit, Claude Desktop) and a fake `.env` under a temporary home.

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

### Scenario: Every home of a known secret is read, and a pasted node key is found

* *GIVEN* a temporary home with fake `secrets.json` files for the deployments `local`, `default` and `test`, a fake `default/node_access.pem`, a fake `default/connection-instructions.txt`, a fake `~/.exapump/config.toml`, a fake starter-kit credentials file, and a fake Claude Desktop configuration with an `exasol-cfb` entry
* *AND* a temporary repository with a tracked `notes.md` holding the fake node key's body wrapped at 76 characters instead of 64, and a commit in history that added the fake exapump password to `docs/setup.md`
* *WHEN* `make release-check` runs
* *THEN* it MUST report `FAIL known-secret` for `notes.md`, naming the secret as the key of `default/node_access.pem`, and for the commit and `docs/setup.md`, naming the exapump profile password
* *AND* the output and the report file MUST NOT contain 8 consecutive characters of any fake secret
* *AND* with the starter-kit credentials directory removed, it MUST report that home as `WARN`, naming it as not checked

### Scenario: The CFBD key is checked only when the owner types it

* *GIVEN* the temporary repository of the previous scenario with a fake CFBD key in a tracked file without a `Bearer` prefix
* *WHEN* `make release-check` runs without `--cfbd-key-prompt`, and again with it and the fake key given at the prompt
* *THEN* the first run MUST NOT report the key, and MUST state "CFBD key: pattern-checked only"
* *AND* the second run MUST report `FAIL known-secret`, naming the CFBD key and the path, without printing the key
* *AND* neither run MUST open a database session or write the key to any file

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

### Scenario: A change to a frozen path fails the check during the freeze

* *GIVEN* a temporary repository with `vs/catalog/catalog.json`, `sql/core/x.sql` and `loader/load-plan.yaml`, and `demo/freeze.yaml` written by `make demo-freeze FROM=2026-11-01 UNTIL=2026-11-21`
* *WHEN* a later commit changes `sql/core/x.sql`, and the check runs with `--fast` and `--now 2026-11-10`, then with `--now 2026-11-25`
* *THEN* the first run MUST report `FAIL freeze sql/core`, naming both tree ids and the way out, and exit with 2
* *AND* the second run MUST pass the rule and report a `WARN` to remove `demo/freeze.yaml`
* *AND* with the change reverted, the first run MUST pass the rule

### Scenario: The fast check runs with every lint and reads no secret

* *GIVEN* the project repository and a temporary home without any deployment, exapump, starter-kit, Claude Desktop or `.env` file
* *WHEN* `make lint` runs
* *THEN* it MUST run `scripts/release_check.py --fast` after the linters and fail when it fails
* *AND* the fast check MUST NOT open any file under the home's `.exasol`, `.exapump` or `.exasol-starter-kit` directories, the Claude Desktop configuration or `.env`, and MUST NOT connect to a database
