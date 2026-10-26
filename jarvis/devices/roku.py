"""Roku External Control Protocol adapter."""

import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class RokuError(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class UrllibTransport:
    def __init__(self, host, port=8060, timeout=8):
        self.base = f"http://{host}:{port}"
        self.timeout = timeout

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=self.timeout) as response:
                return response.read().decode("utf-8", "replace")
        except (OSError, urllib.error.URLError) as exc:
            raise RokuError(str(exc), retryable=True) from exc

    def post(self, path):
        request = urllib.request.Request(self.base + path, data=b"", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", "replace")
        except (OSError, urllib.error.URLError) as exc:
            raise RokuError(str(exc), retryable=True) from exc


def parse_active_app(xml_text):
    root = ET.fromstring(xml_text)
    app = root.find("app")
    if app is None:
        return {"id": "", "name": ""}
    return {"id": app.attrib.get("id", ""), "name": (app.text or "").strip()}


def parse_power_mode(xml_text):
    root = ET.fromstring(xml_text)
    node = root.find("power-mode")
    return node.text.strip() if node is not None and node.text else ""


def parse_apps(xml_text):
    root = ET.fromstring(xml_text)
    apps = []
    for app in root.findall("app"):
        apps.append({"id": app.attrib.get("id", ""), "name": (app.text or "").strip()})
    return apps


def location_host(packet):
    match = re.search(r"LOCATION:\s*http://([0-9.]+):\d+", packet, re.I)
    return match.group(1) if match else None


def discover_roku(timeout=2):
    """Ask the local network for a Roku ECP device. Returns an IP, or None."""
    message = "\r\n".join(
        [
            "M-SEARCH * HTTP/1.1",
            "HOST: 239.255.255.250:1900",
            'MAN: "ssdp:discover"',
            "ST: roku:ecp",
            "MX: 1",
            "",
            "",
        ]
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(message.encode("utf-8"), ("239.255.255.250", 1900))
        while True:
            try:
                data, _addr = sock.recvfrom(2048)
            except socket.timeout:
                return None
            host = location_host(data.decode("utf-8", "replace"))
            if host:
                return host
    except OSError:
        return None
    finally:
        sock.close()


class RokuClient:
    def __init__(self, host, port=8060, timeout=8, transport=None):
        self.host = host
        self.port = port
        self.transport = transport or UrllibTransport(host, port, timeout)

    def device_info(self):
        return {"power_mode": parse_power_mode(self.transport.get("/query/device-info"))}

    def active_app(self):
        return parse_active_app(self.transport.get("/query/active-app"))

    def installed_apps(self):
        return parse_apps(self.transport.get("/query/apps"))

    def power(self, on):
        key = "PowerOn" if on else "PowerOff"
        self.transport.post("/keypress/" + key)
        try:
            mode = self.device_info().get("power_mode", "")
        except RokuError:
            return {"submitted": True, "verified": False, "power_mode": "", "key": key}
        expected = "PowerOn" if on else "PowerOff"
        return {
            "submitted": True,
            "verified": mode == expected,
            "power_mode": mode,
            "key": key,
        }

    def launch(self, app_id):
        self.transport.post("/launch/" + urllib.parse.quote(str(app_id), safe=""))
        try:
            active = self.active_app()
        except RokuError:
            return {"submitted": True, "verified": False, "active_id": "", "active_name": ""}
        return {
            "submitted": True,
            "verified": active.get("id") == str(app_id),
            "active_id": active.get("id", ""),
            "active_name": active.get("name", ""),
        }

    def keypress(self, key):
        self.transport.post("/keypress/" + key)
        return {"submitted": True, "key": key}
