# Blast Radius — Check Before Changing Shared Code

## How to Use This File

Document your project's high-impact shared packages here, organized by caution level. Before changing any listed package, assess downstream impact.

## EXTREME caution (many repos affected)
<!-- List packages that are imported by 15+ repos or services -->
<!-- Example: `@your-org/core`, `@your-org/shared-types` -->

## HIGH caution (moderate impact)
<!-- List packages imported by 10+ repos or services -->
<!-- Example: `@your-org/client`, `@your-org/server` -->

## Rules
- Adding new features to shared packages is generally safe
- Changing existing public APIs or schemas is dangerous — check all consumers first
- Before changing any high-impact package, identify all downstream dependents
- Run tests in affected repos after making changes
