# Hardware checks

Run these on the Pi after the project folder is copied to `~/jarvis-system`. The laptop tests do not replace them.

The TV starts on the Pi HDMI input. The USB microphone is plugged into a black USB port. The Roku is on the same network.

1. `python3 -m jarvis --check` prints the Roku address `10.0.0.43`, the model name `qwen3:4b`, and remote inference `false`.
2. `python3 -m jarvis --text "remember that I prefer science fiction movies" --no-speak` answers that it will remember. Stop the process, start it again, and ask what kinds of movies you like. The answer includes science fiction.
3. `python3 -m jarvis --text "open YouTube"` makes the TV show YouTube, and the spoken line says the TV reports that app. Unplug the TV's network or use a wrong address and confirm Jarvis says it could not reach the TV.
   Then run `python3 -m jarvis --text "turn off the TV" --no-speak`. The TV goes off and the reply says `The TV is off.` With the TV off, `curl http://10.0.0.43:8060/query/device-info` shows which `power-mode` this TV uses.
4. Say "Jarvis, who directed Interstellar and what other movies did they direct?" The reply names the director from the lookup and does not claim it pressed remote buttons.
5. Say "Jarvis, remind me in two minutes to check the oven." Then stay silent. Jarvis speaks the reminder once, on time, without anyone making a sound first. Say something during the reminder and confirm it is not taken as a command.
6. While a reply is playing, the next sentence is taken after the reply ends. This build does not interrupt itself mid-sentence.
7. `python3 jarvis_face.py` on the Pi HDMI input shows the gold hologram. Saying a command, if the listener is running instead, moves it through listening, thinking, acting, and speaking.
8. `python3 clapper.py --roku 10.0.0.43` still toggles power with two claps. Stop it before starting `python3 -m jarvis`, because they share the microphone.
9. Reboot. `systemctl status jarvis` is active if the service was enabled. A reminder that was still pending remains pending. A task that was mid-run is not launched again.
10. Play a show on the TV with the volume up, and say "turn off the TV" without "Jarvis". Nothing happens. Say "Jarvis, what's the weather", then "Boston" without "Jarvis". Jarvis answers for Boston.
11. Set `permissions.roku_standing_authorization` to `false`. Say "Jarvis, open YouTube", then "yes". YouTube opens only after the yes. Set it back.
12. Run `python3 -m jarvis --backup`, then `sqlite3 <that file> "select count(*) from memories"`. The count matches what you have saved, including a fact saved just before the backup.
13. Say "Jarvis, remember that the test code is 4321", restart the service, say "Jarvis, forget the test code", and restart again. "Jarvis, what's my test code?" finds nothing, and `grep 4321 ~/jarvis/user.txt` finds nothing.

## Sparrow

Do these once against a real container. They have not been run yet. Turn on `sparrow.enabled`, and for checks 5 and 6 also `share_transcripts` and `poll_outbox`.

1. `python3 -m jarvis --sparrow-check` lists five tools and shows both tokens present.
2. Say "Jarvis, what's on my plate?" The reply names items visible in Sparrow's open loops.
3. Say "Jarvis, brief me on" and a company in Sparrow. The reply names that company.
4. Stop the container. Ask again. Jarvis says it could not reach Sparrow, and "Jarvis, open YouTube" still works.
5. Say "Jarvis, remind Hugh about the deck tomorrow." The sentence appears in Sparrow as `external:jarvis-pi`. Speech without "Jarvis" does not appear, and neither does "Jarvis, remember that the test code is 4321".
6. Queue an outbox item in Sparrow. Jarvis speaks it within `poll_seconds`, once. Queue one during quiet hours. It is spoken when they end.
7. Compare what `disclosure_action` values Sparrow can send against the list in `SPEAKABLE_DISCLOSURE` in `jarvis/sparrow.py`.
