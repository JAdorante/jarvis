#!/usr/bin/env python3
"""Turn a Roku TV on and off with a double clap.

Plug a USB microphone into the Pi. Two claps turn the TV on if it is off,
and off if it is on. The microphone stays powered, so the next pair of claps
still works.
"""

import argparse
import array
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.05
BLOCK_BYTES = int(SAMPLE_RATE * BLOCK_SECONDS) * 2
QUIET_SECONDS = 2
CLAP_GAP_MIN = 0.12
CLAP_GAP_MAX = 0.55
LEVEL_FLOOR = 0.02


def uhubctl(*args):
    result = subprocess.run(
        ["sudo", "-n", "uhubctl", *args],
        text=True,
        capture_output=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        sys.stderr.write(output)
        raise SystemExit(
            "uhubctl could not run. Install it and allow it to run without a password."
        )
    return output


def hub_locations():
    locations = []
    for line in uhubctl().splitlines():
        if line.startswith("Current status for hub "):
            locations.append(line.split()[4])
    if not locations:
        raise SystemExit("uhubctl did not find any USB hubs.")
    return locations


def set_usb_power(on):
    action = "on" if on else "off"
    for location in hub_locations():
        uhubctl("-l", location, "-a", action)


def capture_devices():
    result = subprocess.run(["arecord", "-l"], text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("arecord is missing. Install it with: sudo apt install -y alsa-utils")
    devices = []
    for line in result.stdout.splitlines():
        match = re.match(r"card (\d+): .*\[([^\]]+)\].*device (\d+):", line)
        if match:
            devices.append((f"plughw:{match.group(1)},{match.group(3)}", match.group(2)))
    return devices


def find_microphone():
    """Return (arecord device, name) for a USB microphone, if one is plugged in."""
    devices = capture_devices()
    for device, name in devices:
        lowered = name.lower()
        if "usb" in lowered or "mic" in lowered:
            return device, name
    if devices:
        return devices[0]
    return None


def wait_for_microphone(timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = find_microphone()
        if found is not None:
            return found
        time.sleep(0.5)
    raise SystemExit(
        "No microphone found. Plug it into a USB port on the Pi and run this again."
    )


class Recorder:
    def __init__(self, device):
        self.proc = subprocess.Popen(
            [
                "arecord",
                "-q",
                "-D",
                device,
                "-f",
                "S16_LE",
                "-r",
                str(SAMPLE_RATE),
                "-c",
                "1",
                "-t",
                "raw",
            ],
            stdout=subprocess.PIPE,
        )

    def read_level(self):
        data = self.proc.stdout.read(BLOCK_BYTES)
        if len(data) < BLOCK_BYTES:
            return 0.0
        samples = array.array("h")
        samples.frombytes(data)
        return max(abs(sample) for sample in samples) / 32768

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def calibrate(device, sensitivity):
    blocks = int(QUIET_SECONDS / BLOCK_SECONDS)
    print(f"Stay quiet for {QUIET_SECONDS:.0f} seconds. Measuring the room.")
    recorder = Recorder(device)
    try:
        peaks = [recorder.read_level() for _ in range(blocks)]
    finally:
        recorder.close()
    typical = sorted(peaks)[int(len(peaks) * 0.9)]
    threshold = max(typical * 6, LEVEL_FLOOR) / sensitivity
    print(f"Room level is {typical:.3f}. A clap needs to reach {threshold:.3f}.")
    return threshold


def wait_for_double_clap(device, threshold):
    last_clap = None
    last_report = 0.0
    print("Clap twice, close together.")
    recorder = Recorder(device)
    try:
        while True:
            level = recorder.read_level()
            now = time.monotonic()
            if now - last_report >= 1:
                print(f"  listening  level {level:.3f}  need {threshold:.3f}")
                last_report = now
            if level < threshold:
                continue
            if last_clap is not None and now - last_clap < CLAP_GAP_MIN:
                continue
            if last_clap is not None and now - last_clap <= CLAP_GAP_MAX:
                print("Two claps.")
                return
            print("One clap. Clap again.")
            last_clap = now
    finally:
        recorder.close()


def roku_power_mode(ip):
    url = f"http://{ip}:8060/query/device-info"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            text = response.read().decode("utf-8", "replace")
    except (OSError, urllib.error.URLError) as exc:
        print(f"Could not reach the TV: {exc}")
        return None
    match = re.search(r"<power-mode>([^<]+)</power-mode>", text)
    return match.group(1) if match else None


def roku_key(ip, key):
    url = f"http://{ip}:8060/keypress/{key}"
    request = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=3) as response:
        response.read()


def toggle_roku(ip):
    """Turn the TV off when it is on, and on otherwise."""
    from jarvis.devices.roku import RokuClient, RokuError

    client = RokuClient(ip)
    try:
        info = client.device_info()
    except RokuError as exc:
        print(f"Could not reach the TV: {exc}")
        return
    turning_on = info.get("power_mode") != "PowerOn"
    try:
        result = client.power(turning_on)
    except RokuError as exc:
        print(f"The TV did not accept the command: {exc}")
        return
    if result.get("verified"):
        print("TV on." if turning_on else "TV off.")
    else:
        print("Command sent. The TV did not confirm the new power state.")


def test_light():
    print("Turning every USB socket off for 3 seconds. The light should go dark.")
    set_usb_power(False)
    time.sleep(3)
    set_usb_power(True)
    print("USB power is back. The light should be on.")


def main():
    parser = argparse.ArgumentParser(description="Double-clap to turn a Roku TV on and off")
    parser.add_argument(
        "--roku",
        metavar="IP",
        help="Roku TV address, for example 10.0.0.43",
    )
    parser.add_argument(
        "--test-light",
        action="store_true",
        help="turn the USB light off for 3 seconds, then on again",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=8,
        help="how long the USB light stays off after two claps (default 8)",
    )
    parser.add_argument(
        "--sensitivity",
        type=float,
        default=1,
        help="higher hears softer claps, lower ignores more noise (default 1)",
    )
    args = parser.parse_args()
    if args.sensitivity <= 0:
        raise SystemExit("Sensitivity has to be greater than 0.")

    try:
        if args.test_light:
            test_light()
            return

        if args.roku:
            device, device_name = wait_for_microphone()
            print(f"Microphone: {device_name}")
            threshold = calibrate(device, args.sensitivity)
            while True:
                wait_for_double_clap(device, threshold)
                toggle_roku(args.roku)
                time.sleep(1)
            return

        while True:
            device, device_name = wait_for_microphone()
            print(f"Microphone: {device_name}")
            threshold = calibrate(device, args.sensitivity)
            wait_for_double_clap(device, threshold)
            print(f"Light off for {args.seconds:.0f} seconds.")
            set_usb_power(False)
            time.sleep(args.seconds)
            set_usb_power(True)
            print("Light on. Waiting for the microphone to return.")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if not args.roku:
            try:
                set_usb_power(True)
            except SystemExit:
                pass


if __name__ == "__main__":
    main()
