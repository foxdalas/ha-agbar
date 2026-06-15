"""Synchronous client for the Agbar / Veolia (Liferay) customer portal.

This is the validated flow from probe_agbar.py, wrapped in a class. It runs
inside Home Assistant's executor (see coordinator.py) because the login flow
(cookies, redirects, Incapsula WAF) is proven against ``requests`` and not worth
re-deriving in aiohttp.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import requests

BASE = "https://agbar.veolia.cat"
# Realistic browser UA — Incapsula (incap_ses_* cookie) challenges obvious bots.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
TIMEOUT = 30

# Liferay's per-session CSRF token, embedded in an inline script.
P_AUTH_RE = re.compile(r"Liferay\.authToken\s*=\s*'([^']+)'")

ES_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


class AgbarError(Exception):
    """Generic Agbar API error."""


class AgbarAuthError(AgbarError):
    """Login failed (bad credentials / no session established)."""


def es_float(text: str) -> float:
    """Spanish number -> float.  '1.234,56 €' -> 1234.56 ; '2031,991' -> 2031.991

    Strips anything that isn't a digit/separator/sign first, so currency symbols
    (even mojibake ones) never reach float().
    """
    cleaned = re.sub(r"[^0-9,.\-]", "", text)
    return float(cleaned.replace(".", "").replace(",", "."))


def es_date(text: str) -> date:
    """'14 jun 2026' -> date(2026, 6, 14). Unparseable -> date.min (sorts last)."""
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]{3,4})\.?\s+(\d{4})", text or "")
    if not m:
        return date.min
    try:
        return date(int(m.group(3)), ES_MONTHS.get(m.group(2).lower()[:3], 1), int(m.group(1)))
    except ValueError:
        return date.min


def is_logged_in(html: str) -> bool:
    """True only when the page proves an authenticated session.

    A wrong password does NOT raise an HTTP error — Liferay just re-renders a
    page — so we must inspect the content. The logout control is rendered only
    for an authenticated user.
    """
    if "Cerrar sesión" in html or "Tancar sessió" in html:
        return True
    return bool(re.search(r'"signedIn"\s*:\s*true', html))


def summarize(raw: dict) -> dict:
    """Turn the raw API responses into the flat dict the sensors consume."""
    # --- consumption: cumulative meter reading + last daily delta ------------
    rows = sorted(
        raw.get("consumos", {}).get("consumos", []),
        key=lambda r: es_date(r.get("fechaConsumo", "")),
        reverse=True,
    )
    real = [r for r in rows if not r.get("lecturaEstimada", False)]
    latest = (real or rows)[0] if rows else None

    # --- flow rate: latest day's max caudal ----------------------------------
    caudales = sorted(
        raw.get("caudales", {}).get("caudales", []),
        key=lambda c: es_date(c.get("fecha", "")),
        reverse=True,
    )
    last_flow = caudales[0] if caudales else None

    # --- invoices: last invoice + outstanding debt ---------------------------
    facts = sorted(
        raw.get("facturas", {}).get("facturas", []),
        key=lambda f: es_date(f.get("fechaEmision", "")),
        reverse=True,
    )
    last_invoice = facts[0] if facts else None
    unpaid = [f for f in facts if f.get("estado") != "PAGADA"]
    debt = round(sum(es_float(f["importeEuros"]) for f in unpaid), 2)

    return {
        "contract": raw.get("contract"),
        "meter_reading_m3": es_float(latest["lectura"]) if latest else None,
        "last_daily_m3": es_float(latest["consumo"]) if latest else None,
        "last_reading_date": latest["fechaConsumo"] if latest else None,
        "last_reading_estimated": bool(latest.get("lecturaEstimada")) if latest else None,
        "max_flow_m3h": es_float(last_flow["qMax"]) if last_flow else None,
        "max_flow_date": last_flow["fecha"] if last_flow else None,
        "last_invoice_eur": es_float(last_invoice["importeEuros"]) if last_invoice else None,
        "last_invoice_status": last_invoice["estado"] if last_invoice else None,
        "last_invoice_date": last_invoice["fechaEmision"] if last_invoice else None,
        "debt_eur": debt,
        "unpaid_count": len(unpaid),
    }


class AgbarApiClient:
    """Logs in and fetches consumption + invoices from the Veolia portal."""

    def __init__(self, username: str, password: str, history_days: int = 400) -> None:
        self._username = username
        self._password = password
        self._history_days = history_days
        self._session: requests.Session | None = None
        self._p_auth: str | None = None
        self.contract: str | None = None

    # -- low-level ------------------------------------------------------------
    def _new_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})
        return s

    @staticmethod
    def _scrape_p_auth(html: str) -> str | None:
        m = P_AUTH_RE.search(html)
        return m.group(1) if m else None

    def login(self) -> "AgbarApiClient":
        """Authenticate via CustomLoginPortlet. Raises AgbarAuthError on failure."""
        s = self._new_session()
        # 1) GET login page -> WAF/session cookies + a p_auth token.
        r = s.get(f"{BASE}/es/login", timeout=TIMEOUT)
        r.raise_for_status()
        p_auth = self._scrape_p_auth(r.text)
        if not p_auth:
            raise AgbarError("Could not find p_auth on the login page")

        # 2) POST credentials to the portlet action URL (lifecycle=1 = action).
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
            "_CustomLoginPortlet_login": self._username,
            "_CustomLoginPortlet_password": self._password,
        }
        r = s.post(action, data=body, allow_redirects=True, timeout=TIMEOUT)
        r.raise_for_status()

        # 3) Confirm the session and grab a fresh p_auth for data calls.
        home = s.get(f"{BASE}/es/group/sgab/inicio", timeout=TIMEOUT)
        home.raise_for_status()
        if not is_logged_in(home.text):
            raise AgbarAuthError("Login failed — check username/password")

        self._session = s
        self._p_auth = self._scrape_p_auth(home.text)
        self.contract = s.cookies.get("LR_LAST_CONTRACT")
        return self

    def _resource(self, page: str, portlet: str, op: str, **params) -> dict:
        """Call a Liferay portlet resource endpoint (lifecycle=2 = AJAX)."""
        if self._session is None:
            raise AgbarError("Not logged in")
        qs = {
            "p_p_id": portlet,
            "p_p_lifecycle": "2",
            "p_p_state": "normal",
            "p_p_mode": "view",
            "p_p_cacheability": "cacheLevelPage",
            "p_auth": self._p_auth,
            f"_{portlet}_op": op,
        }
        for k, v in params.items():
            qs[f"_{portlet}_{k}"] = v
        page_url = f"{BASE}/es/group/sgab/{page}"
        r = self._session.get(
            page_url,
            params=qs,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": page_url,
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        # Resource responses omit charset -> force UTF-8 (€, accented names).
        r.encoding = "utf-8"
        try:
            return r.json()
        except ValueError as err:
            raise AgbarError(
                f"{op}: non-JSON response (len={len(r.text)}); session may have expired"
            ) from err

    def _warm_up(self, page: str) -> None:
        """Load a page's render phase so its portlet selects the contract in
        the session before we hit its (contract-less) resource endpoints."""
        self._session.get(f"{BASE}/es/group/sgab/{page}", timeout=TIMEOUT).raise_for_status()

    # -- high-level -----------------------------------------------------------
    def fetch_all(self) -> dict:
        """One full cycle: login, warm up, fetch consumption + invoices."""
        self.login()
        today = date.today()
        start = (today - timedelta(days=self._history_days)).strftime("%d/%m/%Y")
        end = today.strftime("%d/%m/%Y")

        self._warm_up("mis-consumos")
        consumos = self._resource(
            "mis-consumos", "MisConsumos", "buscarConsumosDiaria",
            fechaInicio=start, fechaFin=end, inicio="0", fin=str(self._history_days),
        )
        caudales = self._resource(
            "mis-consumos", "MisConsumos", "buscarCaudales",
            fechaInicio=start, fechaFin=end, inicio="0", fin=str(self._history_days),
        )

        facturas = {"facturas": []}
        if self.contract:
            self._warm_up("mis-facturas")
            facturas = self._resource(
                "mis-facturas", "MisFacturas", "loadFacturas",
                numeroContrato=self.contract, inicio="0", fin="24",
                numeroFacturaBusqueda="", estadoBusqueda="",
                fechaEmisionDesdeBusqueda="", fechaEmisionHastaBusqueda="",
                importeDesdeBusqueda="", importeHastaBusqueda="",
            )

        return {
            "contract": self.contract,
            "consumos": consumos,
            "caudales": caudales,
            "facturas": facturas,
        }
