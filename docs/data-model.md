# Taskman SQL data model

This document defines how Taskman stores work items in SQLite and Postgres. It applies decisions D5 to D8 of epic 261108000. The lifecycle rules in [the specification](specification.md) do not change. This document shows how each rule applies to the SQL model.

The file backend (`tasks/` Markdown files) does not use this model. Feature 261109005 removes the file backend after the SQL backends are complete.

## Entities

### Project

A project holds the work items of one product or repository. One database holds many projects.

| Field | Rule |
| --- | --- |
| key | 2 to 10 characters: uppercase letters and digits. The first character is a letter. Unique in the database. Example: `TM`. |
| name | 1 to 200 characters. The default is the key. |
| next number | The number for the next work item. It starts at 1. |

Work is centered on items, not on repositories (decision M5). A command that names an item key, such as `TM-42`, finds the item and its project from the key. No setting selects a project.

- A new top-level item needs `--project KEY`. A new child gets the project of its parent.
- Taskman creates a project the first time `new --project` names an unknown key.
- `list`, `ready`, `search`, `browse` and the UI show the items of all projects. `--project KEY` limits them to one project. The UI has a project filter.

### Work item

| Field | Rule |
| --- | --- |
| key | `<project key>-<number>`, for example `TM-42`. Unique in the database. Taskman never gives a number to a second item, also after a discard. |
| type | `epic`, `feat` or `task`. |
| title | 1 to 200 characters. |
| description | Markdown text. This is the requirements text. It replaces the file body. |
| status | `backlog`, `active` or `done`. `done` means the item is complete. Status is a field of the item: `move` and `close` change its value. Nothing moves between tables. |
| draft | True when the item is new. `finalize` sets it to false. |
| parent | A work item in the same project, or none. A `task` has no children. |
| rank | The position among siblings. Lower rank shows first. |
| revision | An integer that starts at 0. See "Revision". |
| assignee | A person or an agent name, for example `claude-code`. Optional. 1 to 100 characters. |
| labels | A set of labels. Each label has 1 to 50 characters: lowercase letters, digits, `-` and `_`. |
| created | Time and author of the creation. |
| changed | Time of the last change to any field, comment, link or label. |

### Comment

A comment replaces a journal entry.

| Field | Rule |
| --- | --- |
| kind | `decision`, `progress`, `handoff`, `summary` or `verification`. |
| result | `pass`, `fail` or `blocked`. Only a `verification` comment has a result, and it must have one. |
| author | 1 to 100 characters: the `--actor` value, or the git `user.name` (decision M1). |
| text | Markdown text. It must not be empty. |
| revision | The item revision when Taskman stored the comment. |
| created | The time of the comment. |

Nobody can change or delete a comment. A comment is evidence.

### Dependency link

A link says that one work item depends on another work item in the same project. A link can not make a cycle. Taskman includes parent completion when it checks for cycles: a parent closes after its children, so a child can not depend on its parent or on an ancestor.

### Activity entry

Taskman writes one activity entry for each change to a work item.

| Field | Rule |
| --- | --- |
| item | The work item. |
| field | The changed field: `created`, `title`, `description`, `status`, `draft`, `type`, `rank`, `assignee`, `labels` or `dependencies`. |
| old value, new value | The values before and after the change. For `description`, Taskman stores the full old and new text. |
| author | Who made the change: the `--actor` value, or the git `user.name` (decision M1). |
| time | The time of the change. |

A comment does not make an activity entry. The comment itself is the record.

## Lifecycle rules

Each rule of epic requirement R1 applies to the SQL model as follows.

| Rule | SQL model |
| --- | --- |
| Drafts | A new item has draft true. `finalize` sets it to false. A draft can not become active or done. An item can not become active when an ancestor is a draft. |
| Close gates | `close` needs: no open descendant, no unfinished dependency on the item or an ancestor, a `summary` comment at the current revision, and the latest `verification` comment at the current revision with result `pass`. |
| Immutability | The change of status to `done` is the last change to an item. After it, the item accepts no change and no comment: no field, link, label, assignee or rank changes (decision M2). |
| Revision | The revision increases by 1 when the description, type or dependencies change, and when a `decision` or `progress` comment is added. When an item gets a new revision, a new child, a discarded child, or a status change, the revision of each ancestor increases by 1. Assignee, labels and rank do not change the revision. |
| Cycles | `depend` refuses a link that makes a cycle, with parent completion included. |
| Task is a leaf | A `task` can not have children. `convert` to `task` is refused when the item has children. |
| Discard | Only a draft with no children and no item that depends on it can be discarded. Discard deletes the item with its comments, links, labels and activity. The key number is not used again. |
| Rank | `reorder` moves an open item among its siblings. A done item keeps its rank. Moving one item does not change the rank of a done sibling. |
| Readiness | An item is ready when it is not done, and the item and all its ancestors are finalized, not done, and have no unfinished dependency. |

## Transactions and locking

Each lifecycle operation runs in one database transaction. An operation reads, checks the rules and writes in that transaction. Two operations on the same project can not interleave. If a check fails, the transaction stops and the database does not change.

Key numbers come from the project's next number in the same transaction. Two writers can not get the same key.

## Commands and UI

| Today | SQL backends |
| --- | --- |
| `new`, `finalize`, `update`, `edit`, `depend`, `move`, `convert`, `close`, `discard`, `reorder`, `list`, `show`, `context`, `ready`, `dependents`, `waiting-on`, `search`, `browse`, `ui` | Same commands. They accept and print keys such as `TM-42`. `new` without `--parent` needs `--project KEY`. Listing commands accept `--project KEY`. |
| `record` | `comment` adds a comment with the same options (`--kind`, `--actor`, `--result`, `--body-file`). `record` stays as an alias until feature 261109005 updates the plugin skills. |
| none | `assign KEY NAME`, `unassign KEY`, `label KEY --add L --remove L`, `activity KEY`. `list`, `browse` and the UI filter by assignee and label. |
| `validate` | Checks dependency links and the schema version. File layout checks do not apply. |
| `consolidate`, `history` | Not available (D8). The command stops with a message. |
| `migrate`, `worktree-status` | File backend only. On a SQL backend the command stops with the message "This command works only with the file backend." |

The UI shows comments, activity, assignee and labels in the item details. The UI can set the assignee and labels and add comments.

### Output

The JSON output of `show --json` and `context` keeps today's shape, so the plugin skills continue to work:

- `id` is the key.
- `path` is the location (see below).
- `frontmatter` has the fields of the work item. Its `journal` list has the comments in the journal entry form (`kind`, `actor`, `at`, `text`, `revision`, `result`).
- `body` is the description.

`context` also gives `activity` for the item.

The location of an item is `<backend>:<key>`, for example `sqlite:TM-42`. Commands that print a path today print this location. Error messages that list child file names list child keys.

`show` without `--json` prints the item as Markdown: a frontmatter block with the fields, then the description.

### Last change

`Item.modified` is the time of the last change (the `changed` field). The UI uses it for the "recently done" panel.

## Inputs from feature 261108001

| Input | Answer |
| --- | --- |
| Locking across one lifecycle operation | One transaction for each operation. See "Transactions and locking". |
| Checks that assume 9-digit IDs | Removed. Keys follow the key rule above. Task 261109011 removes the checks in the UI and the help text. |
| Location | `<backend>:<key>`. |
| `worktree-status` | File backend only. Feature 261109005 removes it. |
| Last change time | The `changed` field. The contract suite checks it. |
| `migrate` | File backend only. Feature 261109005 removes it. |

## Out of scope

- Custom workflow states, sprints, estimates and due dates.
- User accounts and permissions. The author is a name, not an account.
- Attachments.
- Changing or deleting comments.
- Import from `tasks/` files. SQL storage starts empty (decision D4, confirmed on Nov 9, 2026).

## Decisions

- **M1 Author of a change (agreed Nov 9, 2026).** Every command that changes an item accepts an optional `--actor NAME`. Without it, the author is the git `user.name`. The author goes into the activity entry and into comments.
- **M2 Done items (agreed Nov 9, 2026).** The change of status to `done` is the last change to an item. After it, nothing changes: not fields, links, labels, assignee, rank or comments. Today the UI can still reorder done items; that stops.
- **M3 `record` becomes `comment` (agreed Nov 9, 2026).** The new name matches the model. `record` stays as an alias until feature 261109005 updates the plugin skills.
- **M4 JSON shape (agreed Nov 9, 2026).** `show --json` and `context` keep today's keys, with comments in `frontmatter.journal`. A Jira-like shape (for example `fields` and `comments`) can come with feature 261109005, together with the plugin updates.
- **M5 Project selection (agreed Nov 9, 2026).** A repository does not select a project. The item key identifies the item and its project. A new top-level item names its project with `--project KEY`; listings show all projects unless `--project` limits them.
