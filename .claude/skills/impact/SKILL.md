---
name: impact
description: Assess blast radius of changes to packages, capabilities, or services
user_invocable: true
---

# Assess blast radius of a change

Analyze what will be affected if you change a specific package, capability, or service.

## Usage

The user will specify a package name, service, or component to assess. For example:
- `/impact @your-org/core`
- `/impact auth-service`

## Instructions

1. Read the argument provided by the user (`$ARGUMENTS`).

2. Read the blast radius reference in `.claude/rules/blast-radius.md` to check if this is a known high-impact package.

3. Investigate the actual impact:
   - Search the codebase for imports/dependencies on the target package
   - Identify direct dependents (repos/packages that directly import this)
   - Identify transitive impact (what breaks downstream)
   - Check if this is a safe additive change or a breaking change

4. Report:
   - **Direct dependents**: Repos/packages that directly import this
   - **Transitive impact**: What breaks downstream
   - **Safe vs dangerous**: Is this a safe additive change or a breaking change?
   - **Testing scope**: Which repos need testing after this change
   - **Migration needs**: Does this require a coordinated rollout?
