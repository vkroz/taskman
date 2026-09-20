# Taskman - Vision

## Taskman - Vision
 
Skills, plugins, tools and agents for AI-assisted SDLC. 

Topics covered
- discovery and requirements
- specations
- analysis and design
- planning 
- implementation
- QA
- DevOps

For discovery and planning phase: `defmod` 
For projects and tasks management: `taskman`
For agentic SDLC: `taskman`
For QA: ...TK...
For autonomuos execution: `musterkit` - coordinationation and orchestration agent. Shared memory and context management





========= STOP READING HERE ============

## Objective

Accelerate the software development lifecycle (SDLC) through adoption of AI coding tools and automation. Transition software teams from ad-hoc AI use to repeatable, validated, and measurable AI-assisted development.

This guideline defines the path for software engineering teams to transform and aceelerate software developement with AI coding tool. 

The five sections below list the actions required to reach that state. Execute them in order; each section builds on the ones before it.



## Objective

This guideline charts how a software team moves from ad-hoc AI use to AI-driven development. At the target, AI drafts code, tests, and routine fixes from specifications. The pipeline gates and packages every change. Developers write the specs, review the output, and approve each release. The path has five stages: foundations, daily practice, workflow integration, quality automation, and autonomous agents.

Execute the stages in order; each builds on the ones before it.


## 1. Set foundations: tool requirements, team standards, dev environment

- Specify AI tool requirements for procurement: integrated development environment (IDE) and terminal integration, support for the team's language and framework stack, retrieval over internal code and documentation, audit log access for the team.
- Set team standards for AI use: which tasks expect AI assistance, which require human authorship, and how AI-assisted work is marked in commits and pull requests (PRs). Examples — test scaffolding, documentation drafts, CI/CD explanation, and simple local refactors expect AI assistance; security-critical paths, IAM changes, deployment configuration, broad refactoring, and architectural decisions require explicit human ownership.
- DS&A will standardize practices, controls, and quality gates. It will not force one AI coding tool where team workflows require different tools.
- Commit AI assistant configuration to the repository so every developer's environment loads identically: extension manifests, model and context defaults, project-level rules files.

## 2. Embed AI in daily developer practice

- Start AI use on low-stakes tasks: boilerplate, test scaffolds, documentation drafts. Treat AI output as a first draft requiring refinement before commit.
- Build a team-curated library of reusable prompts for refactoring, test generation, debugging, and documentation.
- Maintain a team reference of AI output failure modes encountered in practice: hallucinated function and library names, outdated patterns, license-tainted snippets, incorrect environment assumptions, and unsafe permission suggestions. Reference it during code review.
- Add five checks for AI-generated content to the code review template: local execution, test coverage, secret and credential exposure, licensing, and preservation of internal context.
- Share AI usage experience regularly across the team: working prompts, failed approaches, model behaviors. Add what gets shared to the prompt library and failure-modes reference, so individual lessons become team standard.

**Developer skills and mindset shift**

- **Skills to develop** — name and learn four practices. *Spec writing*: translate intent into testable acceptance criteria. *Prompt design*: compose context and constraints, not commands. *AI output review*: evaluate generated code for hallucinated APIs, hidden assumptions, license risk. *Agent orchestration*: sequence tasks, scope context, judge when to stop.
- **Mindset shift** — treat AI output as a first draft, not a finished product. Verify before commit. Retain ownership over judgments AI is not trusted to make: architecture, security-critical paths, scope of change.
- **Day-to-day shift during adoption** — writing code becomes one mode of work alongside writing specs and reviewing AI output. Reviewing AI-generated code becomes a primary activity, not a sideline. Developers carry context for the AI through repository rules, prompt libraries, and connected docs — they do not paste it each turn.

**Implementation**

- Load the prompt library and failure-modes reference into the AI assistant's working context automatically — through whatever configuration mechanism the tool exposes (rules files, system prompts, retrieval connectors). Developers should not have to fetch or paste them.
- Put the code review checklist in a merge request template or bot check, so reviewers do not have to remember it.

## 3. Wire AI into engineering workflows

- Write a specification for every change that touches application code, public interfaces, data contracts, pipeline behavior, or deployment behavior: problem, acceptance criteria, constraints, out-of-scope items.
- Direct AI to generate implementation from the spec, not from a ticket title or chat history.
- Use AI connected to the codebase, internal docs, design decisions, and runbooks — so AI agents' actions are grounded in your system, not generic patterns.
- Do not connect AI to production systems or deployment infrastructure. AI operates inside the development environment; humans drive every change that crosses that boundary.
- Trigger AI support from CI events, not developer memory. MR opens → CI drafts the summary. Code merges → CI drafts release notes.

**Implementation**

- Connect the AI tool to your codebase, internal docs, design records, and runbooks. Use the tool's own configuration files and connectors, such as Model Context Protocol (MCP) servers or rules files; do not build retrieval engines.
- Run AI as part of CI/CD: execute tests when a MR opens; draft release notes when code merges; validate the spec when docs change.
- Limit what the AI can read: set workspace boundaries, exclude sensitive files, scan for secrets before content reaches the tool. Run AI-generated code in dev sandboxes before review.

## 4. Automate quality gates

- Run static analysis, security scanning, license scanning, and coverage thresholds on every PR. For DS&A repositories, include data validation, schema checks, infrastructure linting, and IAM review where applicable. Block merges on gate failure.
- Track AI-assisted changes separately from human-authored changes in the continuous integration (CI) system where tool support and workflow metadata make that reliable.

**Implementation**

- Keep CI gate configuration in the repository alongside application code: thresholds, scanners, gate definitions. Changes to gates go through code review like any other change.
- Mark AI-assisted work at the merge request level so CI can track AI-assisted failure rates separately. Use the AI tool's audit features if it provides them; otherwise add a commit-metadata convention or MR label.
- Use CI gate failures to find what to fix. When AI-assisted code fails a gate, trace it to a rules file, prompt template, connector setting, or context source. Fix that, then watch the failure rate drop on the next runs.
- Update the AI tool's configuration when output quality drops. If AI misses the spec's intent, improve the spec template; if output lacks context, connect more sources.

## 5. Introduce autonomous agents on bounded tasks

- Once Sections 1–4 are in place, assign autonomous agents to bounded tasks: dependency upgrades, triage, scaffolding, documentation updates, and merge request summaries.
- Keep human oversight on every agent action. Agents may open merge requests, but they may not approve, merge, deploy, modify production infrastructure, or change IAM boundaries without human review. The CI gates from Section 4 are the validation layer agents depend on. Update the agent's configuration when its behavior drifts from what you expect.
- Treat end-to-end autonomy as a direction, not a near-term target. Field-wide validation tooling has not caught up with the operational risk autonomous agents introduce at production scale.

**Autonomous-agent practices** — the next phase after bounded-task agents.

- **Multi-agent roles** — split the SDLC across specialist agents. A *coding agent* implements features and fixes from a specification. A *review agent* gates every change against correctness, coverage, and policy; it stays independent of the coding agent. A *test agent* runs unit and integration tests, drafts fixes for failures, and reports coverage gaps. A *build/deploy agent* drives continuous integration and continuous delivery (CI/CD), fixes build failures, and holds at the deployment boundary for human approval. An *operations agent* monitors production signals, opens incident reports, and drafts remediation merge requests. A *spec/architecture agent* maintains the specification as source of truth, regenerates code to match intent, and flags drift.
- **Human-machine boundaries** — humans own goal-setting, acceptance criteria, architecture, identity and access management (IAM), production deployment approval, and exception handling. Agents may not approve their own work, merge to main, deploy to production, or modify IAM. The agent that authored a change is never the agent that reviews it.
- **Feature delivery workflow** — human writes the specification → spec agent decomposes it into tasks → coding agent implements → review agent gates the merge request → test agent verifies → build agent packages → human approves deployment.
- **Bug fix workflow** — bug report arrives → triage agent classifies and reproduces. Coding agent drafts the fix on a branch → review agent enforces a regression test and policy → human approves merge → build and deploy agents proceed.
- **Incident response workflow** — operations agent detects an anomaly → opens an incident with timeline and impact → proposes mitigation (rollback, feature-flag flip) for human approval. Human executes the production-affecting step → operations agent drafts the post-incident report and follow-up tasks.
- **Target-stage caution** — full end-to-end autonomy is not always the right destination. The Quotient AI Maturity Model classifies Stage 5 (End-to-End Autonomy) as high operational risk. It identifies Stage 3 (Standardized Workflows) or Stage 4 (Supervised Automation) as the recommended target. Higher maturity demands proportionally stronger validation, oversight, and governance — invest there before extending agent scope.

---

**References**

- Quotient. *An AI Maturity Model for Engineering Teams.* 2026.
- CapitalG. *Agentic SDLC Thesis (Q1'2026).* 2026.
- DORA. *State of AI Assisted Software Development.* 2025. [https://dora.dev/research/2025/dora-report/](https://dora.dev/research/2025/dora-report/)

