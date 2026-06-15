#!/usr/bin/env python3
"""
Probe script for the Agbar / Veolia (Liferay) customer portal.

Validates the two risky pieces before we wrap anything in Home Assistant:
  1. programmatic login (form-based, no captcha, behind Incapsula WAF)
  2. fetching consumption + invoices via Liferay portlet resource endpoints

Run it WITHOUT putting your password in the shell history:
    read -s AGBAR_PASSWORD; export AGBAR_PASSWORD
    export AGBAR_USERNAME='your_login'
    python3 probe_agbar.py

Requires:  pip install requests
"""
import os
import re
import sys
from datetime import date, timedelta

import requests

BASE = "https://agbar.veolia.cat"
# Realistic browser UA — Incapsula (incap_ses_* cookie) challenges obvious bots.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# Liferay's per-session CSRF token lives in an inline script as Liferay.authToken='...'
P_AUTH_RE = re.compile(r"Liferay\.authToken\s*=\s*'([^']+)'")


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})
    return s


def scrape_p_auth(html: str) -> str | None:
    m = P_AUTH_RE.search(html)
    return m.group(1) if m else None


_CONTRACT_RES = (
    re.compile(r"mis-contratos/-/detail/(\d+)"),
    re.compile(r"/misfacturas/view/(\d+)/"),
    re.compile(r'numeroContrato["\']?\s*[:=]\s*["\']?(\d{6,})'),
)


def extract_contract(html: str) -> str | None:
    """Find the contract number on an authenticated page (cookie is unreliable)."""
    for rx in _CONTRACT_RES:
        m = rx.search(html or "")
        if m:
            return m.group(1)
    return None


def login(s: requests.Session, username: str, password: str):
    """Log in via CustomLoginPortlet. Returns (p_auth, contract)."""
    # 1) GET the login page: sets WAF + session cookies, and gives us a p_auth.
    r = s.get(f"{BASE}/es/login")
    r.raise_for_status()
    p_auth = scrape_p_auth(r.text)
    if not p_auth:
        raise RuntimeError("Could not find p_auth on the login page")

    # 2) POST credentials to the portlet action URL (p_p_lifecycle=1 = action phase).
    action = (
        f"{BASE}/es/login?p_p_id=CustomLoginPortlet&p_p_lifecycle=1"
        f"&p_p_state=normal&p_p_mode=view"
        f"&_CustomLoginPortlet_javax.portlet.action=%2Flogin%2Flogin"
        f"&_CustomLoginPortlet_mvcRenderCommandName=%2Flogin%2Flogin"
        f"&p_auth={p_auth}"
    )
    body = {
        "saveLastPath": "false",
        "redirect": "",
        "doActionAfterLogin": "false",
        "_CustomLoginPortlet_lastContract": "",
        "_CustomLoginPortlet_login": username,
        "_CustomLoginPortlet_password": password,
    }
    r = s.post(action, data=body, allow_redirects=True)
    r.raise_for_status()

    # 3) Load an authenticated page and grab a fresh p_auth for the data calls.
    home = s.get(f"{BASE}/es/group/sgab/inicio")
    home.raise_for_status()
    if not is_logged_in(home.text):
        raise RuntimeError("Login failed — check username/password (no session established)")
    return scrape_p_auth(home.text), extract_contract(home.text)


def portlet_resource(s, page, portlet, op, p_auth, **params):
    """Call a Liferay portlet resource endpoint (p_p_lifecycle=2 = resource/AJAX phase)."""
    qs = {
        "p_p_id": portlet,
        "p_p_lifecycle": "2",
        "p_p_state": "normal",
        "p_p_mode": "view",
        "p_p_cacheability": "cacheLevelPage",
        "p_auth": p_auth,
        f"_{portlet}_op": op,
    }
    for k, v in params.items():
        qs[f"_{portlet}_{k}"] = v
    page_url = f"{BASE}/es/group/sgab/{page}"
    r = s.get(
        page_url,
        params=qs,
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": page_url,
                 "Accept": "application/json, text/javascript, */*; q=0.01"},
    )
    r.raise_for_status()
    # Resource responses often omit charset -> requests falls back to latin-1
    # and mangles UTF-8 (€, accented names). Force UTF-8.
    r.encoding = "utf-8"
    try:
        return r.json()
    except ValueError:
        ct = r.headers.get("content-type")
        raise RuntimeError(
            f"{op}: non-JSON response (HTTP {r.status_code}, content-type={ct}, "
            f"len={len(r.text)})\n--- body[:500] ---\n{r.text[:500]!r}\n"
            f"--- final URL ---\n{r.url}"
        )


def fetch_consumos_diaria(s, p_auth, fecha_inicio, fecha_fin, fin=40):
    return portlet_resource(
        s, "mis-consumos", "MisConsumos", "buscarConsumosDiaria", p_auth,
        fechaInicio=fecha_inicio, fechaFin=fecha_fin, inicio="0", fin=str(fin),
    )


def fetch_consumos_all(s, p_auth, fecha_inicio, fecha_fin, page_size=100, max_rows=3000):
    """Paginate buscarConsumosDiaria until the server says ultimaPagina (or cap).

    inicio/fin are an INCLUSIVE index window, so page N covers [N*size, N*size+size-1].
    Returns (rows, hit_last_page, pages_fetched).
    """
    rows = []
    inicio = 0
    pages = 0
    while len(rows) < max_rows:
        resp = portlet_resource(
            s, "mis-consumos", "MisConsumos", "buscarConsumosDiaria", p_auth,
            fechaInicio=fecha_inicio, fechaFin=fecha_fin,
            inicio=str(inicio), fin=str(inicio + page_size - 1),
        )
        batch = resp.get("consumos", [])
        pages += 1
        rows.extend(batch)
        if resp.get("ultimaPagina") or not batch:
            return rows, bool(resp.get("ultimaPagina")), pages
        inicio += page_size
    return rows, False, pages


def fetch_caudales(s, p_auth, fecha_inicio, fecha_fin, fin=40):
    return portlet_resource(
        s, "mis-consumos", "MisConsumos", "buscarCaudales", p_auth,
        fechaInicio=fecha_inicio, fechaFin=fecha_fin, inicio="0", fin=str(fin),
    )


def fetch_facturas(s, p_auth, numero_contrato, fin=11):
    return portlet_resource(
        s, "mis-facturas", "MisFacturas", "loadFacturas", p_auth,
        numeroContrato=numero_contrato, inicio="0", fin=str(fin),
        numeroFacturaBusqueda="", estadoBusqueda="",
        fechaEmisionDesdeBusqueda="", fechaEmisionHastaBusqueda="",
        importeDesdeBusqueda="", importeHastaBusqueda="",
    )


def es_float(text: str) -> float:
    """Spanish number -> float.  '1.234,56 €' -> 1234.56 ; '2031,991' -> 2031.991

    Strips anything that isn't a digit/separator/sign first, so currency symbols
    (even mojibake ones) never reach float()."""
    cleaned = re.sub(r"[^0-9,.\-]", "", text)
    return float(cleaned.replace(".", "").replace(",", "."))


# Spanish month abbreviations as they appear in the portal ("14 jun 2026").
ES_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


def es_date(text: str) -> date:
    """'14 jun 2026' -> date(2026, 6, 14). Unparseable -> date.min (sorts last)."""
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]{3,4})\.?\s+(\d{4})", text or "")
    if not m:
        return date.min
    try:
        return date(int(m.group(3)), ES_MONTHS.get(m.group(2).lower()[:3], 1), int(m.group(1)))
    except ValueError:
        return date.min


# ─────────────────────────────────────────────────────────────────────────────
# TODO (your contribution): the two functions below shape what the sensors mean.
# See the explanation in chat — there are real decisions here, not boilerplate.
# ─────────────────────────────────────────────────────────────────────────────

def is_logged_in(html: str) -> bool:
    """Decide whether an authenticated page proves we are logged in.

    A WRONG password does NOT raise an HTTP error — Liferay just re-renders a
    page (often the login form again, sometimes with an error banner). So we
    must look at the *content*. Pick a signal that is present only when signed
    in and absent otherwise.

    Ideas to consider:
      * the logout control text ("Cerrar sesión" / "Tancar sessió" for CA)
      * Liferay.ThemeDisplay marking the session: `"signedIn":true`
      * the user's name / contract number appearing in the page
    Return True only when you are confident the session is authenticated.
    """
    # Logout control is rendered only for an authenticated user (ES + CA themes).
    if "Cerrar sesión" in html or "Tancar sessió" in html:
        return True
    # Belt-and-suspenders: Liferay.ThemeDisplay serializes the session state.
    return bool(re.search(r'"signedIn"\s*:\s*true', html))


def summarize(consumos: dict, caudales: dict, facturas: dict) -> dict:
    """Turn the three raw API responses into the sensor values we want.

    Decisions that are yours to make:
      * Which entry is the "current" meter reading? `consumos["consumos"]` came
        back newest-first in our capture — do you trust that order or sort by
        date yourself? Each item has `lectura` (cumulative m³) and `consumo`
        (that day's m³).
      * Do you skip estimated readings (`lecturaEstimada == True`) for the
        "latest reading", or accept them?
      * How do you define "debt"? Sum `importeEuros` of every invoice whose
        `estado` != "PAGADA"? Count them? Both?

    Return a dict like:
      {
        "meter_reading_m3": float,     # cumulative -> HA statistics / Energy
        "last_daily_m3": float,        # most recent day's consumption
        "last_invoice_eur": float,
        "last_invoice_status": str,
        "debt_eur": float,
        "unpaid_count": int,
      }
    """
    # --- consumption ---------------------------------------------------------
    # Don't trust the server's ordering: sort by parsed date, newest first.
    rows = sorted(consumos.get("consumos", []), key=lambda r: es_date(r.get("fechaConsumo", "")),
                  reverse=True)
    # For the cumulative meter value prefer a REAL reading; estimates can jump
    # around and would pollute the Energy Dashboard sum. Fall back to any row.
    real = [r for r in rows if not r.get("lecturaEstimada", False)]
    latest = (real or rows)[0] if rows else None

    meter_reading = es_float(latest["lectura"]) if latest else None
    last_daily = es_float(latest["consumo"]) if latest else None

    # --- invoices ------------------------------------------------------------
    facts = facturas.get("facturas", [])
    # Sort by emission date too, rather than relying on response order.
    facts = sorted(facts, key=lambda f: es_date(f.get("fechaEmision", "")), reverse=True)
    last_invoice = facts[0] if facts else None

    # "Debt" = everything not explicitly PAGADA (paid). Conservative: anything
    # in another state (PENDIENTE, etc.) counts as owed.
    unpaid = [f for f in facts if f.get("estado") != "PAGADA"]
    debt = round(sum(es_float(f["importeEuros"]) for f in unpaid), 2)

    return {
        "meter_reading_m3": meter_reading,
        "last_daily_m3": last_daily,
        "last_reading_date": latest["fechaConsumo"] if latest else None,
        "last_reading_estimated": bool(latest.get("lecturaEstimada")) if latest else None,
        "last_invoice_eur": es_float(last_invoice["importeEuros"]) if last_invoice else None,
        "last_invoice_status": last_invoice["estado"] if last_invoice else None,
        "debt_eur": debt,
        "unpaid_count": len(unpaid),
    }


# Real responses captured live from the account on 2026-06-15 (trimmed). Used by
# --selftest so we can validate the parsing logic without a live login.
SAMPLE_CONSUMOS = {"ultimaPagina": False, "consumos": [
    {"fechaConsumo": "14 jun 2026", "horaConsumo": "23:11", "lectura": "2031,991", "consumo": "2,528", "lecturaEstimada": False},
    {"fechaConsumo": "13 jun 2026", "horaConsumo": "23:11", "lectura": "2029,463", "consumo": "1,03", "lecturaEstimada": False},
    {"fechaConsumo": "12 jun 2026", "horaConsumo": "23:11", "lectura": "2028,433", "consumo": "1,654", "lecturaEstimada": False},
]}
SAMPLE_CAUDALES = {"ultimaPagina": False, "caudales": [
    {"fecha": "14 Jun 2026", "horaMax": "16:11:20", "horaMin": "02:11:22", "qMin": "0", "qMax": "1,144"},
]}
SAMPLE_FACTURAS = {"ultimaPagina": True, "facturas": [
    {"numeroFactura": "22062026AE00003348", "fechaEmision": "12 jun 2026", "estado": "PAGADA", "importeEuros": "527,07 €"},
    {"numeroFactura": "22062026AE00001658", "fechaEmision": "11 mar 2026", "estado": "PAGADA", "importeEuros": "28,09 €"},
]}


def selftest():
    """Validate parsing on the real captured data — no network, no credentials."""
    assert es_float("2031,991") == 2031.991
    assert es_float("1.234,56 €") == 1234.56
    assert es_date("14 jun 2026") == date(2026, 6, 14)
    assert is_logged_in('<a>Cerrar sesión</a>') is True
    assert is_logged_in('<form id="loginForm">') is False
    result = summarize(SAMPLE_CONSUMOS, SAMPLE_CAUDALES, SAMPLE_FACTURAS)
    print("selftest OK — summarize() on real captured data:")
    for k, v in result.items():
        print(f"  {k:24} = {v}")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return

    # Credentials from env, or prompt interactively (password is never echoed).
    username = os.environ.get("AGBAR_USERNAME") or input("Agbar username: ").strip()
    password = os.environ.get("AGBAR_PASSWORD")
    if not password:
        import getpass
        password = getpass.getpass("Agbar password: ")
    if not username or not password:
        sys.exit("Username and password are required.")

    s = new_session()
    print("Logging in…")
    p_auth, contract = login(s, username, password)
    cookie_contract = s.cookies.get("LR_LAST_CONTRACT")
    print(f"OK, logged in. p_auth = {p_auth}")
    print(f"contract: scraped={contract!r}  cookie={cookie_contract!r}")

    if "--depth" in sys.argv:
        # How far back does the daily telemetry really go? Probe a 5-year window.
        s.get(f"{BASE}/es/group/sgab/mis-consumos").raise_for_status()
        far = (date.today() - timedelta(days=5 * 365)).strftime("%d/%m/%Y")
        today = date.today().strftime("%d/%m/%Y")
        rows, hit_last, pages = fetch_consumos_all(s, p_auth, far, today)
        dates = sorted(es_date(r.get("fechaConsumo", "")) for r in rows)
        print(f"\nDEPTH REPORT (window {far} … {today}):")
        print(f"  total daily rows : {len(rows)} over {pages} page(s)")
        print(f"  reached last page: {hit_last}")
        if dates:
            print(f"  newest date      : {dates[-1]}")
            print(f"  oldest date      : {dates[0]}")
            print(f"  span             : {(dates[-1] - dates[0]).days} days")
        return

    # Warm up the render phase so each portlet establishes its selected-contract
    # context in the session before we hit its resource (AJAX) endpoints.
    s.get(f"{BASE}/es/group/sgab/mis-consumos").raise_for_status()
    consumos = fetch_consumos_diaria(s, p_auth, "15/06/2024", "15/06/2026")
    caudales = fetch_caudales(s, p_auth, "16/03/2026", "15/06/2026")

    s.get(f"{BASE}/es/group/sgab/mis-facturas").raise_for_status()
    facturas = fetch_facturas(s, p_auth, contract or cookie_contract)

    print(f"\nconsumos: {len(consumos.get('consumos', []))} rows; "
          f"caudales: {len(caudales.get('caudales', []))}; "
          f"facturas: {len(facturas.get('facturas', []))}")
    if consumos.get("consumos"):
        print("newest consumo row:", consumos["consumos"][0])

    print("\nSummary:", summarize(consumos, caudales, facturas))


if __name__ == "__main__":
    main()
