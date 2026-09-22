# AGENTS.md

## Development rules

This file defines development practices. Product scope, domain terminology, and technology choices belong in the project documentation.

Before changing behavior, read the relevant documentation that exists in the repository, including specifications, architecture, data models, and source documents supplied by the user. Do not assume that documentation or decisions from another project apply here.

The authoritative source for this project's requirements is `docs/article_project.docx`, especially sections 4 and 5. Earlier article versions are superseded. Record unresolved inconsistencies explicitly; do not silently turn assumptions into requirements.

Do not silently replace documented decisions with personal preferences. If code, documentation, or requirements disagree, surface the conflict before making a broad change.

Do not select or integrate agent systems or ASR engines until the user specifies which systems to use.

Track pending article amendments in `docs/ARTICLE-REVIEW.md`. Until the user confirms that they have been applied, end each response with a short reminder. Do not edit the article itself while implementation is the current priority.

---

## Mandatory agent workflow

### Docker use restriction
Only human user canbuild or interact with docker

### Use the repository skills

The repository contains agent skills under `.agents/`.

For implementation work, use the **Caveman** skill from `.agents/caveman/SKILL.md` and follow its instructions.

For requirement clarification, architecture decisions, ambiguous product behavior, terminology conflicts, or changes that affect the domain model, use **grill-with-docs** from `.agents/grill-with-docs/SKILL.md`.

For bug diagnosis and fixes, use **diagnose** from `.agents/diagnose/SKILL.md` and follow its instructions within this repository's Docker, Git, secrets, and no-automated-UI-tests rules. Skill examples do not override these rules.

Do not imitate these skills from memory. Read the relevant skill files from `.agents/` before using them.

Use `grill-with-docs` only for genuinely consequential ambiguity. Do not turn routine engineering decisions or obvious UX behavior into an endless interview.

### Deliver observable results

Every implementation change must produce a result visible and verifiable by the user-developer in a real application usage scenario. Explain which scenario the change supports, where its result can be observed, and how to verify it. Tie backend and infrastructure work to the application behavior it enables or improves.

For documentation-only changes, identify the affected rules or documents and explain how they were checked.

---

## Git policy

**Never create Git commits.**

Do not run:

```text
git commit
git merge
git rebase
git cherry-pick
git tag
git push
```

Do not amend, squash, or rewrite repository history.

You may inspect Git state and diffs when a repository exists, for example:

```text
git status
git diff
git log
```

Leave all changes uncommitted for the user to review and commit.

Never discard unrelated user changes.

---

## Secrets and configuration

**The agent must never access, read, retrieve, generate, write, or disclose secret values.**

- All deployment secrets (passwords, API tokens, signing keys, and credentials) belong exclusively in the root `.env` file, which the user creates and edits manually. The agent must not read, create, or modify `.env`.
- Tell the user only which secret variable is required, what it is used for, and where to obtain its value or how to generate it locally. Never ask the user to send the value in chat or tool output.
- Do not inspect secrets indirectly through process environments, container inspection, expanded Compose configuration, logs, credential stores, or other files. Exclude `.env` and other known secret-bearing files from searches and reads; avoid commands that dump environment variables or resolved credentials.
- Documentation and examples may contain secret variable names and empty placeholders only. Do not provide working, generated, or default secret values.
- Non-secret application settings belong in each service's `config.json` (for example, `backend/config.json` and `frontend/config.json` when those services exist). Dockerfiles copy the relevant JSON into the container working directory; application code reads it directly. Do not introduce a duplicate root JSON or duplicate these settings in `.env` or separate environment-variable overrides. Docker network bindings and service dependencies belong in `compose.yaml`.
- Application and Docker configuration may load user-provided secrets from `.env` at runtime without exposing their values to the agent, logs, or frontend. If a tool requires environment variables for non-secret settings, derive them from the owning service's JSON. Shared settings must have one owner, including settings consumed by both the backend and database startup.
- Keep `.env` out of Git tracking and Docker build contexts. Never bake secrets into images, build arguments, source code, or frontend bundles.
- Missing required secrets must produce an error naming only the missing variable and instructions for the user, without a fallback secret or disclosure of configuration values.

---

## Docker-first development

All application development, dependency execution, migrations, backend commands, frontend commands, and runtime checks should be designed to run in Docker.

Deployment targets are WSL/Linux and Ubuntu servers. Start the project with a single `compose.yaml`; do not require PowerShell, Windows-specific tooling, or host-side preparation scripts for application startup or deployment.

Do not make the host machine a hidden dependency.

Use `docker compose up --no-build` to start existing images without triggering an implicit build. Run application commands with `docker compose exec <service> ...`, using the actual service names in the project.

### Image builds belong to the user

Prepare and maintain:

- Dockerfiles;
- `compose.yaml`;
- `.dockerignore`;
- required non-secret build arguments;
- service-owned `config.json` files for non-secret settings;
- documentation of required `.env` variable names and their value sources, without secret values;
- exact build/start commands.

However, **do not build Docker images yourself unless the user explicitly asks you to do so**.

Do not run:

```text
docker build ...
docker compose build ...
docker compose up --build ...
```

without explicit user approval.

When an image rebuild becomes necessary, stop at that boundary and give the user the exact command to run. After the user has built the images, continue working inside Docker.

The repository must remain reproducible from a clean Docker build.

Use the project's documented runtime, package manager, framework adapter, and lockfile consistently inside Docker. Do not switch tooling or introduce an additional package manager without a justified, documented decision.

---

## Minimize sources of truth

Prefer one implementation of a rule over several similar implementations. This applies to the entire codebase.

Before writing new logic, search for an existing implementation that can be reused, extracted, or generalized.

Reuse domain services, repositories, query helpers, parsing and normalization helpers, validation and permission rules, shared errors, schemas, constants, configuration, API clients, UI components, and analytics helpers.

If two flows perform the same business operation, they should normally converge on the same backend service/use-case.

Examples:

- Different input methods must use the same authoritative validation and processing rules.
- Preview and execution must share the rules that determine their results.
- A permission rule used by several endpoints must live in one reusable policy/service.
- Repeated data queries and deterministic transformations should use shared helpers.

Do not copy business rules between endpoints, services, repositories, background jobs, CLI/admin utilities, and frontend code.

If duplication appears, extract a small reusable function/service instead of maintaining parallel implementations. Avoid speculative abstractions for behavior that is not shared.

---

## Keep backend logic centralized and reusable

Backend business logic belongs in reusable application/domain services, not directly in route handlers.

Route handlers should mostly:

1. validate request input;
2. enforce the applicable authorization policy;
3. call a reusable service/use-case;
4. serialize the result.

Do not hide business rules in repository methods, route handlers, SQL fragments, or frontend code when they belong in a reusable domain/application service.

The frontend may perform UX validation, but the backend remains authoritative for permissions, data validation, domain rules, state transitions, and persistence invariants.

Use shared domain helpers for deterministic transformations and shared error types translated at the API boundary.

Before adding an endpoint or workflow, check whether it can reuse an existing service. Prefer composition of small reusable services over copying code into a new workflow.

---

## Shared data contracts

Use explicit typed contracts:

- backend request/response schemas and typed service interfaces;
- enums or equivalent constrained types for stable states;
- frontend types derived from or kept directly aligned with API contracts;
- shared components for repeated visual states.

Avoid multiple string literals for the same status across the codebase.

---

## Prefer simple code

Choose the simplest design that satisfies the documented requirement.

Do not introduce microservices, message brokers, additional databases, generic plugin frameworks, elaborate event systems, or premature abstractions unless the current requirement and measured behavior justify them.

A small amount of explicit code is preferable to a clever abstraction that creates more failure modes. Extract stable shared behavior when repetition becomes apparent.

Do not expand product scope merely because an adjacent feature seems useful.

---

## Frontend component reuse

Before developing the main frontend, stop and wait for the user to provide components from previous projects. Inspect those components and reuse their suitable logic before implementing new components. This checkpoint is explicitly required by the user; do not proceed past it without the components or an explicit change to this instruction.

Build the UI from reusable components rather than page-specific copies.

Reuse components for recurring navigation, tables, pagination, form controls, status badges, dialogs, empty/loading/error states, and chart containers.

If two screens look and behave substantially the same, first try to parameterize an existing component.

Do not create a design-system abstraction for a component that is only used once.

Follow the language and terminology established by the current requirements. Do not add localization infrastructure without a requirement.

---

## UI tests

**Do not write automated UI tests.**

Do not add Playwright tests, Cypress tests, browser E2E suites, component snapshot tests, or component tests whose purpose is testing rendered UI behavior.

For UI work, rely on type checks, framework/compiler checks, linting/formatting, and manual verification in the running Dockerized application.

Backend/domain tests are allowed and encouraged where they protect important reusable logic, including deterministic transformations, validation, permission rules, state transitions, calculations, concurrency protection, and idempotency.

Do not create tests merely to increase coverage numbers.

---

## Error handling

Prefer explicit failure states over silent fallback behavior.

Do not silently guess when input is ambiguous, data is invalid, a required integration is unavailable, or authorization is unclear.

Return errors that give the UI enough information to explain what happened.

Do not expose stack traces or secrets to clients.

---

## Database and migrations

When a database is used, all schema changes must be represented by migrations using the project's chosen migration tool.

Do not manually mutate the development database as a substitute for a migration.

Keep constraints in the database when they protect real invariants. Use request-scoped sessions/transactions and concurrency protection where operations can conflict.

Do not enforce permissions only in frontend logic.

---

## Dependencies

Before adding a dependency, check whether the repository or standard library already provides what is needed.

Prefer established, focused dependencies. Do not add a large framework for a small convenience.

When introducing a dependency:

- explain why it is needed;
- pin/manage it through the project's normal dependency mechanism;
- ensure it works in Docker;
- avoid duplicating an existing dependency's responsibility.

---

## Documentation discipline

When a domain term or behavior changes, update the appropriate documentation.

Use `grill-with-docs` for substantial unresolved product/domain questions.

Keep domain glossaries focused on terminology. Requirements belong in specifications; architecture and implementation decisions belong in architecture documentation.

Create documents only when there is relevant content to record. Do not assume that a particular documentation template already exists.

Only create an ADR for a decision that is:

1. costly to reverse;
2. non-obvious to a future maintainer;
3. the result of a real trade-off.

---

## Before declaring a task complete

Check what applies to the change:

- the implementation follows the current project documentation;
- Docker configuration is sufficient to reproduce the work;
- no Docker image build was performed unless explicitly requested;
- no Git commit was created;
- no secret values were accessed, generated, written, or disclosed by the agent;
- deployment secrets are user-managed in `.env`; non-secret settings have one owning service configuration;
- business logic is not duplicated unnecessarily;
- reusable components/services were used where appropriate;
- backend/domain checks pass inside Docker where possible;
- no automated UI tests were added;
- migrations exist for schema changes;
- unrelated user changes were not overwritten;
- any required image rebuild command is handed to the user explicitly.

End with a concise summary of what changed, what was verified, and what the user must run, especially any required Docker image build command. State any verification that could not be performed.
