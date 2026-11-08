import os
import json
import time
from datetime import datetime, timezone, timedelta
import requests
import numpy as np
from numba import njit

# ==============================================================================
# 1. MOTOR SCRAPLING (SUPLANTACIÓN TLS CHROME + BYPASS CLOUDFLARE)
# ==============================================================================
try:
    from scrapling.fetchers import FetcherSession, StealthySession
    SCRAPLING_OK = True
except Exception:
    SCRAPLING_OK = False

# ==============================================================================
# 2. CONFIGURACIÓN — MOTOR v4.2 CLOUD SNIPER (ESPECIALISTA EN HÁNDICAPS)
# ==============================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5203732922")
COOKIE_BETANO_CLOUD = os.getenv("COOKIE_BETANO", "")

TORNEOS_BETANO_URLS = [
    ("🏆 Copa América", "COPA_AMERICA", "https://www.betano.pe/api/virtuals/futbol/copa-america/?req=stnf,c", "https://www.betano.pe/virtuals/futbol/copa-america/"),
    ("🌍 La Copa",      "LA_COPA",      "https://www.betano.pe/api/virtuals/futbol/copa/?req=stnf,c",         "https://www.betano.pe/virtuals/futbol/copa/"),
    ("🇪🇺 Euro Virtual", "EURO",         "https://www.betano.pe/api/virtuals/futbol/euro/?req=stnf,c",         "https://www.betano.pe/virtuals/futbol/euro/"),
    ("🌐 Mundial",      "MUNDIAL",      "https://www.betano.pe/api/virtuals/futbol/?req=stnf,c",              "https://www.betano.pe/virtuals/futbol/")
]

BANKROLL_INICIAL = 100.0         # Banca base S/ 100.00
STOP_WIN_SOLES = 115.0           # +15% (+S/ 15.00) -> Pausa automática para asegurar ganancias
STOP_LOSS_SOLES = 90.0           # -10% (-S/ 10.00) -> Cortacircuitos automático anti-racha
KELLY_FRAC = 0.16                # Fracción de Kelly conservadora

N_SIMS = 500_000                 # 500,000 simulaciones Monte Carlo en C por partido
SEGUNDOS_ESCANEO = 15            # Escaneo cada 15 segundos en tiempo real
SEGUNDOS_MIN_PARA_APOSTAR = 28   # Margen mínimo para que te dé tiempo de meter el pick en el celular
DURACION_BLOQUE_NUBE_MIN = 52    # En GitHub Actions corre 52 min seguidos por cada ejecución

# Filtros estrictos del Motor v4 (Cero cuotas bajas de relleno)
CUOTA_MIN = 1.48
CUOTA_MAX = 2.38

ARCHIVO_ESTADO = "estado_auto_virtual.json"
ZONA_PERU = timezone(timedelta(hours=-5))


def redondear_stake_antibaneo(monto):
    """Redondea siempre a múltiplos limpios de S/ 0.50 (S/ 1.50 a S/ 3.50) para evitar limitaciones."""
    redondeado = round(monto * 2.0) / 2.0
    return max(1.50, min(3.50, redondeado))


# ==============================================================================
# 3. NÚCLEO MONTE CARLO EN C (NUMBA) CALIBRADO CON MATRIZ INSPIRED
# ==============================================================================
@njit(fastmath=True)
def simular_motor_inspired_numba(lam_l, lam_v, cov, n_sims):
    ll = max(0.18, lam_l - cov)
    lv = max(0.15, lam_v - cov)

    c_1, c_x, c_2 = 0, 0, 0
    c_l_por2, c_v_por2 = 0, 0
    c_o15, c_o25, c_u25 = 0, 0, 0
    c_btts_si, c_btts_no = 0, 0

    for _ in range(n_sims):
        r_com = np.random.poisson(cov)
        gl = np.random.poisson(ll) + r_com
        gv = np.random.poisson(lv) + r_com
        tot = gl + gv
        diff = gl - gv

        if diff > 0:
            c_1 += 1
            if diff >= 2:
                c_l_por2 += 1
        elif diff == 0:
            c_x += 1
        else:
            c_2 += 1
            if diff <= -2:
                c_v_por2 += 1

        if tot >= 2:
            c_o15 += 1
        if tot >= 3:
            c_o25 += 1
        else:
            c_u25 += 1

        if gl >= 1 and gv >= 1:
            c_btts_si += 1
        else:
            c_btts_no += 1

    inv = 1.0 / float(n_sims)
    return (
        c_1 * inv,           # 0: Gana Local (1)
        c_x * inv,           # 1: Empate (X)
        c_2 * inv,           # 2: Gana Visita (2)
        c_l_por2 * inv,      # 3: Local gana por 2+ goles
        c_v_por2 * inv,      # 4: Visita gana por 2+ goles
        c_o15 * inv,         # 5: Más de 1.5 Goles
        c_o25 * inv,         # 6: Más de 2.5 Goles
        c_u25 * inv,         # 7: Menos de 2.5 Goles
        c_btts_si * inv,     # 8: Ambos Anotan Sí
        c_btts_no * inv      # 9: Ambos Anotan No
    )


# ==============================================================================
# 4. INGENIERÍA INVERSA DEL JSON DE BETANO (MERCADOS 4144, 4145, 4146, 4014, 4141)
# ==============================================================================
def _devig_diccionario(mapa_cuotas):
    validas = {k: float(v) for k, v in mapa_cuotas.items() if float(v) > 1.01}
    if not validas:
        return {}, 0.0
    suma_inv = sum(1.0 / v for v in validas.values())
    margen_pct = (suma_inv - 1.0) * 100.0
    probs_limpias = {k: (1.0 / v) / suma_inv for k, v in validas.items()}
    return probs_limpias, margen_pct


def _lambda_desde_goles_exactos_equipo(mapa_goles_eq):
    probs, _ = _devig_diccionario(mapa_goles_eq)
    if not probs:
        return None
    pesos_gol = {"0": 0.0, "1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0, "5+": 5.25}
    lam = sum(pesos_gol[k] * p for k, p in probs.items() if k in pesos_gol)
    return lam if lam > 0.20 else None


def decodificar_evento_json_betano(ev_raw, nombre_torneo, clave_torneo, proximos_cola):
    ev_id = str(ev_raw.get("id", ""))
    seg_inicio = int(ev_raw.get("secondsToStart", 0) or 0)
    participantes = ev_raw.get("participants", [])
    if len(participantes) < 2:
        return None

    local = str(participantes[0].get("name", "")).strip()
    visita = str(participantes[1].get("name", "")).strip()
    if not local or not visita:
        return None

    cuotas = {}
    goles_exactos_total = {}
    goles_eq_local = {}
    goles_eq_visita = {}
    marcadores_exactos = []
    handicap_asiatico = {}

    for mkt in ev_raw.get("markets", []):
        m_type = str(mkt.get("type", "")).upper()
        sels = mkt.get("selections", [])

        if m_type == "MRES":
            for s in sels:
                nom = str(s.get("name", "")).strip()
                pr = float(s.get("price", 0.0) or 0.0)
                if nom in ("1", "X", "2") and pr > 1.01:
                    cuotas[nom] = pr

        elif m_type == "HCTG":
            for s in sels:
                nom = str(s.get("name", "")).lower()
                pr = float(s.get("price", 0.0) or 0.0)
                if pr <= 1.01:
                    continue
                if "2.5" in nom:
                    cuotas["over_25" if ("más" in nom or "mas" in nom) else "under_25"] = pr
                elif "1.5" in nom:
                    cuotas["over_15" if ("más" in nom or "mas" in nom) else "under_15"] = pr

        elif m_type == "BTSC":
            for s in sels:
                nom = str(s.get("name", "")).lower()
                pr = float(s.get("price", 0.0) or 0.0)
                if pr <= 1.01:
                    continue
                if "sí" in nom or "si" in nom:
                    cuotas["btts_si"] = pr
                elif "no" in nom:
                    cuotas["btts_no"] = pr

        elif m_type == "DBLC":
            for s in sels:
                nom = str(s.get("name", "")).upper().strip()
                pr = float(s.get("price", 0.0) or 0.0)
                if nom in ("1X", "2X", "12") and pr > 1.01:
                    cuotas[nom] = pr

        elif m_type == "4144":  # Hándicap Asiático (Margen ultra bajo 4.64%)
            for s in sels:
                nom = str(s.get("name", "")).strip()
                hcap = float(s.get("handicap", 0.0) or 0.0)
                pr = float(s.get("price", 0.0) or 0.0)
                if pr > 1.01:
                    handicap_asiatico[nom] = {"price": pr, "handicap": hcap}

        elif m_type == "4014":
            for s in sels:
                nom = str(s.get("name", "")).strip()
                pr = float(s.get("price", 0.0) or 0.0)
                if pr > 1.01:
                    goles_exactos_total[nom] = pr

        elif m_type == "4145":
            for s in sels:
                nom = str(s.get("name", "")).strip()
                pr = float(s.get("price", 0.0) or 0.0)
                if pr > 1.01:
                    goles_eq_local[nom] = pr

        elif m_type == "4146":
            for s in sels:
                nom = str(s.get("name", "")).strip()
                pr = float(s.get("price", 0.0) or 0.0)
                if pr > 1.01:
                    goles_eq_visita[nom] = pr

        elif m_type == "4141":
            for s in sels:
                nom = str(s.get("name", "")).strip()
                pr = float(s.get("price", 0.0) or 0.0)
                if "-" in nom and len(nom) <= 5 and pr > 1.01:
                    marcadores_exactos.append((nom, pr))

    if "1" not in cuotas or "X" not in cuotas or "2" not in cuotas:
        return None

    marcadores_exactos.sort(key=lambda x: x[1])
    top_marcadores = [f"{m[0]} (@{m[1]:.2f})" for m in marcadores_exactos[:3]]

    return {
        "id": f"BT_{ev_id}",
        "torneo": nombre_torneo,
        "clave_torneo": clave_torneo,
        "seconds_to_start": seg_inicio,
        "local": local,
        "visita": visita,
        "partido": f"{local} vs {visita}",
        "cuotas": cuotas,
        "goles_exactos_total": goles_exactos_total,
        "goles_eq_local": goles_eq_local,
        "goles_eq_visita": goles_eq_visita,
        "handicap_asiatico": handicap_asiatico,
        "top_marcadores": top_marcadores,
        "proximos_en_cola": proximos_cola[:3]
    }


# ==============================================================================
# 5. CLIENTE SCRAPLING DE BAJA LATENCIA (SESIÓN REUTILIZABLE EN NUBE Y PC)
# ==============================================================================
class BetanoVirtualClient:
    def __init__(self):
        self.usa_scrapling = SCRAPLING_OK
        self.req_session = requests.Session()

    def _headers_para_torneo(self, referer_url):
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
            "Referer": referer_url,
            "Origin": "https://www.betano.pe",
            "X-Requested-With": "XMLHttpRequest"
        }
        if COOKIE_BETANO_CLOUD:
            hdrs["Cookie"] = COOKIE_BETANO_CLOUD
        return hdrs

    def descargar_json_torneo(self, url_api, referer_url):
        hdrs = self._headers_para_torneo(referer_url)

        if self.usa_scrapling:
            try:
                with FetcherSession(impersonate="chrome", timeout=7) as s:
                    r = s.get(url_api, headers=hdrs)
                    if r.status == 200 and r.text and r.text.strip().startswith("{"):
                        return json.loads(r.text.strip())
            except Exception:
                pass

            try:
                with StealthySession(headless=True, solve_cloudflare=True) as stealth:
                    page = stealth.fetch(url_api)
                    raw = page.text if hasattr(page, "text") else str(page)
                    ini, fin = raw.find("{"), raw.rfind("}")
                    if ini != -1 and fin != -1:
                        return json.loads(raw[ini:fin + 1])
            except Exception:
                pass

        try:
            r = self.req_session.get(url_api, headers=hdrs, timeout=7)
            if r.status_code == 200 and r.text.strip().startswith("{"):
                return r.json()
        except Exception:
            pass
        return None

    def escanear_4_torneos_betano(self):
        partidos_activos = []
        for nom_torneo, clave_t, url_api, ref_url in TORNEOS_BETANO_URLS:
            payload = self.descargar_json_torneo(url_api, ref_url)
            if not payload or "data" not in payload:
                continue

            data_node = payload.get("data", {})
            current_events = data_node.get("currentEvents", [])
            content_queue = data_node.get("content", [])

            proximos_cola = []
            for item in content_queue:
                if item.get("liveNow"):
                    continue
                partes = item.get("displayNameParts", [])
                if len(partes) >= 2:
                    proximos_cola.append(f"{partes[0].get('name', '')} vs {partes[1].get('name', '')}")

            for ev_raw in current_events:
                ev_dec = decodificar_evento_json_betano(ev_raw, nom_torneo, clave_t, proximos_cola)
                if ev_dec:
                    partidos_activos.append(ev_dec)

        return partidos_activos


# ==============================================================================
# 6. MOTOR DE DECISIÓN v4.2 (PRECISIÓN EXACTA EN HÁNDICAPS ASIÁTICOS + SINGLE PICK)
# ==============================================================================
def evaluar_handicap_asiatico_exacto(hcap, cuota, p_win, p_draw, p_win_por2, p_lose_por2):
    """
    Calcula la Probabilidad Efectiva Equivalente (p_eff) y el Valor Esperado (ev_pct)
    exacto para todas las líneas de Hándicap Asiático (-0.75, -0.5, -0.25, 0.0, +0.25, +0.5, +0.75).
    """
    p_lose = max(0.0, 1.0 - p_win - p_draw)
    p_win_exact1 = max(0.0, p_win - p_win_por2)
    p_lose_exact1 = max(0.0, p_lose - p_lose_por2)

    if hcap == -0.25:
        # Gana si gana el equipo; pierde solo el 50% si empatan
        retorno_esp = (p_win * cuota) + (p_draw * 0.50)
    elif hcap == 0.25:
        # Gana si gana el equipo; gana media apuesta y reembolsa media si empatan
        retorno_esp = (p_win * cuota) + (p_draw * ((0.50 * cuota) + 0.50))
    elif hcap == 0.0:
        # Reembolso total en caso de empate
        retorno_esp = (p_win * cuota) + (p_draw * 1.0)
    elif hcap == -0.5:
        retorno_esp = p_win * cuota
    elif hcap == 0.5:
        retorno_esp = (p_win + p_draw) * cuota
    elif hcap == -0.75:
        # Gana total si gana por 2+; gana media apuesta si gana por 1 gol
        retorno_esp = (p_win_por2 * cuota) + (p_win_exact1 * ((0.50 * cuota) + 0.50))
    elif hcap == 0.75:
        # Gana total si gana o empata; pierde solo media apuesta si pierde por 1 gol
        retorno_esp = ((p_win + p_draw) * cuota) + (p_lose_exact1 * 0.50)
    else:
        return 0.0, -100.0

    ev_pct = (retorno_esp - 1.0) * 100.0
    p_eff = retorno_esp / cuota
    return p_eff, ev_pct


def analizar_partido_betano_profundo(ev, bankroll_actual):
    c = ev["cuotas"]
    c1, cx, c2 = c["1"], c["X"], c["2"]

    probs_1x2, _ = _devig_diccionario({"1": c1, "X": cx, "2": c2})
    p1_mres = probs_1x2.get("1", 0.40)
    px_mres = probs_1x2.get("X", 0.28)
    p2_mres = probs_1x2.get("2", 0.32)

    lam_l_4145 = _lambda_desde_goles_exactos_equipo(ev["goles_eq_local"])
    lam_v_4146 = _lambda_desde_goles_exactos_equipo(ev["goles_eq_visita"])

    probs_4014, _ = _devig_diccionario(ev["goles_exactos_total"])
    if probs_4014 and all(k in probs_4014 for k in ["0", "1", "2"]):
        p_u25_4014 = probs_4014["0"] + probs_4014["1"] + probs_4014["2"]
        p_o25_4014 = 1.0 - p_u25_4014
        p_o15_4014 = 1.0 - (probs_4014["0"] + probs_4014["1"])
    else:
        p_u25_4014, p_o25_4014, p_o15_4014 = 0.52, 0.48, 0.72

    if lam_l_4145 and lam_v_4146:
        lam_l, lam_v = lam_l_4145, lam_v_4146
    else:
        goles_tot = 2.00 + (p_o25_4014 * 1.30)
        peso_l = p1_mres / max(0.10, (p1_mres + p2_mres))
        lam_l = goles_tot * peso_l * 1.04
        lam_v = goles_tot * (1.0 - peso_l) * 0.96

    (
        mc_1, mc_x, mc_2, mc_l_por2, mc_v_por2,
        mc_o15, mc_o25, mc_u25, mc_btts_si, mc_btts_no
    ) = simular_motor_inspired_numba(lam_l, lam_v, 0.11, N_SIMS)

    p_real_1 = (mc_1 * 0.50) + (p1_mres * 0.50)
    p_real_x = (mc_x * 0.50) + (px_mres * 0.50)
    p_real_2 = (mc_2 * 0.50) + (p2_mres * 0.50)
    p_real_1x = p_real_1 + p_real_x
    p_real_2x = p_real_2 + p_real_x

    p_real_u25 = (mc_u25 * 0.45) + (p_u25_4014 * 0.55)
    p_real_o25 = (mc_o25 * 0.45) + (p_o25_4014 * 0.55)

    probs_btts, _ = _devig_diccionario({"si": c.get("btts_si", 0), "no": c.get("btts_no", 0)})
    p_real_btts_si = (mc_btts_si * 0.50) + (probs_btts.get("si", mc_btts_si) * 0.50)
    p_real_btts_no = (mc_btts_no * 0.50) + (probs_btts.get("no", mc_btts_no) * 0.50)

    mercados_evaluar = [
        (f"Resultado: Gana {ev['local']} (1)", p_real_1, c.get("1", 0.0), False),
        (f"Resultado: Gana {ev['visita']} (2)", p_real_2, c.get("2", 0.0), False),
        ("Goles Totales: Menos de 2.5", p_real_u25, c.get("under_25", 0.0), False),
        ("Goles Totales: Más de 2.5", p_real_o25, c.get("over_25", 0.0), False),
        ("Ambos Equipos Anotan: Sí", p_real_btts_si, c.get("btts_si", 0.0), False),
        ("Ambos Equipos Anotan: No", p_real_btts_no, c.get("btts_no", 0.0), False),
        (f"Doble Oportunidad: {ev['local']} o Empate (1X)", p_real_1x, c.get("1X", 0.0), False),
        (f"Doble Oportunidad: {ev['visita']} o Empate (2X)", p_real_2x, c.get("2X", 0.0), False),
    ]

    # Evaluar Hándicap Asiático (4144) con matemática exacta y prioridad estructural
    for nom_ha, datos_ha in ev["handicap_asiatico"].items():
        hcap = datos_ha["handicap"]
        cuota_ha = datos_ha["price"]
        es_loc = ev["local"].lower() in nom_ha.lower()

        p_w = p_real_1 if es_loc else p_real_2
        p_w2 = mc_l_por2 if es_loc else mc_v_por2
        p_l2 = mc_v_por2 if es_loc else mc_l_por2

        p_eff_ha, _ = evaluar_handicap_asiatico_exacto(hcap, cuota_ha, p_w, p_real_x, p_w2, p_l2)
        if p_eff_ha > 0.0:
            mercados_evaluar.append((f"Hándicap Asiático: {nom_ha}", p_eff_ha, cuota_ha, True))

    candidatos = []
    for nom_m, p_real, cuota_m, es_handicap in mercados_evaluar:
        if cuota_m <= 1.01 or not (CUOTA_MIN <= cuota_m <= CUOTA_MAX):
            continue

        p_implicita = 1.0 / cuota_m
        edge_pct = (p_real - p_implicita) * 100.0
        ev_pct = ((p_real * cuota_m) - 1.0) * 100.0

        # Bono de prioridad (+3.5 pts) para el Hándicap Asiático por su bajo margen en Betano (4.6%)
        bono_ha = 3.5 if es_handicap else 0.0
        score_pick = (p_real * 100.0) + (edge_pct * 2.2) + bono_ha

        if p_real >= 0.53 and edge_pct >= -0.3:
            b = cuota_m - 1.0
            kelly = max(0.015, (((b * max(p_real, p_implicita + 0.02)) - (1.0 - p_real)) / b) * KELLY_FRAC)
            stake_bruto = (bankroll_actual * min(3.50, max(1.50, kelly * 100.0))) / 100.0
            stake_soles = redondear_stake_antibaneo(stake_bruto)
            stake_pct = round((stake_soles / max(1.0, bankroll_actual)) * 100.0, 1)

            candidatos.append({
                "id": ev["id"],
                "torneo": ev["torneo"],
                "seconds_to_start": ev["seconds_to_start"],
                "partido": ev["partido"],
                "mercado": nom_m,
                "cuota": round(cuota_m, 2),
                "cuota_justa": round(1.0 / p_real, 2),
                "prob_pct": round(p_real * 100.0, 1),
                "edge_pct": round(edge_pct, 2),
                "ev_pct": round(ev_pct, 2),
                "lambda_str": f"{lam_l:.2f} vs {lam_v:.2f} (Tot: {lam_l+lam_v:.2f})",
                "top_marcadores": " | ".join(ev["top_marcadores"]),
                "proximos": " → ".join(ev["proximos_en_cola"]),
                "stake_soles": stake_soles,
                "stake_pct": stake_pct,
                "score": score_pick
            })

    if not candidatos:
        return None

    candidatos.sort(key=lambda x: -x["score"])
    return candidatos[0]


# ==============================================================================
# 7. CONTROL DESDE TELEGRAM EN LA CALLE (/g, /p, /pausa, /activar, /estado, /reset)
# ==============================================================================
def cargar_estado():
    if os.path.exists(ARCHIVO_ESTADO):
        try:
            with open(ARCHIVO_ESTADO, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "banca_actual": BANKROLL_INICIAL,
        "ganadas": 0,
        "perdidas": 0,
        "pausado": False,
        "last_update_id": 0,
        "alertados_ids": [],
        "ultimo_pick": None
    }


def guardar_estado(st):
    st["alertados_ids"] = st["alertados_ids"][-250:]
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)


def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "Markdown"}, timeout=8)
        if r.status_code != 200:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": texto.replace("*", "").replace("`", "")}, timeout=8)
    except Exception:
        pass


def procesar_comandos_telegram(st):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"offset": st.get("last_update_id", 0) + 1, "timeout": 1}
    try:
        r = requests.get(url, params=params, timeout=4)
        if r.status_code != 200:
            return
        for u in r.json().get("result", []):
            st["last_update_id"] = u["update_id"]
            txt = str(u.get("message", {}).get("text", "")).strip().lower()
            if not txt:
                continue

            if txt in ("/g", "g", "ganada"):
                up = st.get("ultimo_pick")
                if up:
                    gan = round(up["stake_soles"] * (up["cuota"] - 1.0), 2)
                    st["banca_actual"] = round(st["banca_actual"] + gan, 2)
                    st["ganadas"] += 1
                    st["ultimo_pick"] = None
                    guardar_estado(st)
                    enviar_telegram(f"✅ *¡VERDE SUMADO!* (`+S/ {gan:.2f}`)\n💰 *Banca Actual:* `S/ {st['banca_actual']:.2f}` | *Récord:* `{st['ganadas']}W - {st['perdidas']}L`")
            elif txt in ("/p", "p", "perdida"):
                up = st.get("ultimo_pick")
                if up:
                    per = up["stake_soles"]
                    st["banca_actual"] = round(st["banca_actual"] - per, 2)
                    st["perdidas"] += 1
                    st["ultimo_pick"] = None
                    guardar_estado(st)
                    enviar_telegram(f"❌ *Rojo descontado* (`-S/ {per:.2f}`)\n💰 *Banca Actual:* `S/ {st['banca_actual']:.2f}` | *Récord:* `{st['ganadas']}W - {st['perdidas']}L`")
            elif txt in ("/pausa", "pausa", "/stop"):
                st["pausado"] = True
                guardar_estado(st)
                enviar_telegram("⏸️ *Radar en PAUSA.* No recibirás alertas hasta que envíes `/activar`.")
            elif txt in ("/activar", "activar", "/start"):
                st["pausado"] = False
                guardar_estado(st)
                enviar_telegram("▶️ *Radar ACTIVADO.* Escaneando Hándicaps Asiáticos y picks de valor cada 15s.")
            elif txt in ("/estado", "estado"):
                tot = st["ganadas"] + st["perdidas"]
                wr = (st["ganadas"] / tot * 100.0) if tot > 0 else 0.0
                modo_str = "PAUSADO ⏸️" if st["pausado"] else "ACTIVO 🟢"
                enviar_telegram(
                    f"📊 *AUDITORÍA EN VIVO — MOTOR v4.2*\n"
                    f"📡 *Estado:* `{modo_str}`\n"
                    f"💰 *Banca:* `S/ {st['banca_actual']:.2f}` | *Récord:* `{st['ganadas']}W - {st['perdidas']}L ({wr:.1f}%)`"
                )
            elif txt in ("/reset", "reset"):
                st["banca_actual"] = BANKROLL_INICIAL
                st["ganadas"] = 0
                st["perdidas"] = 0
                st["pausado"] = False
                guardar_estado(st)
                enviar_telegram("🔄 *Sesión reiniciada a S/ 100.00 y radar activo.*")
    except Exception:
        pass


# ==============================================================================
# 8. BUCLE PRINCIPAL COMPATIBLE CON GITHUB ACTIONS (NUBE) Y PC LOCAL
# ==============================================================================
def iniciar_sniper_betano_v42():
    es_nube_github = os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    modo_entorno = "☁️ GITHUB CLOUD" if es_nube_github else "💻 PC LOCAL"

    print(f"🚀 Compilando motor C ({modo_entorno} — Especialista Hándicaps v4.2)...")
    _ = simular_motor_inspired_numba(1.3, 1.1, 0.11, 1000)

    cliente = BetanoVirtualClient()
    st = cargar_estado()
    guardar_estado(st)

    t_inicio_bloque = time.time()
    ciclo = 0

    while True:
        # En GitHub Actions, cerrar limpiamente a los 52 min para autoguardar el estado en el repo
        if es_nube_github and (time.time() - t_inicio_bloque) >= (DURACION_BLOQUE_NUBE_MIN * 60):
            print("⏱️ Fin del bloque horario en GitHub Actions. Guardando estado para el siguiente relevo...")
            guardar_estado(st)
            break

        ciclo += 1
        t0 = time.time()
        st = cargar_estado()
        procesar_comandos_telegram(st)

        if st["banca_actual"] >= STOP_WIN_SOLES and not st["pausado"]:
            st["pausado"] = True
            guardar_estado(st)
            enviar_telegram(f"🏆 *¡STOP-WIN ALCANZADO! (S/ {st['banca_actual']:.2f})* Sesión en pausa para proteger tu +15%. Envía `/reset` o `/activar` para continuar.")
        elif st["banca_actual"] <= STOP_LOSS_SOLES and not st["pausado"]:
            st["pausado"] = True
            guardar_estado(st)
            enviar_telegram(f"🛑 *STOP-LOSS ACTIVADO (S/ {st['banca_actual']:.2f})* Cortacircuitos activo. Envía `/reset` para reiniciar.")

        if not st["pausado"]:
            eventos = cliente.escanear_4_torneos_betano()
            picks_ciclo = []

            for ev in eventos:
                if ev["id"] in st["alertados_ids"]:
                    continue
                if ev["seconds_to_start"] < SEGUNDOS_MIN_PARA_APOSTAR:
                    continue
                pk = analizar_partido_betano_profundo(ev, st["banca_actual"])
                if pk:
                    picks_ciclo.append(pk)

            ms = (time.time() - t0) * 1000.0
            hora_pe = datetime.now(ZONA_PERU).strftime("%H:%M:%S")
            print(f"[{hora_pe} | Ciclo #{ciclo:03d} | {ms:.0f}ms] ✅ {len(eventos)} torneos leídos | {len(picks_ciclo)} oportunidades detectadas.")

            if picks_ciclo:
                picks_ciclo.sort(key=lambda x: -x["score"])
                mejor = picks_ciclo[0]

                st["alertados_ids"].append(mejor["id"])
                st["ultimo_pick"] = mejor
                guardar_estado(st)

                alerta = (
                    f"🚨 *SEÑAL EN TIEMPO REAL — BETANO VIRTUALS ({hora_pe})* 🚨\n"
                    f"🏟️ *{mejor['torneo']}* _(Inicia en {mejor['seconds_to_start']} seg)_\n"
                    f"⚽ *{mejor['partido']}*\n\n"
                    f"👉 *PICK RECOMENDADO:* `{mejor['mercado']}`\n"
                    f"🧡 *Cuota Betano:* `@{mejor['cuota']:.2f}` _(Prob. Real: {mejor['prob_pct']:.1f}% | Justa: @{mejor['cuota_justa']:.2f})_\n"
                    f"📐 *Goles Programados (4145/4146):* `{mejor['lambda_str']}`\n"
                    f"🎯 *Top Marcadores RNG (4141):* `{mejor['top_marcadores']}`\n"
                    f"💵 *Stake Seguro:* `S/ {mejor['stake_soles']:.2f}` _({mejor['stake_pct']:.1f}% de tu banca)_\n\n"
                    f"⏭️ *Próximos en cola:* _{mejor['proximos']}_\n"
                    f"📲 _Comandos: `/g` (Ganada) | `/p` (Perdida) | `/pausa` (Silenciar)_"
                )
                enviar_telegram(alerta)
                print(f"   📲 ¡ALERTA ENVIADA! -> [{mejor['torneo']}] {mejor['partido']} | {mejor['mercado']} @{mejor['cuota']:.2f}")

        time.sleep(SEGUNDOS_ESCANEO)


if __name__ == "__main__":
    iniciar_sniper_betano_v42()
