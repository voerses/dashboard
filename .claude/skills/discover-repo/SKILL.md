---
name: discover-repo
description: "Systematically explore a repo to understand its structure, patterns, and conventions"
user_invocable: true
---

# Discover a repo's structure, patterns, and conventions

Perform a structured exploration of a repository to understand what it does, how it works, and how it fits into the broader system.

## Usage

The user will specify a repo name. For example:
- `/discover my-service`
- `/discover shared-lib`

## Instructions

Read the argument provided by the user (`$ARGUMENTS`). This is the repo name to explore.

### Step 0: Locate the Repo

1. Look for the repo at `{repo-name}/` (sibling of `aidev/` at the project root).
2. If not found, check for close name matches (e.g., hyphens vs underscores).
3. If still not found, tell the user the repo is not cloned and suggest cloning it.
4. Stop and wait for the user before continuing.

### Step 1: Manifest Scan

Read the project manifest to identify language, dependencies, and available scripts:

- **JavaScript/TypeScript repos:** Read `{repo-name}/package.json`. If it has a `workspaces` field, this is a monorepo — also read `packages/*/package.json` for each workspace.
- **Go repos:** Read `{repo-name}/go.mod`. Check for `cmd/` directory for CLI entry points.
- **Hybrid repos:** Some repos have both. Check for both `package.json` and `go.mod`.

Extract and note:
- Language(s) and runtime
- Key dependencies
- Available scripts (`npm run`, `make`, `go build`)
- Monorepo structure if applicable (list workspace packages)

### Step 2: Entry Point Mapping

Find the main entry points based on what Step 1 revealed:

**For JS repos:**
- Read `src/index.js` or `src/index.ts` (or the `main`/`module` field from package.json)
- Check for `wrangler.toml` or `wrangler.jsonc` (Cloudflare Worker)
- Check for `sst.config.*` or `stacks/` directory (SST infrastructure)
- Check for `bin/` directory or `"bin"` field in package.json (CLI tool)

**For Go repos:**
- Read `cmd/main.go` or `cmd/*/main.go` (CLI entry points)
- Check for `internal/` vs `pkg/` directory layout
- Look for HTTP handler setup (mux/chi/gin/net-http)

**For monorepos:**
- Identify the "main" package (usually the one with the service entry point)
- Map which packages are libraries vs services vs CLIs

### Step 3: Pattern Recognition

Classify the repo by matching against common patterns. Read the relevant files to confirm each pattern:

| Pattern | Indicators | Classification |
|---------|-----------|----------------|
| **HTTP service** | Has HTTP handler setup, routes, middleware | Web service |
| **Cloudflare Worker** | Has `wrangler.toml`, exports `fetch` handler | Edge service |
| **SST infra** | Has `sst.config.ts`, `stacks/` directory | Infrastructure-as-code |
| **CLI tool** | Has `bin/` or commander/yargs/urfave-cli deps | Developer tool |
| **Library** | No entry point, only exports, consumed by other packages | Shared library |
| **Go service (uber/fx)** | Uses `fx.New()`, `fx.Module()`, `fx.Provide()` | Go dependency-injected service |

Note which patterns match. A repo may match multiple patterns.

### Step 4: Test Infrastructure

Identify the testing setup:

**JS repos:**
- Look for `test/` or `__tests__/` directory
- Read `package.json` for test scripts and test framework deps
- Check for `test/helpers/` (shared test fixtures)
- Check for `.env.test` or test-specific configuration

**Go repos:**
- Look for `*_test.go` files
- Check for `testdata/` directories
- Check for mockery configuration (`mockery.yaml` or `.mockery.yaml`)
- Check for `internal/testutil/` or similar test helper packages

Note the test command(s), framework, and any special test infrastructure.

### Step 5: Dependency Blast Radius

Check the repo's key exports against `.claude/rules/blast-radius.md`:

1. Read `.claude/rules/blast-radius.md`.
2. Determine which blast radius tier the repo's packages fall into.
3. Identify which other repos depend on this repo's packages.

## Output Format

Present the exploration results in this structured format:

```
## Repo Discovery: {repo-name}

### Identity
- **Role:** (1-line description)
- **Language:** JS/Go/Hybrid
- **Runtime:** Node.js / Cloudflare Worker / Go binary / etc.
- **Pattern(s):** (from Step 3 classification)

### Structure
(Directory tree of key paths)

### Entry Points
- **Main:** (file path and what it does)
- **CLI:** (if applicable)
- **HTTP:** (handler setup location)

### Dependencies
- **Key imports:** (high-blast-radius packages this repo uses)
- **Blast radius tier:** (if this repo exports packages others consume)

### Test Infrastructure
- **Framework:** (mocha/vitest/testify/etc.)
- **Command:** (how to run tests)
- **Fixtures:** (shared test helpers, etc.)

### Infrastructure
(Database tables, buckets, queues, KV namespaces — if applicable)
```

Present the results to the user for review. Ask if there are any undocumented conventions or tribal knowledge to include.

## Tips

- For monorepos, focus the exploration on the most important 3-5 packages, not every single one.
- Some repos have a `README.md` that provides useful context — skim it but don't rely on it as the source of truth (code over specs).
- For Go repos, the `go.sum` file reveals the full transitive dependency tree but is usually too large to read. Focus on `go.mod` direct dependencies.
- When classifying patterns, check both `src/` and `lib/` directories — some repos use one convention, some the other.
