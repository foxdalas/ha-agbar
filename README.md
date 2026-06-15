# Agbar (Veolia) Water — Home Assistant integration

[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/foxdalas/ha-agbar.svg?style=for-the-badge)](https://github.com/foxdalas/ha-agbar/releases)
[![License](https://img.shields.io/github/license/foxdalas/ha-agbar.svg?style=for-the-badge)](LICENSE)

Pulls water data from the **Agbar / Veolia customer portal** (`agbar.veolia.cat`,
a Liferay portal) into Home Assistant, including history backfill for the
**Energy Dashboard**.

> Note: this targets the Veolia/Agbar portal (`agbar.veolia.cat`). It is **not**
> the same as the `aiguesdebarcelona.cat` OFEX API used by other integrations.

## Sensors

| Entity | Unit | Notes |
|---|---|---|
| Meter reading | m³ | Cumulative reading; also imported as long-term statistics (Energy Dashboard) |
| Daily consumption | m³ | Most recent day's usage |
| Consumption this month | m³ | Month-to-date total |
| Average daily consumption | m³ | Diagnostic, last 30 days |
| Highest daily consumption | m³ | Diagnostic |
| Max daily flow | m³/h | Diagnostic, from the telemetry caudales feed |
| Days since last reading | d | Diagnostic, data freshness |
| Water price | €/m³ | Effective rate of the **last closed bill** (reference, not a forecast) |
| Last invoice | € | Amount, with payment status & issue date as attributes |
| Outstanding debt | € | Sum of invoices not marked `PAGADA`; unpaid count as attribute |
| **Water leak alarm** | binary | On when the portal reports an active `WATER LEAK` alarm |

Two long-term statistics are also imported for the Energy Dashboard:
`agbar:water_<contract>` (consumption, m³) and `agbar:water_cost_<contract>`
(cost, €). The cost is distributed from your issued invoices — variable charges
in proportion to daily usage, fixed charges evenly — so each period sums to the
real bill (the progressive Catalan *Canon de l'aigua* is already baked into that
total, so no tariff reconstruction is needed).

Telemetry lags ~1 day, so the integration polls every 4 hours. The portal only
retains ~3–4 months of daily history, so the Energy Dashboard backfill goes back
about that far (and rolls forward over time) — there is no deeper data to fetch.

## Installation

### Via HACS (recommended)

Click the button to add this repository to HACS, then install **Agbar (Veolia) Water**:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=foxdalas&repository=ha-agbar&category=integration)

<details>
<summary>Manual HACS steps</summary>

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/foxdalas/ha-agbar`, category **Integration**.
2. Search for **Agbar (Veolia) Water** and install it.
3. Restart Home Assistant.
</details>

### Manual

1. Copy `custom_components/agbar` into your Home Assistant `config/custom_components/`.
2. Restart Home Assistant.

### Set up

Then add the integration and enter your `agbar.veolia.cat` email/username and password:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=agbar)

### Energy Dashboard

After the first refresh, open the Energy configuration and, under **Water consumption**,
add the statistic `Agbar water <contract>` (`agbar:water_<contract>`). To also track
spend, set its cost to **"Use an entity tracking the total costs"** and pick
`Agbar water cost <contract>` (`agbar:water_cost_<contract>`):

[![Open your Home Assistant instance and show your energy configuration panel.](https://my.home-assistant.io/badges/config_energy.svg)](https://my.home-assistant.io/redirect/config_energy/)

## Development

`probe_agbar.py` is a standalone script used to reverse-engineer and validate the
portal API. Run `python3 probe_agbar.py --selftest` to test the parsing logic
offline, or `python3 probe_agbar.py` (with `AGBAR_USERNAME`/`AGBAR_PASSWORD`, or
interactive prompt) for a live end-to-end check.

## How it works

- Authentication: Liferay form login via `CustomLoginPortlet` (no captcha),
  behind an Incapsula WAF (needs a realistic User-Agent).
- Data: Liferay portlet **resource** endpoints (`p_p_lifecycle=2`). The
  consumption portlet reads its contract from the session, so the page's render
  phase is "warmed up" before each AJAX call.
- Numbers come back in Spanish format (decimal comma); responses are decoded as
  UTF-8 to keep `€`/accents intact.
