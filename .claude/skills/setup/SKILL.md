---
name: setup
description: "Set up aidev symlinks so Claude Code loads the process from aidev/"
user_invocable: true
---

# Set up aidev for your project

Create the 3 symlinks that wire Claude Code to aidev's process files.

## Instructions

Run the setup script:

```bash
./aidev/setup.sh
```

This creates 3 symlinks in the current directory (project root):
- `.claude → aidev/.claude` (hooks, rules, skills, settings)
- `CLAUDE.md → aidev/CLAUDE.md` (root instructions)
- `.specs → aidev/.specs` (workflow templates + state)

After the script runs successfully, tell the user:

> Setup complete! The process is now active. Clone your project repos alongside aidev/:
> ```
> gh repo clone your-org/your-repo
> ```
>
> Next time, just run `claude` from this directory — everything is wired up.
