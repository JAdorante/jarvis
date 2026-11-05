# Known limits

This version is the foundation from the specification: a persistent memory database, a bounded tool loop, Roku checks, reminders, and the existing hologram. It is not the whole specification.

## Measured on this machine

Nothing in section 14 of the specification has been timed on the Raspberry Pi yet. The wake word is still a check after Whisper finishes, not a separate always-on detector. No model comparison has been run. Treat the latency numbers in the specification as targets, not as results.

## Television

Roku's external control can launch an app, send keys, and report the active app and power state. It cannot see a Netflix profile row, a season list, or the episode that is playing inside an app. "Resume The Sopranos" opens HBO Max and says that the episode cannot be chosen. A launch is successful only when the TV's active-app query returns that app. Jarvis keeps asking for about six seconds without sending the command again. If the TV never confirms, Jarvis says so.

"Off" is confirmed when the TV reports any power mode other than `PowerOn`. The exact words a TV uses have not been measured on this one.

The hologram and Jarvis's voice use the Pi's HDMI input. They are not visible or audible while the TV is showing the Roku home or a streaming app. Showing both at once would need another display or a capture device. Capturing protected video is out of scope.

With `roku.pi_input` set, a reminder waits until the TV is back on the Pi, however long that takes. A spoken routine waits only about ten minutes, then is skipped for the day. With `roku.pi_input` empty, which is the default, Jarvis cannot tell and may speak to an input nobody is watching.

The hologram page is served only to the Pi itself. To open it from another computer, set `face.host` to `0.0.0.0`. Anyone on your network could then see the start of each sentence you say to Jarvis.

The Roku address is `10.0.0.43` until `config/jarvis.json` changes. `python3 -m jarvis --discover` can look for a Roku on the local network.

## Voice

Jarvis and the double-clap program both need the USB microphone. Run one of them, not both. The microphone is not listened to while Jarvis is speaking, because the TV speakers would be heard as a new command. Sound in the first moment after a reminder ends is also thrown away. Echo cancellation has not been measured with the Rode microphone.

You cannot interrupt Jarvis. It does not listen while it works on a request or speaks, so "stop" only reaches it afterwards, where it closes an open question or approval. A request that is already running finishes, up to the limit in `agent.max_seconds`.

A follow-up such as "what about Friday?" is accepted for 20 seconds after Jarvis finishes speaking, without saying "Jarvis" again. That window is `audio.followup_seconds`. An open question or approval is dropped after `agent.pending_seconds`.

Jarvis does not recognize voices. Anyone in the room can answer yes to an approval or ask about saved facts and Sparrow items. Everything it says goes through the TV speakers.

## Model

Ordinary conversation uses `qwen3:4b` on the Pi. A 7 or 8 billion parameter model does not leave enough of the 8 GB of memory for the microphone and the system. The model does not choose remote buttons, shell commands, or unlisted tools. Multi-step requests are planned by the rules in `jarvis/agent/planning.py`, then checked against tool results.

Remote inference is off. Saved personal facts are not sent to a remote model unless `allow_private_memory` is turned on.

## Not built yet

- A dedicated wake-word engine, and a measurement of its CPU use.
- Hearing you while Jarvis is talking or working, including cancelling a running request.
- Browsing Netflix, Max, or YouTube for a title.
- Calendar, email, or any account login. An email request is refused. Nothing is drafted or sent.
- Browser automation.
- Seeing the Roku screen.
- Episode-level playback.
- A benchmark report from the Pi.

Weather comes from Open-Meteo and needs a city. It covers today and the next six days. A forecast or a Wikipedia lookup sends the words of that question over the internet. A question about you ("what's my sister's name") stays on the Pi and is answered only from saved facts.

## Sparrow

Sparrow is off by default and has not yet been run against `sparrow.ravenry.us`. With `share_transcripts` on, only sentences that contain the wake word are sent. Facts you ask Jarvis to remember, anything with `remember`, `forget`, or a word like `password`, and yes-or-no replies stay on the Pi. That filter is a word list and can miss a secret said another way.

Sparrow labels each item with a `disclosure_action`. Jarvis reads aloud `offer`, `allow`, `disclose`, or no label, and holds back anything else. Only `offer` has been seen so far, so the full list of values still needs checking against Sparrow.

## Storage

The database uses SQLite WAL mode. That is safer across a power cut than a single plain file, and it is still an SD card. Keep backups with `python3 -m jarvis --backup`. Audio is not kept after a sentence is transcribed.

Forgetting a fact erases it from the database, the conversation and audit history, and `user.txt`, `existence.txt`, and their `.bak` copies. It stays out if the same line is imported again. Two places still hold it. Backups made earlier are not changed, so delete old files in `~/jarvis/backups/` if that matters. The service also prints each sentence addressed to it and each reply, and systemd keeps that in `journalctl -u jarvis.service` until it rotates out. Speech that was not addressed to Jarvis is not printed.
