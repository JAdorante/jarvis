# Hardware checks

Run these on the Pi after the project folder is copied to `~/jarvis-system`. The laptop tests do not replace them.

The TV starts on the Pi HDMI input. The USB microphone is plugged into a black USB port. The Roku is on the same network.

1. `python3 -m jarvis --check` prints the Roku address `10.0.0.43`, the model name `jarvis`, and remote inference `false`.
2. `python3 -m jarvis --text "remember that I prefer science fiction movies" --no-speak` answers that it will remember. Stop the process, start it again, and ask what kinds of movies you like. The answer includes science fiction.
3. `python3 -m jarvis --text "open YouTube"` makes the TV show YouTube, and the spoken line says the TV reports that app. Unplug the TV's network or use a wrong address and confirm Jarvis says it could not reach the TV.
4. Say "Jarvis, who directed Interstellar and what other movies did they direct?" The reply names the director from the lookup and does not claim it pressed remote buttons.
5. Say "Jarvis, remind me in two minutes to check the oven." Wait. Jarvis speaks the reminder once.
6. While a reply is playing, the next sentence is taken after the reply ends. This build does not interrupt itself mid-sentence.
7. `python3 jarvis_face.py` on the Pi HDMI input shows the gold hologram. Saying a command, if the listener is running instead, moves it through listening, thinking, acting, and speaking.
8. `python3 clapper.py --roku 10.0.0.43` still toggles power with two claps. Stop it before starting `python3 -m jarvis`, because they share the microphone.
9. Reboot. `systemctl status jarvis` is active if the service was enabled. A reminder that was still pending remains pending. A task that was mid-run is not launched again.
