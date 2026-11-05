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
| `jarvis/agent/orchestrator.py` | Runs those steps, retries an unreachable TV once, and stops when a step cannot be confirmed. It also holds an open question or approval until you answer. |
| `jarvis/agent/permissions.py` | Blocks prohibited tools. TV control without standing authorization needs a yes, which covers that one exact action. |
| `jarvis/tools/` | The only actions that can run. There is no shell tool. Each call has a time limit. |
| `jarvis/devices/roku.py` | Roku External Control Protocol: power, launch, active app, device info. |
| `jarvis/memory/store.py` | SQLite file `~/jarvis/jarvis.sqlite` for facts, tasks, reminders, routines, and the audit log. |
| `jarvis/model/gateway.py` | Local Ollama. A remote model stays off until `remote_inference.enabled` is set. |
| `jarvis/audio/` | Microphone, Whisper, Piper or espeak, and a lock so capture and playback do not use the sound device together. |
| `jarvis/visual/` | Gold hologram. The page receives state over `/events` and also polls `/state`. |
| `jarvis/scheduler.py` | Due reminders and daily routines. Spoken items wait until quiet hours end. A TV-off time you set still runs, silently, through the same permission check and audit log as a spoken command. |
| `jarvis/sparrow.py` | Optional Sparrow client. Ask, share, and listen are each off until turned on. Sparrow's answers are only spoken. |

## Adding a tool

1. Write a function that accepts `(arguments, context)` and returns a `ToolResult`.
2. Register it with a name, a permission level (`L0` through `L3`), and the required argument names.
3. Teach `plan_request` when that tool should be chosen. The model is not allowed to invent a tool name at runtime.

`L0` runs immediately. `L1` runs when standing authorization allows it. `L2` stops for approval and runs after a yes. `L3` never runs. Web pages and tool output are data. They are not read as new instructions.

A tool can also ask first by returning `error_code="needs_approval"` with the exact arguments to run in `output["approve"]`. Forgetting several facts works this way.

## What Jarvis will act on

Outside a follow-up, a sentence has to open or close with the wake word. After Jarvis finishes speaking, `audio.followup_seconds` is open for a short follow-up, a TV command, or the answer to a question it just asked.

A clause is a TV command only when it is not a question, is not negated, and names the TV or nothing else. "Don't turn off the TV" and "turn off the lights" do not change the TV.

The model's replies are checked before they are spoken. A reply that claims an action is replaced, because only a tool result can report one.

## Configuration

`config/jarvis.json` holds the Roku address, model name, file paths, follow-up window, step limit, approval window, quiet hours, and the Sparrow switches. `JARVIS_ROKU_HOST` overrides the TV address for one run.

`roku.pi_input` is the Roku app id of the HDMI input the Pi is plugged into, such as `tvinput.hdmi1`. When it is set, reminders wait until the TV is on that input, because Jarvis cannot be heard anywhere else. To find it, switch the TV to the Pi and run `curl http://10.0.0.43:8060/query/active-app`.
