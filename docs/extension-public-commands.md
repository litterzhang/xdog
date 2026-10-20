# Public slash commands from extensions

An extension can expose a user-facing command without exposing its internal
extension ID. This does not register a model tool or change extension trust.

```python
api.command('inspect', 'Read current state', callback,
            public_name='show-state', complete=complete_arguments)
```

Users type `/show-state`. The worker callback is still registered as `inspect`
and belongs to the actual configured extension client. Existing commands without
a public name retain `/ext-<extension>-<command>`. Fully qualified command IDs
remain accepted for internal routing/debugging; they are not extra public menu
entries when a public name is declared. Rich result attribution and keybinding
identities remain fully qualified, not derived from the public spelling.

## One catalog

Command discovery, execution, help, argument completion and shortcuts use the
same live declaration. A public name is lowercase ASCII, starts with a letter,
is at most32 characters and otherwise uses letters, digits, `_` or `-`. Slash,
whitespace, empty names and the internal `ext-` prefix are not accepted.

`public_name` is optional data in a command snapshot. Host snapshots include
`command_owners` when public names exist. Readers validate the declared active
extension and reject attempts to reassign a qualified name to another extension.
Actual callback/client/session binding still enforces authority; metadata labels
alone never grant a host operation or model turn.

Replacing/unregistering commands updates their live revision and completion IDs.
Old public names disappear from discovery; stale argument completions cannot
invoke the renamed callback. Reload/unload uses the existing worker lifecycle.

For shortcuts:

```python
api.shortcut('inspect', 'f6', 'Read current state', callback,
             public_name='show-state')
```

The shortcut displays/runs `/show-state`, but an explicit user keybinding such as
`ext-example-inspect: alt+x` still binds that original extension+command identity.
It cannot override existing editor/approval/exit keys. Command availability or
completion never creates a worker or invokes the business callback by itself.

## Collisions and host integration

Hosts reserve their builtin commands and installed skill names. Discovery of
those filesystem skill names runs in owned preparation IO. Extension public
names may not shadow these names or another registered extension command. Initial
or live conflicting declarations fail explicitly with diagnostics, never overwrite
an existing command or become an implicit skill/model prompt. If a new skill is
added after activation, only the conflicting command is quarantined: help, status,
unload and the rest of the catalog remain usable. Invoking the conflicting name
reports the conflict instead of silently switching to the new skill. Remove the
conflicting configuration or explicitly unload the extension to resolve it. Both Coding and Claw route
public names through this generic catalog; neither hardcodes Goal command logic.

Terminal clients and hosts must understand public command snapshots to offer the
new names; old qualified-only snapshots remain valid. Host snapshot validation
rejects a public command without explicit active-owner attribution.

## Goal display

The packaged display-only `goal_ui` declares:

```text
/goals                 list Goals in the current authorized view
/goals GOAL_ID         filter to a specific Goal
/goals page OFFSET     read from the specified offset (not a page number)
```

`/goal` remains the packaged skill name, not the display command. `/goals` is
available when `goal_ui` is enabled and its worker registered; package installation
or skill discovery does not start a display worker. The command reads the existing
owner-bound view and refreshes status, never starts a model or changes Goal/task/
review/notification state. Goal's model-facing tools are unchanged.
