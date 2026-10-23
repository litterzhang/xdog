---
title: Reference
---

## Installation and management

```bash
pip install xdog-eyes
xdog-eyes --help
xdog-eyes daemon start
xdog-eyes daemon status
```

| CLI | Meaning |
|---|---|
| `validate FILE` | Validate saved v2 JSON; no observation/model is created. |
| `--owner OWNER start FILE` | Start/reuse the daemon and register the definition. |
| `--owner OWNER list` | List this owner's registrations. |
| `--owner OWNER status ID` | Observation/handler facts, not Agent completion. |
| `--owner OWNER pause ID` | Reversibly pause collection. |
| `--owner OWNER resume ID` | Explicitly resume under existing definition/recovery fences. |
| `--owner OWNER cancel ID` | Permanently cancel this registration, not downstream work. |
| `daemon stop` | Explicitly stop the daemon, not an action performed by host-binding closure. |
| `--directory DIR ...` | Use an explicit private daemon isolation directory. |

The daemon defaults to `~/.local/share/xdog/eyes` and honors `XDG_DATA_HOME`.
Changing owner selects a management namespace, not a new delivery permission.

## Model tool

The installed capability exposes `eyes`, using the CLI rather than a Python
management client. Registering/opening the binding creates no observation.

```json
{"action":"create","owner":"repo-review","definition_path":"/absolute/path/to/eyes.json"}
```

```json
{"action":"status","owner":"repo-review","observer_id":"EXACT_RETURNED_REGISTRATION_ID"}
```

Every action needs owner. `create` also needs definition_path;
status/pause/resume/cancel need observer_id. Omit unrelated fields. The returned
exit_code/stdout/stderr describe the actual CLI call; nonzero exit is not success.
Failure or cancellation does not prove registration had no durable effect.
Normal host tool policy still applies.

## Definition and source limits

| Part | Contract |
|---|---|
| Definition | Version 2 JSON: name, sources, detectors, handlers and routes. |
| File source | Bounded regular-file polling and explicit include/exclude selection. |
| Scheduler | One-off zoned `at`, monotonic `every_seconds`, or five-field `cron` with IANA timezone. |
| ACP destination | Explicit Unix/optional stdio connection and existing session/group address. |
| Raw events | Explicit event reads/ACK; a consumer commits its own effect before acknowledging. |

There is no `cancel_notifications`, project-management workspace option or
supported public EyesClient/embedded ObserverService. See the
[package guide](https://github.com/litterzhang/xdog/blob/main/packages/eyes/README.md)
for complete schemas, examples and recovery limits.
