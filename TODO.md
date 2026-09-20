# Workflow optimization TODO

Derived from VovKa's 30-day Claude Code usage. Caveat: this is one user's data, not the whole team's.

## 1. Codify manual browser verification (highest-value)
- Evidence: 443 puppeteer calls; `frontend/` has only vitest unit tests — no Playwright, no `e2e/` dir, no checked-in browser scripts. All UI verification was hand-driven in chat and discarded.
- Action: add Playwright under `frontend/e2e/`. First spec = AWS guided onboarding wizard (happy path + validation-failure path). Wire into `numio-dev.sh` against the ephemeral-port worktree stack.

## 2. Fix the AWS guided onboarding churn hotspot
- Evidence: session title "Fix Validation steps in AWS Guided onboarding flow" appears 3x in the window; ~12 done tasks touch onboarding/CUR/CFN. Backend has `test_onboarding_cfn.py` and `test_cur_runner_e2e.py`, but no UI-flow test covers the wizard — where the bugs landed (cancel-dialog hang, empty waiting-step, CFN param validation, CUR-not-created).
- Action: the Playwright spec from #1 is the fix. Make "add/extend the onboarding e2e spec" an acceptance criterion on every future onboarding task.

## 3. Treat `/compact` as a decomposition signal
- Evidence: `/clear` 20x (~1 reset per 2-3 sessions — healthy, leave alone). `/compact` 7x means 7 sessions grew too large to want to lose. `/context` 12x.
- Action: when a task needs compaction, decompose it. Lean on `/tm:plan` to split features into smaller tasks that fit one `/clear`-bounded session. Target: zero `/compact` next month.

## 4. Template the front-loaded clarification dialogs
- Evidence: several sessions open with structured Q&A (`/tm:define` discipline) — working well, keeps scope tight.
- Action: no change to the practice. Capture the recurring question set (identity, attribution, scope boundaries) as a checklist in the `/tm:define` flow so it's answered once per feature.

## 5. Make doc-sync mechanical, not just a rule
- Evidence: CLAUDE.md and memory treat doc drift as a defect; `/tm:docs-sync` exists but is a manual gate.
- Action: add a Stop-hook or pre-close check in `taskman.sh` that refuses to close a feature if its linked spec wasn't touched in the same branch.

---

**If only one:** do #1. It collapses #1 and #2, attacks the 30% debug ratio and the 443-call manual-verification cost in one change.
