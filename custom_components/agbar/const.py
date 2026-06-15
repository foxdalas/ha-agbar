"""Constants for the Agbar (Veolia) Water integration."""
from __future__ import annotations

DOMAIN = "agbar"

# The portal exposes telemetry with a ~1 day lag, so polling often is pointless.
SCAN_INTERVAL_HOURS = 4

# How many days of daily history to pull (for the Energy Dashboard backfill).
HISTORY_DAYS = 400

MANUFACTURER = "Agbar (Veolia)"
