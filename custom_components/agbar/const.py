"""Constants for the Agbar (Veolia) Water integration."""
from __future__ import annotations

DOMAIN = "agbar"

# The portal exposes telemetry with a ~1 day lag, so polling often is pointless.
SCAN_INTERVAL_HOURS = 4

# Date window we ask for. The portal only retains ~108 days of daily telemetry
# (verified), so a generous window simply captures the full retained set as it
# rolls forward — we never get more than the server keeps.
HISTORY_DAYS = 400

# The server appears to cap a single response at ~100 rows regardless of `fin`,
# so we paginate consumption in chunks this size until ultimaPagina.
PAGE_SIZE = 100

MANUFACTURER = "Agbar (Veolia)"
