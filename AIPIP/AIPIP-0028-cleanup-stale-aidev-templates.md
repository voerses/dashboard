---
id: AIPIP-0028
title: Remove stale language templates, populate conventions and blast radius
status: accepted
author: @claude
created: 2026-03-28
---

## Problem

The aidev framework was forked from storacha, a multi-language (Go/JS/TS) project.
This crypto backtest project is Python-only. Four irrelevant language rule files are
loaded on every session, wasting context and confusing the AI:

- `golang.md` — Go conventions (testify, mockery, snake_case Go files)
- `javascript.md` — JS/TS conventions (kebab-case, Vitest)

Additionally, `conventions.md` and `blast-radius.md` are empty templates with only
HTML comment placeholders — the AI gets no actual guidance from them.

## Changes

### Deletions (already done)
- `aidev/.claude/rules/golang.md` — irrelevant
- `aidev/.claude/rules/javascript.md` — irrelevant
- `crypto_backtest/.claude/rules/golang.md` — duplicate of above
- `crypto_backtest/.claude/rules/javascript.md` — duplicate of above
- `crypto_backtest/.claude/rules/blast-radius.md` — duplicate empty template
- `crypto_backtest/.claude/rules/conventions.md` — duplicate empty template

### Replacements
- `aidev/.claude/rules/conventions.md` — replace empty template with actual Python conventions
- `aidev/.claude/rules/blast-radius.md` — replace empty template with actual v4/ module impact data

## Risk

Low. Removing empty templates and irrelevant language files reduces noise. Replacing
them with real content improves AI adherence to actual project patterns.
