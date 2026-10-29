# Feature: The Sunday cadence: capture what cannot be captured later

Three inputs of the demo exist only for a few days each week, and no amount of later work can recreate them:
- a snapshot of the stored data as of "Sunday after week N", taken before week N+1's first game ends;
- the recordings of the season slices while week N is complete and week N+1 is not;
- the laptop's predictions for week N+1, frozen before its first kickoff.

One command captures all three every Sunday and keeps the recordings safe from being overwritten. It also keeps AWS at the current week for the scale dial. It spends eight calls a week, checks the data before it pays for a capture that a kit would refuse, and resumes after a failure without paying twice.

## Background

- **Command:** `make weekly WEEK=<n> [SEASON=2026] CONFIRM=yes [FROM=<step>] [ALLOW_LATE=yes] [FORCE=yes] [ARGS="--now <UTC time>"]` (`scripts/demo.py weekly`). Without `CONFIRM=yes` it prints the steps, their calls and the windows, and changes nothing. `--now` replaces the clock (tests).
- **Steps, in order.** Each step is printed with its seconds. The command stops at the first failure and prints the `FROM=<step>` line that resumes there; a resumed run never repeats a step that spent calls and succeeded.
  1. `quota`: `make quota TARGET=aws` (free `/info`). The month's remaining calls of the key's shared pool, and the ledger room of the plan `add-cfb-demo-hardening`, must each cover the 8 calls of this run plus the retry headroom. Otherwise the command stops here, at zero calls.
  2. `refresh`: `make refresh TARGET=aws SEASON=<s> WEEK=<n> PLAN=add-cfb-demo-hardening CONFIRM=yes` (4 calls).
  3. `checks` (zero calls, AWS stored `GAMES` after the refresh; the content rules of `demo/rehearsal-kit`):
     - **base:** week n is complete and no week-n+1 game involving an FBS team is completed. Only then can the snapshot be the base of the kit `<s>-w<n>-w<n+1>`;
     - **recording:** week n is complete and week n+1 is not. Only then can the recording serve the kit `<s>-w<n-1>-w<n>`.
  4. `snapshot`: `make snapshot TARGET=aws NAME=aws-<s>-w<nn>` (zero calls). When the base check failed, the name ends in `-late`, and the command states that the kit `<s>-w<nn>-w<nn+1>` is unavailable and that the previous kit stays the newest.
  5. `record`: `make record TARGET=aws PATTERNS=<the season's refresh slices> CONFIRM=yes ARGS="--plan add-cfb-demo-hardening --rerecord"` (4 calls). It is skipped at zero calls, stating why, when the recording check failed or when no base snapshot of week n-1 exists (no kit for it to serve).
  6. `copy`: the files that the `record` step wrote are copied from `cache/replay/` into `~/.cfb-demo/recordings/<s>-w<nn>/`. The directory gets mode 0700, the files 0600, and a `SHA256SUMS` list is written. An existing directory for the week is never overwritten without `FORCE=yes`. The copy lies outside the repository and outside OneDrive, by the snapshot location rule.
  7. `restore`: `make restore TARGET=local NAME=aws-<s>-w<nn> CONFIRM=yes`, then `make analyses` (zero calls).
  8. `predict`: `make predict SEASON=<s> WEEK=<n>` on local, the first freeze of week n+1's predictions. It is refused after week n+1's first kickoff, taken from the restored schedule, unless `ALLOW_LATE=yes`, because those predictions would be graded as reconstructions.
  9. `publish`: `make simulate` and `make publish` at week n on local, unless a week-n publication exists (zero calls).
  10. `sync`: `make bench-sync` (zero calls).

  A step whose command is not deployed yet (the analyses for `predict` and `publish`, `CFB_BENCH` for `sync`) is skipped with that reason. The skip is reported in the summary, and the run still exits 0.
- **Calls:** 8 per run (4 for the refresh, 4 for the recording), all under the ledger plan `add-cfb-demo-hardening`. AWS has no replay directory, so the refresh's own responses cannot be kept, and the recording repeats the 4 requests.
- **Windows**, printed by the dry run:
  - the snapshot can be a kit base until week n+1's first game involving an FBS team ends (in practice, the first kickoff on Tuesday);
  - the recording can serve its kit until week n+1 completes;
  - the predictions are honest until week n+1's first kickoff.
- **Summary:** the kit each capture serves (base of `<s>-w<nn>-w<nn+1>`, recordings for `<s>-w<nn-1>-w<nn>`), the calls spent, the ledger room left, and every skipped step with its reason.
- The examples use fake targets and fake step runners. The fake AWS returns stored `GAMES` rows as the scenario states. The fake runners record the arguments, calls and ledger plan of each step. Each example starts with a snapshot `aws-2026-w09` present, unless it says otherwise.

## Scenarios

### Scenario: A Sunday cadence captures the week in order at eight calls

* *GIVEN* a fake AWS whose stored `GAMES` after the refresh have 2026 week 10 complete and week 11 unplayed, the snapshot `aws-2026-w09`, and the analyses and `CFB_BENCH` deployed on local
* *WHEN* the owner runs `make weekly WEEK=10 CONFIRM=yes` with `--now` on Sunday 2026-11-08
* *THEN* the steps MUST run in the order quota, refresh, checks, snapshot, record, copy, restore, predict, publish, sync
* *AND* the run MUST charge 8 calls in total, all under the ledger plan `add-cfb-demo-hardening`
* *AND* `~/.cfb-demo/recordings/2026-w10/` MUST hold the four recorded files with mode 0600 in a directory with mode 0700, and its `SHA256SUMS` MUST match the files in `cache/replay/`
* *AND* the summary MUST name `aws-2026-w10` as the base of `2026-w10-w11` and the recordings as serving `2026-w09-w10`

### Scenario: A failed step stops the cadence and a resume does not pay twice

* *GIVEN* the inputs of the first scenario, with the `restore` step failing
* *WHEN* the owner runs `make weekly WEEK=10 CONFIRM=yes`, and then `make weekly WEEK=10 CONFIRM=yes FROM=restore` after the cause is fixed
* *THEN* the first run MUST stop after the `restore` step with a non-zero exit code, naming the step and printing the `FROM=restore` line
* *AND* the second run MUST NOT run the `refresh`, `snapshot`, `record` or `copy` steps again, and MUST charge no call
* *AND* the second run MUST end with the `sync` step and exit code 0

### Scenario: A cadence run after the next week began keeps the recording and gives up the kit base

* *GIVEN* a fake AWS whose stored `GAMES` after the refresh have 2026 week 10 complete and two week-11 FBS games completed
* *WHEN* the owner runs `make weekly WEEK=10 CONFIRM=yes` with `--now` on Wednesday 2026-11-11
* *THEN* the snapshot MUST be named `aws-2026-w10-late`, and the output MUST state that the kit `2026-w10-w11` is unavailable and that `2026-w09-w10` stays the newest kit
* *AND* the `record` and `copy` steps MUST still run, because week 11 is not complete, and the summary MUST name `2026-w09-w10` as the kit they serve
* *AND* the `predict` step MUST be refused, stating that week 11 has kicked off and that its predictions would be reconstructions, and the run MUST stop there with the `ALLOW_LATE=yes` line printed

### Scenario: A recording that its kit would refuse is not paid for

* *GIVEN* a fake AWS whose stored `GAMES` after the refresh have 2026 weeks 10 and 11 complete
* *WHEN* the owner runs `make weekly WEEK=10 CONFIRM=yes`, and again without the snapshot `aws-2026-w09` and with week 11 unplayed
* *THEN* in the first run the `record` and `copy` steps MUST be skipped at zero calls, stating that week 11 is complete, and the run MUST charge 4 calls
* *AND* in the second run the `record` and `copy` steps MUST be skipped at zero calls, stating that no base snapshot of week 9 exists
* *AND* both summaries MUST list the skipped steps with their reasons

### Scenario: The cadence refuses before spending when the quota or the ledger cannot cover it

* *GIVEN* the ledger plan `add-cfb-demo-hardening` with 6 calls of room
* *WHEN* the owner runs `make weekly WEEK=10 CONFIRM=yes`
* *THEN* the run MUST stop at the `quota` step with a non-zero exit code, stating the 8 planned calls plus the retry headroom against the 6 calls of room
* *AND* no step after `quota` MUST run, and no call MUST be charged
