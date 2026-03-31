# Copilot Instructions

## General principles

- Prefer safe, maintainable, and portable solutions.
- Keep code clear, practical, and easy to review.
- Unless the user explicitly asks otherwise, choose solutions that can run on multiple operating systems.
- Prefer widely available, modern tooling and language versions, for example Python 3 instead of legacy Python.

## Security

- Never write secrets into the repository, code, comments, examples, tests, configuration, or documentation.
- Never hardcode API keys, tokens, passwords, private URLs with embedded credentials, SSH private keys, or session cookies.
- Use environment variables or local secret files that are excluded from version control.
- When a script needs credentials, show how to read them securely from the environment.
- Do not print secrets in logs, error messages, or debug output.
- Do not invent example secrets that look real.

## Code style

- Write all comments, variable names, function names, and identifiers in English, regardless of the language of the user prompt.
- Do not add obvious comments that only repeat what the code already says.
- Do not add comments addressed to the user that explain what you just changed.
- Prefer self-explanatory code over excessive comments.
- If the language supports it, follow clean code principles:
  - keep functions small and focused
  - avoid deeply nested logic
  - extract reusable logic
  - use meaningful names
  - keep code easy to read and modify
- Avoid unnecessary abstraction, but do not put too much responsibility into one function or file.

## Portability and dependencies

- Unless explicitly requested otherwise, prefer cross-platform solutions over OS-specific ones.
- Prefer Python scripts over shell scripts when portability matters.
- Avoid commands or approaches that only work on one operating system unless the task clearly requires that platform.
- Minimize dependencies. Use the standard library when it is sufficient.
- If extra libraries are needed, use well-known and actively maintained packages.

## Script header

- If a script requires installing dependencies, start the file with a short comment block showing the commands needed to run it.
- Use this format:

```
# Commands:
# source ../venv/bin/activate
# pip install requests openai python-dotenv
# python solution.py
```

- Adapt the commands to the actual project and dependencies.
- Do not include commands that are unnecessary for the solution.

## Git and repository operations
- **No Auto-Committing:** NEVER attempt to commit, push, or stash changes on your own. Only provide the code or shell commands. Leave all Git operations and version control management strictly to the user.
- Be careful with destructive operations.
- Do not suggest or generate commands that rewrite history, force-push, delete branches, delete tags, or remove files unless the user explicitly asks for that.
- Before generating commands that modify a repository, prefer safer variants where possible.
- When working with files, avoid unintended changes outside the requested scope.
- Respect existing project structure and naming conventions.

## Quality

- Handle errors in a practical way.
- Validate inputs when appropriate.
- Produce deterministic, predictable behavior when possible.
- Prefer explicit and readable code over clever one-liners.
- When generating scripts, make them usable as real tools, not just quick demos.
- Keep output messages concise and useful.
- If the task is ambiguous, choose the safest reasonable interpretation.

## Tests and examples

- When relevant, include small usage examples.
- Add tests only when they bring real value and fit the repository style.
- Do not generate fake tests that only assert trivial behavior.

## What to avoid

- Do not add boilerplate just to make the solution look more advanced.
- Do not add dead code, unused helper functions, or placeholder implementations.
- Do not add explanatory comments like "this function does X" when the code is already clear.
- Do not expose secrets even in mocked examples.
- Do not assume a specific shell, path separator, or operating system unless required.
