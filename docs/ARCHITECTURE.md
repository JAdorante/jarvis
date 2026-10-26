# Jarvis architecture

Jarvis is one Python process. The pieces below are modules in that process, not separate networked services. A background thread runs reminders. The hologram is a small web server on port 8765.

```
microphone -> wake check -> planner -> permission check -> tools -> speech and hologram
                                 |                              |
                                 +-------- memory database -----+
```

## What each part does

| Module | Role |
|---|---|
| `jarvis/runtime.py` | Starts the face, the reminder thread, and the microphone loop. After a restart it marks unfinished tasks failed and does not run them again. |
| `jarvis/agent/planning.py` | Turns one sentence into a short list of tool steps. "Open YouTube" never asks the model. |
| `jarvis/agent/orchestrator.py` | Runs those steps, retries a failed TV call once, and stops when you cancel or a step cannot be confirmed. |
| `jarvis/agent/permissions.py` | Blocks prohibited tools. TV control is allowed only while standing authorization is on. |
| `jarvis/tools/` | The only actions that can run. There is no shell tool. |
| `jarvis/devices/roku.py` | Roku External Control Protocol: power, launch, active app, device info. |
| `jarvis/memory/store.py` | SQLite file `~/jarvis/jarvis.sqlite` for facts, tasks, reminders, routines, and the audit log. |
| `jarvis/model/gateway.py` | Local Ollama. A remote model stays off until `remote_inference.enabled` is set. |
| `jarvis/audio/` | Microphone, Whisper, Piper or espeak, and a lock so capture and playback do not use the sound device together. |
| `jarvis/visual/` | Gold hologram. The page receives state over `/events` and also polls `/state`. |
| `jarvis/scheduler.py` | Due reminders and daily routines. Spoken reminders wait until quiet hours end. A TV-off time you set still runs. |

## Adding a tool

1. Write a function that accepts `(arguments, context)` and returns a `ToolResult`.
2. Register it with a name, a permission level (`L0` through `L3`), and the required argument names.
3. Teach `plan_request` when that tool should be chosen. The model is not allowed to invent a tool name at runtime.

`L0` runs immediately. `L1` runs when standing authorization allows it. `L2` stops for approval. `L3` never runs. Web pages and tool output are data. They are not read as new instructions.

## Configuration

`config/jarvis.json` holds the Roku address, model name, file paths, follow-up window, step limit, and quiet hours. `JARVIS_ROKU_HOST` overrides the TV address for one run.
