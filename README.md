# Agbar (Veolia) Water — Home Assistant integration

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
| Last invoice | € | Amount, with payment status & issue date as attributes |
| Outstanding debt | € | Sum of invoices not marked `PAGADA`; unpaid count as attribute |
| Max daily flow | m³/h | Diagnostic, from the telemetry caudales feed |

Telemetry lags ~1 day, so the integration polls every 4 hours.

## Installation

1. Copy `custom_components/agbar` into your Home Assistant `config/custom_components/`
   (or add this repo as a custom HACS repository).
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Agbar (Veolia) Water**.
4. Enter your `agbar.veolia.cat` email/username and password.

### Energy Dashboard

After the first refresh, go to **Settings → Dashboards → Energy → Water consumption**
and add the statistic `Agbar water <contract>` (`agbar:water_<contract>`).

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
