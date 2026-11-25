---
name: hub-update
description: Log this session's progress to the matching project in Layton's Inbox Hub, with a short summary and the next steps (which land on his task list and in his reminders). Use when the user types /hub-update, or asks to "update the hub", "log this to the project", "push this session to the hub", or "remind me of the next steps".
---

# Log this session to the Inbox Hub

Layton keeps long-running work as **Projects** in his Inbox Hub. This skill turns the current
Claude Code session into an update on the right project: a summary, the next steps (each becomes
a task on his list), and a link back to this session. The hub then reminds him through its
check-ins and morning brief.

## 1. Where the hub is

- The hub's address is in the environment variable `INBOX_HUB_URL` (e.g. `https://…`, no trailing
  slash). It is deliberately not written in any repository. If it's empty, stop and tell Layton:
  "Add INBOX_HUB_URL to this environment's variables (the hub's address), then run /hub-update
  again."
- Authentication: in a cloud session the network proxy adds the hub's token itself, so send a
  placeholder header, `Authorization: Bearer hub`. If `INBOX_HUB_TOKEN` is set (a session on
  Layton's own computer), send `Authorization: Bearer $INBOX_HUB_TOKEN` instead.
- The link to this session: if `CLAUDE_CODE_REMOTE_SESSION_ID` is set (e.g. `cse_abc123`), the
  link is `https://claude.ai/code/session_` followed by that id without its `cse_` prefix.
  Otherwise leave the link out.

## 2. Write the update

From this session (the conversation, and the commits and changes made in it), write:

- **summary**: 2 to 5 plain sentences on what's now true: what was built, fixed, decided or
  shipped, and anything left broken or waiting. Outcomes, not process. No file paths unless they
  matter to Layton.
- **steps**: up to 6 next steps, each a short action that can be ticked off ("Add Slack
  check-ins to Projects", not "Continue work"). Only real follow-ups: things not done, decisions
  Layton owes, things to check after a deploy. Set `person` only when someone other than Layton
  must do it. Leave steps out entirely if nothing is outstanding.

## 3. Find the project

Get the projects: `GET $INBOX_HUB_URL/api/agent/projects`. Each has `id`, `number`, `title`,
`outcome`, `keywords` and `area` (dogstar, evo or personal).

- Pick the one this session's work belongs to, by the repository name, the title, its keywords
  or its outcome. If exactly one fits, use it.
- If several could fit, or none does, ask Layton which one, offering to create a new one. For a
  new one, suggest a title from the repository and what it's for, the area (dogstar, evo or
  personal), weekly check-ins and the repository name as a keyword, then create it once he agrees:
  `POST $INBOX_HUB_URL/api/agent/projects` with `{ "title", "outcome", "area", "rhythm": "weekly", "keywords": ["<repo name>"] }`.

Before adding steps, read the project (`GET $INBOX_HUB_URL/api/agent/projects/<id>`) and leave
out any step that is already an open step there (same meaning, even if worded differently).

## 4. Send it

`PATCH $INBOX_HUB_URL/api/agent/projects/<id>` with JSON:

```json
{ "update": "<summary>", "from": "Claude Code", "source": "claude-code", "link": "<session link>", "steps": [{ "title": "<step>" }, { "title": "<step>", "person": "<name>" }] }
```

Build the JSON with Python's `json.dumps` (not by hand) and send it with `curl -sS -X PATCH -H
"Content-Type: application/json" -H "Authorization: …" --data-binary @file`. A 200 reply is the
project with its steps; anything else, show Layton the error and stop. Never retry a failed call
more than once.

## 5. Tell Layton

One or two lines: which project was updated, how many next steps were added, and the link
`$INBOX_HUB_URL/projects/<id>`. Don't repeat the whole summary back.

Nothing here sends email or messages anyone: the hub only records the update and steps.
