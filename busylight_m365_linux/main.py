#!/usr/bin/env python3
import os
import json
import time
import requests
import msal
from pathlib import Path


# ---------------------------
# Laden der Konfiguration
# ---------------------------

# XDG oder Fallback auf ~/.busylight/
CONFIG_DIR = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "busylight"
STATE_DIR = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local/state")) / "busylight"

CONFIG_FILE = CONFIG_DIR / "config.json"
TOKEN_CACHE_FILE = STATE_DIR / "token.json"

# Verzeichnisse bei Bedarf anlegen
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

if not CONFIG_FILE.exists():
    import importlib.resources
    default = importlib.resources.files("busylight_m365_linux").joinpath("default_config.json")
    with default.open("r") as src, CONFIG_FILE.open("w") as dst:
        dst.write(src.read())
    print(f"[INFO] Default config created at {CONFIG_FILE}")

with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

POLL_INTERVAL = config.get("poll_interval", 30)
DEBUG = config.get("debug", True)
BUSYLIGHT_API = config.get("busylight_api_url", "http://localhost:8000/api/v1")
CLIENT_ID = config.get("m365_client_id")
TENANT_ID = config.get("m365_tenant_id")
USER_EMAIL = config.get("user_email")
STATUS_MAPPING = config.get("status_mapping", {})
DIM = config.get("dim", 1.0)


# ---------------------------
# Cache
# ---------------------------

def load_cache():
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_FILE):
        with open(TOKEN_CACHE_FILE, "r") as f:
            cache.deserialize(f.read())
    return cache

def save_cache(cache):
    if cache.has_state_changed:
        if not os.path.exists(TOKEN_CACHE_FILE):
            os.makedirs(os.path.dirname(TOKEN_CACHE_FILE), exist_ok=True)
        with open(TOKEN_CACHE_FILE, "w") as f:
            f.write(cache.serialize())

# ---------------------------
# MS Graph Auth
# ---------------------------
SCOPES = ["Presence.Read"]

def get_token(app, cache):
    accounts = app.get_accounts()
    result = None

    # 1. Versuche, still zu erneuern (z.B. per Refresh Token)
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    # 2. Falls kein gültiges Token: Device Flow starten
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise Exception("Device Flow konnte nicht initialisiert werden.")
        print(flow["message"])
        result = app.acquire_token_by_device_flow(flow)

    return result

# ---------------------------
# Busylight API
# ---------------------------
def set_light(color, light_id):
    
    if DEBUG:
        print(f"[DEBUG] Set light {light_id or 'all'} to {color}")
    url = f"{BUSYLIGHT_API}/lights/{light_id}/on" if light_id else f"{BUSYLIGHT_API}/lights/on"
    data = {"color": color, "dim": DIM}
    response = requests.post(url, json=data)
    if DEBUG:
        print(f"[DEBUG] Request URL: {url}")
        print(f"[DEBUG] Request Data: {data}")
        print(f"[DEBUG] Response: {response.status_code} - {response.text}")

def reset_light(light_id):
    if DEBUG:
        print(f"[DEBUG] Reset light {light_id or 'all'}")
    url = f"{BUSYLIGHT_API}/lights/{light_id}/off" if light_id else f"{BUSYLIGHT_API}/lights/off"
    response = requests.post(url)
    if DEBUG:
        print(f"[DEBUG] Request URL: {url}")
        print(f"[DEBUG] Response: {response.status_code} - {response.text}")

def set_status_light(status, light_id=None):
    mapping = STATUS_MAPPING.get(status)
    if not mapping:
        # Fallback
        mapping = {"type": "effect", "value": "rainbow", "speed": "fast"}
    reset_light(light_id) # Reset before setting new status
    if mapping["type"] == "color":
        color = mapping.get("color", "green")
        set_light(color, light_id)
    elif mapping["type"] == "effect":
        effect = mapping.get("value")
        data = {
            "color": mapping.get("color"),
            "dim": mapping.get("dim", 1.0),
            "speed": mapping.get("speed", "medium"),
        }
        if DEBUG:
            print(f"[DEBUG] Set light {light_id or 'all'} effect {effect} with {data}")
        url = f"{BUSYLIGHT_API}/effects/{light_id}/{effect}" if light_id else f"{BUSYLIGHT_API}/effects/{effect}"
        response = requests.post(url, json=data)
        if DEBUG:
            print(f"[DEBUG] Request URL: {url}")
            print(f"[DEBUG] Request Data: {data}")
            print(f"[DEBUG] Response: {response.status_code} - {response.text}")

# ---------------------------
# M365 Status Abfrage
# ---------------------------
def get_user_presence(token):
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://graph.microsoft.com/v1.0/me/presence"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    data = r.json()
    return data.get("availability", "Offline")

# ---------------------------
# Main Loop
# ---------------------------
def main():
    cache = load_cache()
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache
    )

    token_result = get_token(app, cache)
    save_cache(cache)
    last_status = None

    while True:
        try:
            token = token_result["access_token"]
            status = get_user_presence(token)
            if status != last_status:
                set_status_light(status)
                last_status = status
            else:
                print(f"[INFO] Status unchanged: {status}")
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
