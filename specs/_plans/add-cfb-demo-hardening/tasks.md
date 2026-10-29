# Tasks: add-cfb-demo-hardening (light replan, 2026-10-27)

This list replaces `plan.md` § Implementation Tasks (decision [43]). The heavy task numbers are kept in brackets where a light task takes over a heavy one. Code lives in the worktree `../exasol-cfb-demo-p8` (branch `add-cfb-demo-hardening` from `main`) and is integrated with p4–p6 before Nov 1. No commits until the owner's final commit.

Markers: `[ ]` open, `[~]` in progress, `[x]` done, `[-]` cut or deferred.

## L1 Freeze and hard rules (due Sat Oct 31)

- [x] L1.1 `scripts/release_check.py --fast` with the `freeze` rule only: content digests of the frozen paths against `demo/freeze.yaml`, `--now`, `FAIL` inside the window with both digests and the way out, `WARN` after `until`. It reads no home file, no deployment file and no history. [1.6]
- [x] L1.2 `make demo-freeze FROM= UNTIL=` writes `demo/freeze.yaml`. `make release-check-fast` runs the check, and `make lint` runs it last. [1.6]
- [x] L1.3 CLI tests `tests/cli/test_release_check_cli.py` in a temporary git repository: the change, revert, after-`until`, no-file and untracked-file scenarios. [1.6] Done in p8: 4 tests; `make lint` (ruff, format, mypy strict over 198 files, freeze last) and `make test` (6930) pass.
- [x] L1.4 `CLAUDE.md` hard rules: the freeze and its paths; no `make deploy` on the real local namespace in demo week except through `make rehearse`; no `exakit` runtime command or plain `exakit mcp-doctor` (only `--json`). [1.6] Done in the p8, p6 and main-tree `CLAUDE.md`; p4/p5/p7 get it on integration.

## L2 Captures (calendar-bound; cannot be redone)

- [x] L2.0 **Before week 9 ends (now):** snapshot `aws-2026-w08` while AWS sits at "after week 8" (0 calls). It is the base of the early kit `2026-w08-w09`. Done 2026-10-27: SNAPSHOT run 19, 11 files, 156,004 rows, catalog `b6389f65c48c`, 0 calls, in `~/.cfb-demo/snapshots/aws-2026-w08`.
- [ ] L2.1 **Sun Nov 1** (before Tue Nov 3's first kickoff): check the week dates; quota; AWS `REFRESH_WEEK` week 9 (4); snapshot `aws-2026-w09`; record the week-9 slices (4) against the `aws-2026-w08` base, copied to `~/.cfb-demo/recordings/2026-w09/` with `SHA256SUMS`; `make demo-freeze FROM=2026-11-01 UNTIL=2026-11-21` on the integrated tree. Until L3 exists these are the existing make targets. [10.2]
- [ ] L2.2 **Sun Nov 8** (before Tue Nov 10): cadence week 10 (8); the console fan-out recorded once (3) to `~/.cfb-demo/recordings/console/`. [10.3]
- [ ] L2.3 **By Tue Nov 10, before kickoff:** week-11 predictions frozen on local after the `aws-2026-w10` restore. [10.4]
- [ ] L2.4 **Sun Nov 15** (before Tue Nov 17): cadence week 11 (8). [10.5]
- [ ] L2.5 Only if the demo moves after Nov 22: cadence week 12 and the freeze's `UNTIL`. [10.6]

## L3 `make weekly` (due Sun Nov 8)

- [ ] L3.1 `scripts/weekly.py` + `make weekly WEEK=n [CONFIRM=yes]`: a thin runner over the existing targets in a fixed order (quota → AWS refresh → snapshot → record → copy + checksums → local restore → predict), each step printed with its call estimate first, refused after the window unless `ALLOW_LATE=yes`, stopping at the first failure. CLI tests with fake step runners. [3.5, simplified]

## L4 Rehearsal (due Nov 8–10)

- [ ] L4.1 `make rehearse KIT=<snapshot>+<recordings>` on local only: restore the snapshot, copy the recordings to the per-kit BucketFS replay path, set `FETCH_MODE=REPLAY` and that `REPLAY_PATH`, run the analyses deploy, and print the point. Refused on `aws`. Integration test on `TEST_` with synthetic recordings. [3.1, 3.2, 3.3, merged]
- [ ] L4.2 `make demo-abort`: kill the newest running `REFRESH_WEEK`/`SIMULATE` statement, wait for the lock, print the state. [3.6, without the app deadline]
- [ ] L4.3 Kit `2026-w08-w09` rehearsed twice with equal pages, then the offline drill (Wi-Fi off). [11.1]

## L5 Preflight and tier

- [ ] L5.1 `make demo-preflight`: 0-call checks with `OK/WARN/FAIL` per beat: DB reachable on both targets, point and freshness, port-8000 app health and config, ledger and quota room, kit files and checksums, the freeze file, the local app startup. [5.1, reduced]
- [ ] L5.2 **Mon Nov 9:** go/no-go on the tier (Full, B, C or D), recorded in the decision log. [11.2]

## L6 Docs and Labs files

- [ ] L6.1 `docs/demo-script.md`: beats 1–6 per tier, with placeholders only. [8.1]
- [ ] L6.2 `README.md` and an MIT `LICENSE` (holder per U8), with the CFBD credit and the not-affiliated note. [1.2, 8.3]
- [ ] L6.3 `docs/benchmark.md` from the measured numbers already in `docs/engine-verification.md` and `docs/app-verification.md` (no harness). [1.5, replaced]
- [ ] L6.4 `docs/user-guide.md` demo section and the Claude Desktop MCP recipe; `CLAUDE.md` and `specs/mission.md` commands. [8.2, 8.6]

## L7 Rehearsals and demo

- [ ] L7.1 Nov 9–13 dress rehearsal #1 on `2026-w09-w10` (≤ 8 calls). [11.3]
- [ ] L7.2 Nov 15–16 stage kit `2026-w10-w11`, preflight, offline drill, dress rehearsal #2. [11.5, 11.6]
- [ ] L7.3 Demo week (Nov 16–20): no `make deploy` on the real local namespace. [11.7]
- [ ] L7.4 Wrap-up: ledger total (≤ 70), `docs/demo-hardening-verification.md`. [11.8]

## Cut or deferred (decision [43])

- [-] `CFB_BENCH`, `bench-sync`, the scale card and the app's dial button [2.1–2.3, 4.1–4.2]: the dial is `make simulate TARGET=aws SIMS=1000000` plus publication from the command line.
- [-] Benchmark harness [6.1]: L6.3 cites measured numbers instead.
- [-] Transcript replay [5.3]: no Anthropic key.
- [-] `make kit-facts`/`kit-previews` [3.4]: a documented query and a manual record instead.
- [-] Release check beyond the freeze [1.1]: secret, raw-data, known-secret and history rules are deferred until before the Labs publication; this is an owner gate, not a demo gate.
- [-] `make demo-mode` as its own command [3.2]: folded into `make rehearse`.
- [-] Chat beat on the kit [5.2]: only if Phase 7 merges by Nov 9.
