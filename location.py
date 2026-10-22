import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


CONFIG_PATH = Path.home() / ".config/kanslor-location/config.json"


class LocationNotConfigured(RuntimeError):
    pass


def normalize_zip_code(value):
    zip_code = value.strip()
    if len(zip_code) != 5 or not zip_code.isdigit():
        raise ValueError("enter a five-digit United States ZIP code")
    return zip_code


def lookup_zip_code(zip_code):
    zip_code = normalize_zip_code(zip_code)
    request = urllib.request.Request(
        f"https://api.zippopotam.us/us/{zip_code}",
        headers={"User-Agent": "kanslor-location/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            place = json.load(response)["places"][0]
        return {
            "zip_code": zip_code,
            "name": f"{place['place name']}, {place['state abbreviation']}",
            "latitude": float(place["latitude"]),
            "longitude": float(place["longitude"]),
            "radius_miles": 5.0,
        }
    except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"could not find United States ZIP code {zip_code}") from error


def save_location(location):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=CONFIG_PATH.parent, delete=False) as temporary:
        json.dump(location, temporary, indent=2)
        temporary.write("\n")
        temporary_name = temporary.name
    os.chmod(temporary_name, 0o600)
    os.replace(temporary_name, CONFIG_PATH)


def load_location():
    try:
        location = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        zip_code = normalize_zip_code(location["zip_code"])
        latitude, longitude = float(location["latitude"]), float(location["longitude"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise LocationNotConfigured("no ZIP location is configured") from error
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise LocationNotConfigured("configured ZIP location is invalid")
    return {
        "zip_code": zip_code,
        "name": str(location.get("name") or zip_code),
        "latitude": latitude,
        "longitude": longitude,
        "radius_miles": float(location.get("radius_miles", 5.0)),
    }


def configure_location(zip_code=None):
    while True:
        try:
            location = lookup_zip_code(zip_code or input("Enter your five-digit United States ZIP code: "))
        except ValueError as error:
            if zip_code:
                raise
            print(f"Invalid ZIP code: {error}.")
            continue
        save_location(location)
        return location


def ensure_location(interactive):
    try:
        return load_location()
    except LocationNotConfigured:
        if not interactive:
            raise LocationNotConfigured("run this app with --set-zip before starting its collector") from None
        print("A ZIP code is required to set your local area.")
        return configure_location()


def handle_location_command(arguments):
    if not arguments or arguments[0] != "--set-zip":
        return False
    if len(arguments) > 2:
        raise SystemExit("usage: --set-zip [ZIP_CODE]")
    location = configure_location(arguments[1] if len(arguments) == 2 else None)
    print(f"Location set to {location['name']} ({location['zip_code']}).")
    return True
