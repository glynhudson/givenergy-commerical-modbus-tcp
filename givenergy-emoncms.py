#!/usr/bin/env python3
"""Poll a GivEnergy commercial inverter over Modbus TCP and post to emoncms.

Usage:
    EMONCMS_APIKEY=yourwritekey python3 givenergy_emoncms.py            # run forever
    EMONCMS_APIKEY=yourwritekey python3 givenergy_emoncms.py --once     # one post then exit
    python3 givenergy_emoncms.py --dry-run --once                       # read and print, no post

Only Modbus read commands (function code 4) are ever sent to the inverter.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from pymodbus.client import ModbusTcpClient

# --- Inverter settings ---
DEVICE_IP = os.environ.get("GIVENERGY_IP", "192.168.2.230")
PORT = int(os.environ.get("GIVENERGY_PORT", "502"))
SLAVE_ID = int(os.environ.get("GIVENERGY_SLAVE", "17"))   # 0x11

# --- emoncms settings ---
EMONCMS_URL = os.environ.get("EMONCMS_URL", "https://emoncms.org")
EMONCMS_APIKEY = os.environ.get("EMONCMS_APIKEY", "")
NODE = os.environ.get("EMONCMS_NODE", "givenergy")
INTERVAL = float(os.environ.get("POLL_INTERVAL", "10"))

# Input registers 0-59 are read in one request. Each entry is:
# (register, emoncms input name, scale, signed)
# Power values are converted to W so they match emoncms conventions.
# grid_power: positive = export, negative = import.
# battery_power: negative = charging, positive = discharging.
FIELDS = [
    (5,  "grid_v1",          0.1,   False),
    (6,  "grid_v2",          0.1,   False),
    (7,  "grid_v3",          0.1,   False),
    (10, "grid_i1",          0.1,   False),
    (11, "grid_i2",          0.1,   False),
    (12, "grid_i3",          0.1,   False),
    (13, "grid_hz",          0.01,  False),
    (16, "grid_pf",          0.001, True),
    (18, "pv1_power",        100,   True),   # 0.1 kW -> W
    (20, "pv2_power",        100,   True),
    (30, "grid_power",       100,   True),
    (42, "use_power",        100,   True),
    (52, "battery_power",    100,   True),
    (50, "battery_v",        0.1,   False),
    (59, "soc",              1,     False),
    (21, "export_today_1",   0.1,   False),
    (22, "export_today_2",   0.1,   False),
    (35, "export_today",     0.1,   False),
    (44, "import_today",     0.1,   False),
]
# 32-bit totals, low word first: (low register, name, scale)
FIELDS32 = [
    (32, "import_total_kwh", 0.1),
    (45, "export_total_kwh", 0.1),
]

# --- SoC glitch filter ---
# The BMS occasionally reports 0% for a single poll when the real SoC is e.g. 88%.
# A real SoC cannot fall by more than SOC_MAX_DROP percentage points in one poll,
# so such readings are replaced by the last good value. If the low reading persists
# for SOC_CONFIRM_POLLS consecutive polls it is accepted as genuine.
SOC_MAX_DROP = float(os.environ.get("SOC_MAX_DROP", "10"))
SOC_CONFIRM_POLLS = int(os.environ.get("SOC_CONFIRM_POLLS", "3"))
_soc_last_good = None
_soc_suspect_count = 0


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def read_inverter(client):
    """Return a dict of input name -> value, or None on error."""
    result = client.read_input_registers(address=0, count=60, slave=SLAVE_ID)
    if result.isError():
        log(f"modbus read error: {result}")
        return None
    regs = result.registers
    data = {}
    for reg, name, scale, signed in FIELDS:
        raw = regs[reg]
        if signed and raw > 32767:
            raw -= 65536
        data[name] = round(raw * scale, 3)
    for reg, name, scale in FIELDS32:
        raw = (regs[reg + 1] << 16) + regs[reg]
        data[name] = round(raw * scale, 1)
    data["soc"] = filter_soc(data["soc"])
    return data


def filter_soc(soc):
    """Reject transient SoC drops (e.g. 88% -> 0% for one poll)."""
    global _soc_last_good, _soc_suspect_count
    if _soc_last_good is not None and _soc_last_good - soc > SOC_MAX_DROP:
        _soc_suspect_count += 1
        if _soc_suspect_count < SOC_CONFIRM_POLLS:
            log(f"soc {soc:.0f}% rejected as transient (last good {_soc_last_good:.0f}%, "
                f"{_soc_suspect_count}/{SOC_CONFIRM_POLLS})")
            return _soc_last_good
        log(f"soc {soc:.0f}% persisted for {_soc_suspect_count} polls, accepting")
    _soc_suspect_count = 0
    _soc_last_good = soc
    return soc


def post_emoncms(data):
    """POST one reading to the emoncms input API. Returns True on success."""
    body = urllib.parse.urlencode({
        "node": NODE,
        "fulljson": json.dumps(data),
        "apikey": EMONCMS_APIKEY,
    }).encode()
    req = urllib.request.Request(EMONCMS_URL.rstrip("/") + "/input/post.json", data=body)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            reply = resp.read().decode().strip()
    except Exception as e:
        log(f"emoncms post failed: {e}")
        return False
    if reply.startswith("ok") or reply.startswith('{"success":true'):
        return True
    if reply == "" or reply.lstrip().lower().startswith("<!doctype"):
        log("emoncms rejected the request (check EMONCMS_APIKEY is a write key)")
    else:
        log(f"emoncms replied: {reply[:200]}")
    return False


def main():
    once = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv
    if not dry_run and not EMONCMS_APIKEY:
        log("EMONCMS_APIKEY is not set. Use --dry-run to test without posting.")
        sys.exit(1)

    client = ModbusTcpClient(DEVICE_IP, port=PORT, timeout=5)
    log(f"polling {DEVICE_IP}:{PORT} slave {SLAVE_ID} every {INTERVAL:g}s -> {EMONCMS_URL} node '{NODE}'")

    while True:
        started = time.time()
        if not client.connected and not client.connect():
            log(f"could not connect to {DEVICE_IP}:{PORT}")
        else:
            data = read_inverter(client)
            if data is None:
                client.close()
            elif dry_run:
                print(json.dumps(data, indent=2))
            elif post_emoncms(data):
                log(f"posted: pv {data['pv1_power']:.0f}W grid {data['grid_power']:.0f}W "
                    f"use {data['use_power']:.0f}W batt {data['battery_power']:.0f}W soc {data['soc']:.0f}%")
        if once:
            break
        time.sleep(max(0, INTERVAL - (time.time() - started)))
    client.close()


if __name__ == "__main__":
    main()
