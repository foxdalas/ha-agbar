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


_CONTRACT_RES = (
    re.compile(r"mis-contratos/-/detail/(\d+)"),
    re.compile(r"/misfacturas/view/(\d+)/"),
    re.compile(r'numeroContrato["\']?\s*[:=]\s*["\']?(\d{6,})'),
)


def extract_contract(html: str) -> str | None:
    """Find the contract number on an authenticated page (link/markup), since the
    LR_LAST_CONTRACT cookie is not reliably set right after a fresh login."""
    for rx in _CONTRACT_RES:
        m = rx.search(html or "")
        if m:
            return m.group(1)
    return None


def num(value) -> float:
    """Dot-decimal number (JSON factura/contract endpoints) -> float.
    These use '.' as the decimal point, UNLIKE the comma-format list endpoints,
    so es_float (which strips '.') would mangle them."""
    if isinstance(value, (int, float)):
        return float(value)
    return float(re.sub(r"[^0-9.\-]", "", str(value)) or "0")


def desglose_split(components: list) -> tuple[float, float, float]:
    """From a get-desglose-factura array, return (total, variable, fixed).

    variable = sum of all CONSUMO subconceptos (scale with m³, incl. the
    progressive Canon); fixed = the rest (CUOTA, meter upkeep, IVA) = total - var.
    """
    total = sum(num(c.get("importe", 0)) for c in components or [])
    variable = 0.0
    for comp in components or []:
        for sub in comp.get("subconceptos", []):
            if sub.get("concepto", "").upper() == "CONSUMO":
                variable += num(sub.get("importe", 0))
    return round(total, 2), round(variable, 2), round(total - variable, 2)


def daily_consumption(raw: dict) -> dict:
    """Map of date -> daily consumption (m³) from the consumos rows."""
    out: dict[date, float] = {}
    for r in raw.get("consumos", {}).get("consumos", []):
        d = es_date(r.get("fechaConsumo", ""))
        if d != date.min:
            out[d] = es_float(r.get("consumo", "0"))
    return out


def cost_per_day(raw: dict) -> dict:
    """Per-day cost (€) for each billed period: distribute the invoice total over
    the days we have consumption for — variable part ∝ daily usage, fixed evenly.
    Sums to the invoice total per period (the progressive Canon is already baked
    into that total, so no tariff math is needed)."""
    daily = daily_consumption(raw)
    desgloses = raw.get("desgloses", {})
    out: dict[date, float] = {}
    for inv in raw.get("facturas", {}).get("facturas", []):
        comps = desgloses.get(inv.get("numeroFactura"))
        if not comps:
            continue
        _total, variable, fixed = desglose_split(comps)
        di, df = es_date(inv.get("fechaInicio", "")), es_date(inv.get("fechaFin", ""))
        if di == date.min or df == date.min:
            continue
        in_period = {d: c for d, c in daily.items() if di <= d <= df}
        if not in_period:
            continue
        sum_c = sum(in_period.values())
        n = len(in_period)
        for d, c in in_period.items():
            var_share = variable * (c / sum_c) if sum_c else variable / n
            out[d] = out.get(d, 0.0) + var_share + fixed / n
    return out


def effective_price(raw: dict) -> tuple:
    """Reference €/m³ from the most recent fully-covered (closed) invoice.
    Returns (price, period_label). NOT a predictor — the progressive Canon makes
    €/m³ swing with consumption; this is the last bill's effective rate."""
    daily = daily_consumption(raw)
    last_data = max(daily) if daily else None
    fallback = (None, None)
    for inv in sorted(
        raw.get("facturas", {}).get("facturas", []),
        key=lambda f: es_date(f.get("fechaEmision", "")), reverse=True,
    ):
        di, df = es_date(inv.get("fechaInicio", "")), es_date(inv.get("fechaFin", ""))
        if di == date.min or df == date.min:
            continue
        period_m3 = sum(c for d, c in daily.items() if di <= d <= df)
        if period_m3 <= 0:
            continue
        price = round(es_float(inv["importeEuros"]) / period_m3, 4)
        label = f"{inv.get('fechaInicio')} – {inv.get('fechaFin')}"
        if last_data is not None and df <= last_data:
            return price, label  # fully-covered/closed period: trust it
        if fallback == (None, None):
            fallback = (price, f"{label} (partial)")
    return fallback


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

    # --- analytics from the daily series -------------------------------------
    daily = daily_consumption(raw)
    today = date.today()
    last_data = max(daily) if daily else None
    month_to_date = round(
        sum(c for d, c in daily.items() if (d.year, d.month) == (today.year, today.month)), 3
    ) if daily else None
    recent = [c for d, c in daily.items() if (today - d).days <= 30]
    avg_daily = round(sum(recent) / len(recent), 3) if recent else None
    max_day = round(max(daily.values()), 3) if daily else None

    # --- price (reference) ---------------------------------------------------
    price, price_period = effective_price(raw)

    # --- leak alarms ---------------------------------------------------------
    alarms = ((raw.get("alarms") or {}).get("data") or {}).get("alertasList", [])
    leak_active = any("LEAK" in (a.get("type", "").upper()) and a.get("active") for a in alarms)
    last_alarm = max(alarms, key=lambda a: a.get("startDate", ""), default=None)

    # --- contract / meter metadata -------------------------------------------
    info = raw.get("contract_info") or {}

    return {
        "contract": raw.get("contract"),
        "meter_reading_m3": es_float(latest["lectura"]) if latest else None,
        "last_daily_m3": es_float(latest["consumo"]) if latest else None,
        "last_reading_date": latest["fechaConsumo"] if latest else None,
        "last_reading_estimated": bool(latest.get("lecturaEstimada")) if latest else None,
        "days_since_reading": (today - last_data).days if last_data else None,
        "month_to_date_m3": month_to_date,
        "avg_daily_m3": avg_daily,
        "max_day_m3": max_day,
        "max_flow_m3h": es_float(last_flow["qMax"]) if last_flow else None,
        "max_flow_date": last_flow["fecha"] if last_flow else None,
        "price_eur_m3": price,
        "price_period": price_period,
        "last_invoice_eur": es_float(last_invoice["importeEuros"]) if last_invoice else None,
        "last_invoice_status": last_invoice["estado"] if last_invoice else None,
        "last_invoice_date": last_invoice["fechaEmision"] if last_invoice else None,
        "debt_eur": debt,
        "unpaid_count": len(unpaid),
        "leak_active": leak_active,
        "last_alarm_type": last_alarm.get("type") if last_alarm else None,
        "last_alarm_start": last_alarm.get("startDate") if last_alarm else None,
        "last_alarm_days": last_alarm.get("daysActive") if last_alarm else None,
        "meter_serial": raw.get("meter_serial"),
        "supply_address": info.get("supplyAddress"),
        "owner": info.get("fullName"),
        "smart_metering": info.get("smartMetering"),
        "point_of_service": info.get("pointOfServiceId"),
    }


class AgbarApiClient:
    """Logs in and fetches consumption + invoices from the Veolia portal."""

    def __init__(
        self,
        username: str,
        password: str,
        history_days: int = 400,
        page_size: int = 100,
    ) -> None:
        self._username = username
        self._password = password
        self._history_days = history_days
        self._page_size = page_size
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
        # Prefer scraping the contract from the page; cookie is an unreliable fallback.
        self.contract = extract_contract(home.text) or s.cookies.get("LR_LAST_CONTRACT")
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

    def _resource_all_pages(
        self, page: str, portlet: str, op: str, key: str, *, fecha_inicio: str,
        fecha_fin: str, max_rows: int = 5000,
    ) -> dict:
        """Paginate a resource op until ultimaPagina (the server caps pages at
        ~100 rows regardless of ``fin``). ``key`` is the list field name."""
        rows: list[dict] = []
        inicio = 0
        while len(rows) < max_rows:
            resp = self._resource(
                page, portlet, op, fechaInicio=fecha_inicio, fechaFin=fecha_fin,
                inicio=str(inicio), fin=str(inicio + self._page_size - 1),
            )
            batch = resp.get(key, [])
            rows.extend(batch)
            if resp.get("ultimaPagina") or not batch:
                break
            inicio += self._page_size
        return {key: rows, "ultimaPagina": True}

    def _warm_up(self, page: str) -> None:
        """Load a page's render phase so its portlet selects the contract in
        the session before we hit its (contract-less) resource endpoints."""
        self._session.get(f"{BASE}/es/group/sgab/{page}", timeout=TIMEOUT).raise_for_status()

    def _safe(self, fn):
        """Run an optional fetch; never let one extra break the whole update."""
        try:
            return fn()
        except (AgbarError, requests.RequestException, ValueError, KeyError):
            return None

    def _fetch_contratos(self) -> dict:
        return self._resource(
            "mis-contratos", "ContractDetails", "loadContratos", offset="0", limit="10"
        )

    def _fetch_alarms(self) -> dict:
        return self._resource(
            "mis-consumos", "alertas_consumo_settings_portlet", "buscarUltimasAlarmas"
        )

    def _fetch_factura_datos(self, factura: str, estado: str) -> dict:
        return self._resource(
            "mis-facturas", "MisFacturas", "get-datos-factura",
            numeroContrato=self.contract, numeroFactura=factura, view="factura",
            estado=estado, redsys="R0", payment="redsys",
        )

    def _fetch_factura_desglose(self, factura: str, estado: str):
        return self._resource(
            "mis-facturas", "MisFacturas", "get-desglose-factura",
            numeroContrato=self.contract, numeroFactura=factura, view="factura",
            estado=estado, redsys="R0", payment="redsys",
        )

    # -- high-level -----------------------------------------------------------
    def fetch_all(self) -> dict:
        """One full cycle: login, warm up, fetch consumption + invoices."""
        self.login()
        today = date.today()
        start = (today - timedelta(days=self._history_days)).strftime("%d/%m/%Y")
        end = today.strftime("%d/%m/%Y")

        self._warm_up("mis-consumos")
        consumos = self._resource_all_pages(
            "mis-consumos", "MisConsumos", "buscarConsumosDiaria", "consumos",
            fecha_inicio=start, fecha_fin=end,
        )
        # We only need the most recent day's flow, so one page (newest-first) is enough.
        caudales = self._resource(
            "mis-consumos", "MisConsumos", "buscarCaudales",
            fechaInicio=start, fechaFin=end, inicio="0", fin="30",
        )

        # Optional extras — tolerate failure so a hiccup never blocks the core.
        alarms = self._safe(self._fetch_alarms) or {}
        contratos = self._safe(self._fetch_contratos) or {}
        contract_info = (contratos.get("contractToShow") or [None])[0]

        # Consumption window: we only cost invoices whose period overlaps it.
        cdates = [es_date(r.get("fechaConsumo", "")) for r in consumos.get("consumos", [])]
        cdates = [d for d in cdates if d != date.min]
        win_lo, win_hi = (min(cdates), max(cdates)) if cdates else (date.max, date.min)

        facturas = {"facturas": []}
        desgloses: dict[str, list] = {}
        meter_serial = None
        if self.contract:
            self._warm_up("mis-facturas")
            facturas = self._resource(
                "mis-facturas", "MisFacturas", "loadFacturas",
                numeroContrato=self.contract, inicio="0", fin="24",
                numeroFacturaBusqueda="", estadoBusqueda="",
                fechaEmisionDesdeBusqueda="", fechaEmisionHastaBusqueda="",
                importeDesdeBusqueda="", importeHastaBusqueda="",
            )
            fact_list = sorted(
                facturas.get("facturas", []),
                key=lambda f: es_date(f.get("fechaEmision", "")), reverse=True,
            )
            if fact_list:
                datos = self._safe(
                    lambda: self._fetch_factura_datos(
                        fact_list[0]["numeroFactura"], fact_list[0].get("estadoNum", "3")
                    )
                )
                meter_serial = (datos or {}).get("numeroContador")
            # Pull the desglose only for invoices overlapping our daily data.
            for inv in fact_list[:12]:
                di, df = es_date(inv.get("fechaInicio", "")), es_date(inv.get("fechaFin", ""))
                if di == date.min or df == date.min or df < win_lo or di > win_hi:
                    continue
                comps = self._safe(
                    lambda inv=inv: self._fetch_factura_desglose(
                        inv["numeroFactura"], inv.get("estadoNum", "3")
                    )
                )
                if comps:
                    desgloses[inv["numeroFactura"]] = comps

        return {
            "contract": self.contract,
            "consumos": consumos,
            "caudales": caudales,
            "facturas": facturas,
            "desgloses": desgloses,
            "alarms": alarms,
            "contract_info": contract_info,
            "meter_serial": meter_serial,
        }
