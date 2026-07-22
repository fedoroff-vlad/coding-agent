# notes/ — raw local capture (everything here except this file is gitignored)

Write findings here *while they are still fresh*, in whatever language and detail is fastest —
including real system, service and repository names. **Nothing in this directory is ever committed**
(`.gitignore`: `notes/*`, with this README the single exception), which is exactly what makes it
safe to be specific.

```
notes/inbox.md      # your raw capture — create it, append to it, never commit it
```

Fastest capture wins. From opencode: *"append to notes/inbox.md: search_code missed the retry
config, query was …"*. From a shell:

```powershell
Add-Content notes\inbox.md "`n[retrieval] search_code missed the retry config; query: '...'"
```

## Before any of it leaves this machine

```powershell
.\scripts\scrub-file.ps1 notes\inbox.md      # lists every private term the file contains
```

Redact what it finds, rewrite the item in English *by shape rather than by name*, and move it into
[`../plans/INBOX.md`](../plans/INBOX.md) — that one is committed and syncs between machines. The
reasoning is in `plans/INBOX.md` and in `architecture.md` §Security: capture has to be frictionless,
this repository is public, and one file cannot be both.

This directory is also why `.private-terms` matters even when you never commit: the check is what
tells you *which* words are the ones to remove, at the moment you are about to share the file.
