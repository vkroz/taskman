# Taskman workspace design

The workspace is one HTML file with no build step and no external files. It works offline. It shows what Taskman derives, not raw fields: state, blockers, leaf progress, latest verification and close readiness. Agents finalize, move and close items through the CLI. The page shows whether an item can close and why not.

## Layout

- A sidebar holds the views, the projects and the shortcut list. Each view and project shows its count.
- A 48px top bar shows the view title, the project scope, the match count, the search field and **New**.
- A toolbar holds facet filters (Project, State, Type, Assignee, Label, Draft), an advanced filter, the sort order and the Display menu.
- The list is a tree grid with 36px rows. A row shows the type icon, the key, the title, up to two labels, a Draft chip, the verification mark, leaf progress for epics and features, the state, the owner and the last update. A blocked row names its blockers as links.
- The detail panel opens beside the list at 1150px and wider. It is 420px wide, and 480px from 1600px. On narrower screens it is a full-height sheet with a Back control.
- Below 720px, a 48px app bar has a view select, search, the connection indicator and a **New item** button. All filters move to one sheet. Rows have two lines.
- As the list narrows, its columns drop in this order: last update, owner name, labels, progress.

## Views

The views are Attention (the default), Active, Ready, Blocked, Drafts, Backlog and Done. Attention, Ready, Blocked and Done are flat lists with a parent breadcrumb on each row. Attention rows show the reason. Blocked groups items by their first blocker. Done shows the newest first, grouped as Today, This week and Earlier. Active, Backlog and Drafts are trees with dimmed context ancestors. When more than one project is visible, a header separates the projects.

## Filters, search and sort

A facet opens a checklist of the values in the current view, with counts. A click applies the filter at once. Values of one facet combine with OR, and facets combine with AND. Each applied filter shows as a chip that removes it. **Clear** removes the search, the facets, the advanced conditions and the epic scope. The advanced filter offers item fields with display names and applies 300ms after the last change. Search matches text in the item and its metadata. A key such as `TM-10` followed by Enter opens that item. In tree views, sort applies among siblings. Flat views sort the whole list. Manual order changes by drag within one parent and one project. Done items cannot move. An empty result names each active filter and can remove it.

## Detail panel

From top to bottom, the panel shows:

1. The type, the key (a click copies it), Copy link, previous and next, and close.
2. The project and the ancestors as links.
3. The title, the state, the owner, the labels, and the creation and update times.
4. **Edit** and a menu with Add comment, Assignee and labels, Show only its sub-items, and Delete draft. Delete draft shows only for a draft that can be discarded. Otherwise the menu gives the reason, for example "has 1 child". A done item shows "Closed with evidence" and no actions.
5. Close readiness for open items: finalized, dependencies, sub-items, summary and verification for the current revision. The verdict is "Ready to close" with the `taskman close` command, or "Not closable" with the number of open checks.
6. The description as Markdown. The page builds every node with DOM text, so item text never becomes markup. A first heading equal to the title is not repeated.
7. Sub-items with a segmented progress bar, blocked items first. **Add sub-item** shows on open epics and features.
8. Depends on and Blocks.
9. One timeline of comments and field changes, newest first. Filters are All, Evidence, Decisions, Handoffs and Changes. Each comment shows its kind, actor, revision and time. A verification shows its result. A divider marks the start of each revision.

## Dialogs and changes

All dialogs share one component with a title, a close button and a footer. Ctrl+Enter or Cmd+Enter submits. When a form has changes, Esc, Cancel or a click outside asks before it discards them. A submit disables its button until the server answers, so a second submit does nothing.

- **Create** has a title field, type pills, a parent picker grouped by project and a project select. A parent sets and locks the project. A new project needs a second submit to confirm. The description is optional. After create, the page expands the parent, shows and flashes the new row, and offers to copy `/tm:plan KEY`.
- **Edit** changes the title and the description of open work. A reason is required and becomes a decision comment by the store actor. A draft can have an empty description. If someone changed the title or description after the dialog opened, the dialog keeps the typed text and offers Reload theirs or Overwrite with mine.
- **Add comment** defaults to Progress and explains each kind. A verification requires Pass, Fail or Blocked.
- **Assignee and labels** saves both in one request. The page lowercases labels and rejects labels that do not match `^[a-z0-9_-]{1,50}$`.

Completed records remain read-only. Delete is limited to unreferenced draft leaves.

## Live updates and URL

The page polls every 5 seconds after the previous poll completes. It sends the last etag, so an unchanged workspace returns no rows. A poll never moves focus, clears a text selection or rebuilds a control in use. Rows are patched in place by key. A changed row flashes for 2 seconds. The open item renders again only when it changed and the user is not reading or typing in it. Polling stops while the page is hidden. After a network error, the page retries after 10, 30 and 60 seconds. The connection indicator shows Live, Reconnecting with Retry, or Offline since a time with the last data still visible. Messages after a change are toasts that disappear after 4 seconds.

The URL holds the view, the project, the search, the filters, the sort and the open item, for example `/?view=ready&project=RES&label=bug&item=RES-5`. Filter changes replace the history entry. Opening an item adds one, so Back closes the panel. Collapse state and the theme choice stay in browser storage.

## Keyboard

The list is one tab stop. Tab from search goes to the list. ↑ and ↓ or j and k move, and the open panel follows. ← and → collapse and expand. Home and End go to the first and last item. Enter or o opens the item and moves focus to the panel. Esc closes the innermost layer and returns focus to the row. / focuses search. c creates an item under the focused epic or feature. ? shows the shortcuts.

## Visual system

Colours are tokens for a light and a dark theme. The theme follows the system. The Display menu can set light or dark. Colour shows meaning only: state, verification and blocked. Violet marks interaction: the primary button, focus, selection and links. Text has a contrast of at least 4.5:1, and focus rings and input borders at least 3:1. Type sizes are 12, 13, 14, 16 and 20px, with weights 400, 500 and 600. Spacing uses a 4px grid. Radii are 4, 6 and 8px. Icons are inline SVG and always have a text or label next to them.

## Checks

`tests/ui/test_workspace_ui.py` drives Chromium through Playwright against a seeded `taskman ui` server at 1440x900 and 390x844. It checks density, row content, views and counts, projects, URL state, the detail panel, polling safety, data integrity, filters, the keyboard, create and comment, errors, the mobile layout, contrast in both themes and escaping. Each test that changes data gets its own server on a copy of the seed. `tests/ui/test_workspace_css.py` checks without a browser that styles use only the tokens and the type, weight and radius scales. All these tests are marked `ui`: `uv run pytest -m ui` runs only them. The browser tests need the `ui` extra (`uv sync --extra ui`). They skip when Playwright is not installed or Chromium cannot start.

## Query service

Implementation uses a shared Python query service for CLI and UI. `taskman browse` accepts `--where FIELD=VALUE`, `--contains FIELD=TEXT`, `--exists FIELD`, `--missing FIELD`, `--keyword`, `--epic`, `--project`, `--sort` and `--descending`. Exact matches support comma-separated alternatives; nested structures use contains. JSON rows mark contextual ancestors explicitly.

## Workspace server

`taskman ui` serves the page at `/` and at an item path such as `/TM-7`. The page reads its state from the query string. Every API error is JSON with an `error` text. Mutations need the `X-Taskman: 1` header and a local `Host`.

- `POST /api/query` returns the projects with open counts, the item count of each view, facet counts, and the rows of one view. A row has no description, journal or internal fields. It has derived signals: state, blockers, leaf progress, latest verification, attention reason and last update. The response has an `etag`. When the request sends the same `etag`, the response is only `{etag, unchanged: true}`.
- The views are Attention, Active, Ready, Blocked, Drafts, Backlog and Done. Attention holds open items with a failed or blocked current verification, a handoff as the latest comment, or all close gates passed. Ready holds backlog items that can start. Blocked holds finalized open items that are not ready.
- `GET /api/show/KEY` adds ancestors, children, dependencies, dependents, the close gates and whether the item can be discarded, with the reason when it cannot. `taskman close` uses the same close gates.
- `POST /api/edit` with `base_title` and `base_body` returns 409 with the current text when another actor changed the title or description after the editor opened. Comments, assignee and label changes do not cause a conflict.
- `POST /api/fields` sets the assignee and the labels in one transaction.
- `POST /api/reorder` moves an item among its siblings. The top-level items of each project have their own order.
