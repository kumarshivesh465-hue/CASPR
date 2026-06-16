"""
FIREBASE UPLOAD MODULE
=======================
Uploads satellite index results to Firebase Realtime Database.
Called from app.py after each successful analysis run.

Structure written to Firebase:
  /satellite/{field_id}/{date}/
      meta: { platform, cloud_cover, bbox, timestamp }
      indices: { NDVI: {mean, min, max, std}, EVI: {...}, ... }
      status: "ok"
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone


def _firebase_put(db_url: str, path: str, data: dict) -> bool:
    """
    Write data to Firebase via REST API (no SDK needed).
    Uses PUT so it overwrites the node at `path`.
    """
    url = f"{db_url.rstrip('/')}/{path}.json"
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[Firebase] Upload error: {e}")
        return False


def _firebase_get(db_url: str, path: str) -> dict | None:
    """Read a node from Firebase via REST API."""
    url = f"{db_url.rstrip('/')}/{path}.json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Firebase] Read error: {e}")
        return None


def upload_satellite_results(
    db_url: str,
    field_id: str,
    bbox: tuple,
    meta: dict,
    index_stats: dict,
) -> bool:
    """
    Upload satellite analysis results to Firebase.

    Args:
        db_url:      Firebase Realtime DB URL, e.g.
                     "https://your-project-default-rtdb.firebaseio.com"
        field_id:    Unique field identifier, e.g. "field_hyderabad_01"
        bbox:        (lon_min, lat_min, lon_max, lat_max)
        meta:        dict with keys: date, cloud_cover, platform
        index_stats: dict of { "NDVI": {"mean":0.4, "min":0.1, "max":0.8, "std":0.1}, ... }

    Returns:
        True if upload succeeded, False otherwise.
    """
    date_key  = str(meta.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")))
    # Firebase keys can't contain dots or slashes — sanitise
    date_key  = date_key.replace("/", "_").replace(".", "_").replace(" ", "_").replace("→", "to")
    timestamp = datetime.now(timezone.utc).isoformat()

    payload = {
        "meta": {
            "platform":    meta.get("platform", "Sentinel-2"),
            "cloud_cover": meta.get("cloud_cover", 0),
            "date":        str(meta.get("date", "")),
            "bbox": {
                "lon_min": bbox[0],
                "lat_min": bbox[1],
                "lon_max": bbox[2],
                "lat_max": bbox[3],
            },
            "uploaded_at": timestamp,
        },
        "indices": index_stats,
        "status": "ok",
    }

    path = f"satellite/{field_id}/{date_key}"
    ok   = _firebase_put(db_url, path, payload)

    if ok:
        # Also update a "latest" node for fast dashboard reads
        _firebase_put(db_url, f"satellite/{field_id}/latest", payload)

    return ok


def get_rover_readings(db_url: str, field_id: str, limit: int = 20) -> list[dict]:
    """
    Fetch the most recent rover readings for a field.
    Returns list of reading dicts sorted newest-first.
    """
    data = _firebase_get(db_url, f"rover/{field_id}/readings")
    if not data:
        return []

    readings = list(data.values())
    readings.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return readings[:limit]


def get_fused_recommendations(db_url: str, field_id: str) -> dict | None:
    """Fetch the latest AI-fused recommendations for a field."""
    return _firebase_get(db_url, f"recommendations/{field_id}/latest")
