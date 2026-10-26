# Install and run

Copy this whole project folder to the Pi. The older one-file scripts in your home directory are not enough on their own.

```bash
mkdir -p /home/jadorante/jarvis-system
```

From the laptop, in PowerShell:

```powershell
scp -r "C:\Users\Dell AI User\Downloads\raspberry_pi_test\*" jadorante@10.0.0.27:~/jarvis-system/
```

On the Pi:

```bash
cd ~/jarvis-system
python3 -m jarvis --check
python3 -m jarvis --text "open YouTube" --no-speak
python3 -m jarvis
```

Stop any old `python3 jarvis_listen.py` first. Both programs use the microphone.

The TV has to be on the Pi's HDMI input before you can see the hologram or hear Jarvis. Roku apps use a different input, so the picture and the voice do not play over Netflix.

## Start on boot

```bash
sudo cp ~/jarvis-system/deploy/jarvis.service /etc/systemd/system/jarvis.service
sudo systemctl daemon-reload
sudo systemctl enable --now jarvis.service
sudo systemctl status jarvis.service
```

Logs are in `~/jarvis/logs/jarvis.log` and in `journalctl -u jarvis.service`.

## Memory

The database is `~/jarvis/jarvis.sqlite`. On first start, lines already in `~/jarvis/user.txt` and `~/jarvis/existence.txt` are copied in. The originals stay, and a `.bak` copy is made once.

```bash
python3 -m jarvis --backup
```

Backups land in `~/jarvis/backups/`.

## Useful commands

```bash
python3 -m jarvis --text "remember that I prefer science fiction movies" --no-speak
python3 -m jarvis --text "what kinds of movies do I like" --no-speak
python3 -m jarvis --discover
python3 jarvis_face.py
python3 clapper.py --roku 10.0.0.43
```

Change the TV address in `config/jarvis.json` if the Roku is no longer at `10.0.0.43`.

## Tests on the laptop

```powershell
python -m unittest discover -s tests
```

Those tests use stand-ins for the TV and Wikipedia. They do not measure the Pi.
