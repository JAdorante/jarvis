# Known limits

This version is the foundation from the specification: a persistent memory database, a bounded tool loop, Roku checks, reminders, and the existing hologram. It is not the whole specification.

## Measured on this machine

Nothing in section 14 of the specification has been timed on the Raspberry Pi yet. The wake word is still a check after Whisper finishes, not a separate always-on detector. No model comparison has been run. Treat the latency numbers in the specification as targets, not as results.

## Television

Roku's external control can launch an app, send keys, and report the active app and power state. It cannot see a Netflix profile row, a season list, or the episode that is playing inside an app. "Resume The Sopranos" opens HBO Max and says that the episode cannot be chosen. A launch is successful only when the TV's active-app query returns that app. If the query fails, Jarvis says the command was not confirmed.

The hologram and Jarvis's voice use the Pi's HDMI input. They are not visible or audible while the TV is showing the Roku home or a streaming app. Showing both at once would need another display or a capture device. Capturing protected video is out of scope.

The Roku address is `10.0.0.43` until `config/jarvis.json` changes. `python3 -m jarvis --discover` can look for a Roku on the local network.

## Voice

Jarvis and the double-clap program both need the USB microphone. Run one of them, not both. Playback can be stopped in code, but the microphone is not listened to while Jarvis is speaking, because the TV speakers would be heard as a new command. Echo cancellation has not been measured with the Rode microphone.

A follow-up such as "what about Friday?" is accepted for 20 seconds after an answer, without saying "Jarvis" again. That window is `audio.followup_seconds`.

## Model

Ordinary conversation uses `qwen3:4b` on the Pi. A 7 or 8 billion parameter model does not leave enough of the 8 GB of memory for the microphone and the system. The model does not choose remote buttons, shell commands, or unlisted tools. Multi-step requests are planned by the rules in `jarvis/agent/planning.py`, then checked against tool results.

Remote inference is off. Saved personal facts are not sent to a remote model unless `allow_private_memory` is turned on.

## Not built yet

- A dedicated wake-word engine, and a measurement of its CPU use.
- Hearing you while Jarvis is talking.
- Browsing Netflix, Max, or YouTube for a title.
- Weather, calendar, email, or any account login. An email request is drafted and then refused. Nothing is sent.
- Browser automation.
- Seeing the Roku screen.
- Episode-level playback.
- A benchmark report from the Pi.

Morning weather is stored as an 8:00 reminder that says no forecast source is configured. It will not invent a forecast.

## Storage

The database uses SQLite WAL mode. That is safer across a power cut than a single plain file, and it is still an SD card. Keep backups with `python3 -m jarvis --backup`. Audio is not kept after a sentence is transcribed.
