# AIPIP-0022: Gate 6 Paper Trading Deployment Instructions

**Status:** accepted
**Created:** 2026-03-11
**Author:** Claude (requested by user)

## Problem

The strategy development pipeline (Gate 6) says "Deploy on live data, simulated execution" but provides no concrete instructions for:
1. How to add a new strategy to the existing multi-portfolio paper trading runner
2. How to ensure the dashboard correctly shows all portfolios (not just the new one)
3. How to verify the deployment was successful

This has already caused issues — deploying s62 accidentally overwrote the dashboard showing only the new portfolio instead of all existing ones.

## Proposal

Add a **Deployment Checklist** subsection to Gate 6 in SKILL.md with step-by-step instructions for:
1. Creating the portfolio config file
2. Adding it to the multi-runner config (not replacing existing portfolios)
3. Restarting the multi-runner (not using `--once` which overwrites the dashboard)
4. Verifying all portfolios appear in the dashboard

## Changes

### File: `.claude/skills/strategy/SKILL.md`

Add deployment checklist to Gate 6 section, between the initial kill criteria and the degradation budget.

## Risk Assessment

- **Blast radius:** SKILL.md only (process documentation)
- **Risk:** Low — purely additive documentation, no behavioral changes
