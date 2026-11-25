# Workspaces and data

A workspace is the only directory Freenerf may read or modify. It is selected
on first launch, stored in the global config, and can be changed from
**Settings → Workspace** or `Ctrl+G`.

## Layout

```
<workspace>/.freenerf/
├── sessions.db      # conversations, message metadata
├── tasks.db         # tasks, steps, logs
├── images/          # generated images + history.json
├── backups/         # copies of files before edit/delete
├── logs/            # reserved for diagnostics
└── workspace.json   # name, root, creation time
```

Set `FREENERF_DATA_DIR` to keep this data elsewhere, for example on a different
partition or outside a read-only project tree. Relative values resolve against
the workspace root.

## Isolation guarantees

- Sessions from one workspace never appear in another.
- Tasks, step results and logs are scoped by workspace and session.
- Generated images and backups stay in the workspace that produced them.
- Global settings (provider, API keys, theme) are intentionally shared and live
  in `~/.config/freenerf/config.json`.
- Switching workspace closes the previous agents and providers, then opens the
  new databases and rebuilds tools with the new root.

## Switching sequence

1. Validate the directory: exists (or create), is a directory, is writable.
2. Ensure its `.freenerf/` layout.
3. Close current agents and provider clients.
4. Open `sessions.db` and `tasks.db` for the new workspace.
5. Build a new agent, filesystem and terminal rooted there.
6. Refresh tabs, chat view, task panel and status bar.
7. Persist the new path and add it to recent workspaces.

The previous workspace's data is untouched. Unfinished tasks are **not** run or
copied. Reopening a session offers to continue an unfinished task and waits for
explicit approval.

## Legacy data

Older versions stored sessions and tasks globally under the platform data
directory (`~/.local/share/freenerf/` on Linux). On first use, Freenerf copies
those databases into the first workspace it opens and writes a migration marker
next to the originals:

```
<legacy-data-dir>/.migrated-to-workspace
```

After that no further copies are made, so a second workspace starts empty. You
can copy a database manually if you want to import it somewhere else.

## Backups

Before overwriting, editing or deleting a file, the previous content is copied
to:

```
.freenerf/backups/<UTC timestamp>/<relative path>
```

Backups are excluded from project searches. There is no automatic pruning yet;
remove old timestamp folders when they are no longer useful.

## Termux notes

- Run `termux-setup-storage` before using `~/storage/shared` as a workspace.
- Android shared storage can be slow; prefer a project directory in Termux's
  home for large repositories.
- The `.freenerf/` directory may appear in file managers. Add `.freenerf/` to
  your project's `.gitignore` if you do not want to commit workspace data.
- The app cannot change the Termux font; use pinch-to-zoom.

## Safety

- Path resolution rejects anything outside the workspace, including `..`
  escapes and absolute paths.
- `delete_file` refuses non-empty directories and can never delete the root.
- `rm` commands that target roots, home directories, `.git` or `.freenerf` are
  blocked in every permission mode.
- Shell commands still run with your user's permissions; use `NORMAL` mode and
  review approvals when working with untrusted code.
