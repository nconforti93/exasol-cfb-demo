# Tasks: add-cfb-chat

Worktree: ../exasol-cfb-demo-p7 (branch add-cfb-chat, from add-cfb-app)

## Group A
- [x] 1.1 Toolchain:
- [x] 1.2 `chat/sqlguard.py` [expert]
- [x] 1.3 `chat/prices.py` and `chat/budget.py`: [expert]
- [x] 1.4 `chat/prompt.py` and `prompt_template.md`
- [x] 1.5 `chat/tools.py`
- [x] 1.6 `chat/model.py` and `chat/scripted.py`:
- [x] 1.7 `chat/rehearsal.py` and `rehearsal/questions.yaml`:
- [x] 1.8 `chat/turnlog.py`
- [x] 1.9 (see plan)

## Group B
- [~] 2.1 `chat/loop.py`, `chat/sessions.py` and `chat/appstate.py` (the pure part [expert]

## Group C
- [ ] 3.0 (see plan)
- [ ] 3.1 `chat/db.py`, the start check and `make chat-user`:
- [ ] 3.2 `chat/toolrun.py`, data tools
- [ ] 3.3 App-state tools

## Group C2
- [ ] 3.4 `routes/chat.py` and the wiring in `create_app`:
- [ ] 3.5 Disclosure into Phase 6's query log (P7-d), the turn log wiring, the daily spend at start,

## Group D
- [ ] 4.1 `chatApi.ts`, `useChatTurn.ts` (400 ms poll while running; StrictMode-safe; `AbortControll
- [ ] 4.2 `ChatAnswer.tsx` (plain text with line breaks; no HTML and no Markdown images or links), `
- [ ] 5.1 `tests/it/test_chat_rehearsal_it.py`
- [ ] 5.2 `scripts/chat_rehearse.py` and `make chat-rehearse`

## Group E
- [ ] 6.1 `docs/user-guide.md`, section "The chat":

## Group E2 (gated by U3)
- [ ] 6.2 `specs/architecture.md`:
- [ ] 6.3 `docs/roadmap.md`:
- [ ] 6.4 `CLAUDE.md`, dense bullets:

## Group F (sequential, gated)
- [ ] 7.1 Local, 0 Anthropic calls
- [ ] 7.2 Live, local
- [ ] 7.3 Live in the panel on local and on AWS (`make chat-user TARGET=aws`, then `make app-serve T

## Review and Verification
- [ ] R.1 Code review
- [ ] V.1 Checklist
- [ ] V.2 Scenario coverage audit
- [ ] V.3 Manual testing and verification-report.md
