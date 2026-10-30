import time
import io
import os
import re
import json
import codecs
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from numba import njit, prange

# ==============================================================================
# 1. CONFIGURACION INSTITUCIONAL — IABET PRO 2.5 (B2B + UNDERSTAT xG + SAAS)
# ==============================================================================
ODDS_API_KEYS = [
    os.getenv("THE_ODDS_API_KEY", ""),
    "a13d27d9c5cc3ea988f72ba4ff74c56f",
    "105d8edef7a4869116a817c106dc86a8"
]
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

# CONTROL DE MONETIZACION SAAS:
# "FREE_TRIAL" = Todo desbloqueado (Fase Fidelizacion de clientes)
# "VIP_PAYWALL" = Bloquea los Picks TIER S y pide Clave VIP / Suscripcion
MODO_SAAS = "FREE_TRIAL"
CLAVE_ACCESO_VIP = "IABET2026"
LINK_SUSCRIPCION_VIP = "https://t.me/+tu_enlace_o_stripe_checkout"

BANKROLL_BASE = 100.0
KELLY_FRAC = 0.22
N_SIMS = 1_000_000

CUOTA_MIN_RENTABLE = 1.45
CUOTA_MAX_RENTABLE = 2.25

ARCHIVO_AUDITORIA = "auditoria_iabet.json"
ARCHIVO_WEB = "index.html"
ARCHIVO_CACHE_ODDS = "cache_odds_api.json"
TIEMPO_CACHE_HORAS = 1.0

TZ_PERU = timezone(timedelta(hours=-5))

# Slug ESPN -> (Nombre, Codigo CSV, Slug OddsAPI, Slug Kambi B2B, Slug Understat, Base Tarj, Base Faltas, Base SOT, Base Corners)
LIGAS_CONFIG = {
    "esp.1": ("LaLiga", "SP1", "soccer_spain_la_liga", "spain/la_liga", "La_liga", 4.7, 25.0, 8.4, 9.4),
    "eng.1": ("Premier League", "E0", "soccer_epl", "england/premier_league", "EPL", 3.9, 21.6, 9.3, 10.4),
    "ita.1": ("Serie A", "I1", "soccer_italy_serie_a", "italy/serie_a", "Serie_A", 4.3, 24.2, 8.5, 9.5),
    "ger.1": ("Bundesliga", "D1", "soccer_germany_bundesliga", "germany/bundesliga", "Bundesliga", 3.9, 22.2, 9.5, 9.8),
    "fra.1": ("Ligue 1", "F1", "soccer_france_ligue_one", "france/ligue_1", "Ligue_1", 3.9, 23.6, 8.6, 9.3),
    "ned.1": ("Eredivisie", "N1", "soccer_netherlands_eredivisie", "netherlands/eredivisie", None, 3.2, 20.2, 9.6, 10.2),
    "por.1": ("Primeira Liga", "P1", "soccer_portugal_primeira_liga", "portugal/primeira_liga", None, 5.1, 26.4, 8.4, 9.8),
    "bra.1": ("Brasileirao", "BRA", "soccer_brazil_campeonato", "brazil/serie_a", None, 5.0, 27.0, 8.8, 10.3),
    "arg.1": ("Liga Argentina", "ARG", "soccer_argentina_primera_division", "argentina/liga_profesional", None, 5.1, 27.4, 8.1, 9.1),
    "mex.1": ("Liga MX", "MEX", "soccer_mexico_ligamx", "mexico/liga_mx", None, 4.5, 24.2, 8.8, 9.7),
    "per.1": ("Liga 1 Peru", None, None, "peru/liga_1", None, 5.2, 27.5, 8.4, 9.2),
    "conmebol.libertadores": ("Copa Libertadores", None, "soccer_conmebol_copa_libertadores", "copa_libertadores", None, 5.2, 27.2, 8.5, 9.5),
    "conmebol.sudamericana": ("Copa Sudamericana", None, "soccer_conmebol_copa_sudamericana", "copa_sudamericana", None, 4.9, 26.5, 8.3, 9.2),
    "uefa.champions": ("Champions League", None, "soccer_uefa_champs_league", "champions_league", None, 4.0, 22.4, 9.5, 9.9),
    "uefa.europa": ("Europa League", None, "soccer_uefa_europa_league", "europa_league", None, 4.3, 23.6, 9.0, 9.6)
}

MAPEO_EQUIPOS_CSV = {
    "paris saint germain": "paris sg",
    "psg": "paris sg",
    "atletico madrid": "ath madrid",
    "athletic club": "ath bilbao",
    "athletic bilbao": "ath bilbao",
    "real betis": "betis",
    "real sociedad": "sociedad",
    "celta vigo": "celta",
    "rc celta": "celta",
    "rayo vallecano": "vallecano",
    "deportivo alaves": "alaves",
    "espanyol": "espanol",
    "manchester united": "man united",
    "manchester city": "man city",
    "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle",
    "west ham united": "west ham",
    "tottenham hotspur": "tottenham",
    "wolverhampton wanderers": "wolves",
    "brighton hove albion": "brighton",
    "leicester city": "leicester",
    "ipswich town": "ipswich",
    "inter milan": "inter",
    "internazionale": "inter",
    "ac milan": "milan",
    "hellas verona": "verona",
    "como 1907": "como",
    "bayern munich": "bayern munich",
    "bayer leverkusen": "leverkusen",
    "borussia dortmund": "dortmund",
    "borussia monchengladbach": "m'gladbach",
    "eintracht frankfurt": "ein frankfurt",
    "rb leipzig": "rb leipzig",
    "vfb stuttgart": "stuttgart",
    "mainz 05": "mainz",
    "stade brestois": "brest",
    "stade rennais": "rennes",
    "saint etienne": "st etienne",
    "olympique marseille": "marseille",
    "olympique lyonnais": "lyon",
    "as monaco": "monaco",
    "sporting cp": "sp lisbon",
    "sporting lisbon": "sp lisbon",
    "vitoria guimaraes": "guimaraes",
    "sc braga": "sp braga",
    "psv eindhoven": "psv",
    "az alkmaar": "az alkmaar",
    "atletico mineiro": "atletico-mg",
    "athletico paranaense": "athletico-pr"
}

EQUIPOS_ALTA_POSESION = [
    "juventus", "inter", "milan", "napoli", "real madrid", "barcelona",
    "bayern", "leverkusen", "manchester city", "man city", "arsenal", "liverpool",
    "chelsea", "tottenham", "psg", "paris", "benfica", "sporting", "flamengo", "palmeiras"
]

DB_EQUIPOS_LATAM = {
    "universitario": {"gf": 1.85, "gc": 0.65, "xg": 1.92, "xga": 0.70, "f_com": 12.5, "sot": 5.4, "cor": 5.6, "tarj": 2.2},
    "alianza lima": {"gf": 1.70, "gc": 0.75, "xg": 1.78, "xga": 0.78, "f_com": 12.4, "sot": 5.1, "cor": 5.3, "tarj": 2.3},
    "sporting cristal": {"gf": 2.05, "gc": 1.05, "xg": 2.10, "xga": 1.02, "f_com": 11.2, "sot": 5.8, "cor": 5.9, "tarj": 2.0},
    "melgar": {"gf": 1.80, "gc": 0.85, "xg": 1.88, "xga": 0.86, "f_com": 12.6, "sot": 5.5, "cor": 5.4, "tarj": 2.3},
    "cusco": {"gf": 1.65, "gc": 1.05, "xg": 1.62, "xga": 1.08, "f_com": 13.4, "sot": 4.8, "cor": 4.9, "tarj": 2.5},
    "cienciano": {"gf": 1.50, "gc": 1.15, "xg": 1.48, "xga": 1.18, "f_com": 13.8, "sot": 4.6, "cor": 4.7, "tarj": 2.6},
    "river plate": {"gf": 1.80, "gc": 0.80, "xg": 1.95, "xga": 0.78, "f_com": 11.8, "sot": 5.5, "cor": 5.8, "tarj": 2.3},
    "boca juniors": {"gf": 1.45, "gc": 0.95, "xg": 1.52, "xga": 0.96, "f_com": 13.2, "sot": 4.4, "cor": 5.1, "tarj": 2.7},
    "racing": {"gf": 1.75, "gc": 1.05, "xg": 1.82, "xga": 1.04, "f_com": 12.9, "sot": 5.1, "cor": 5.4, "tarj": 2.5},
    "flamengo": {"gf": 1.85, "gc": 0.90, "xg": 1.98, "xga": 0.88, "f_com": 12.2, "sot": 5.6, "cor": 5.9, "tarj": 2.2},
    "palmeiras": {"gf": 1.80, "gc": 0.80, "xg": 1.94, "xga": 0.82, "f_com": 12.4, "sot": 5.5, "cor": 5.8, "tarj": 2.3},
    "botafogo": {"gf": 1.75, "gc": 0.85, "xg": 1.80, "xga": 0.86, "f_com": 12.6, "sot": 5.3, "cor": 5.5, "tarj": 2.4}
}

HEADERS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*"
}

# ==============================================================================
# 2. MOTOR MONTE CARLO JIT (NUMBA) ALIMENTADO CON xG REAL
# ==============================================================================
@njit(parallel=True)
def simular_montecarlo_xg(
    l_loc, l_vis, f_esp, linea_f, sot_esp, linea_sot,
    cor_esp, linea_cor, tarj_esp, linea_tarj, n_sims
):
    vic_loc, empate, vic_vis = 0, 0, 0
    over_15, over_25, under_25, under_35 = 0, 0, 0, 0
    btts_si = 0
    f_over, f_under = 0, 0
    sot_over, sot_under = 0, 0
    cor_over, cor_under = 0, 0
    tarj_over, tarj_under = 0, 0
    cov = 0.08

    for i in prange(n_sims):
        r = np.random.poisson(cov)
        gl = np.random.poisson(max(0.05, l_loc - cov)) + r
        gv = np.random.poisson(max(0.05, l_vis - cov)) + r

        if gl == 0 and gv == 0 and np.random.random() < 0.05:
            gl, gv = 0, 0

        tot_g = gl + gv
        if gl > gv: vic_loc += 1
        elif gl == gv: empate += 1
        else: vic_vis += 1

        if tot_g > 1.5: over_15 += 1
        if tot_g > 2.5: over_25 += 1
        else: under_25 += 1
        if tot_g < 3.5: under_35 += 1
        if gl > 0 and gv > 0: btts_si += 1

        fric = 1.03 if abs(gl - gv) <= 1 else 0.94
        ftot = max(8.0, np.random.normal(f_esp * fric, 3.10))
        if ftot > linea_f: f_over += 1
        else: f_under += 1

        sot_sim = max(1.0, np.random.normal(sot_esp, 2.15))
        if sot_sim > linea_sot: sot_over += 1
        else: sot_under += 1

        cor_sim = max(2.0, np.random.normal(cor_esp, 2.50))
        if cor_sim > linea_cor: cor_over += 1
        else: cor_under += 1

        mod_f = 1.0 + ((ftot / max(18.0, f_esp)) - 1.0) * 0.25
        t_sim = max(0.0, np.random.normal(tarj_esp * mod_f, 1.20))
        if t_sim > linea_tarj: tarj_over += 1
        else: tarj_under += 1

    return (
        vic_loc / n_sims,            # 0: 1
        empate / n_sims,             # 1: X
        vic_vis / n_sims,            # 2: 2
        (vic_loc + empate) / n_sims, # 3: 1X
        (vic_vis + empate) / n_sims, # 4: X2
        over_15 / n_sims,            # 5: Over 1.5
        over_25 / n_sims,            # 6: Over 2.5
        under_25 / n_sims,           # 7: Under 2.5
        under_35 / n_sims,           # 8: Under 3.5
        btts_si / n_sims,            # 9: BTTS Si
        f_over / n_sims,             # 10: Faltas Over
        f_under / n_sims,            # 11: Faltas Under
        sot_over / n_sims,           # 12: SOT Over
        sot_under / n_sims,          # 13: SOT Under
        cor_over / n_sims,           # 14: Corners Over
        cor_under / n_sims,          # 15: Corners Under
        tarj_over / n_sims,          # 16: Tarjetas Over
        tarj_under / n_sims          # 17: Tarjetas Under
    )

def calcular_marcadores_exactos(l_loc, l_vis):
    from math import exp, factorial
    probs = []
    for g1 in range(4):
        for g2 in range(4):
            p1 = (exp(-l_loc) * (l_loc ** g1)) / factorial(g1)
            p2 = (exp(-l_vis) * (l_vis ** g2)) / factorial(g2)
            probs.append((f"{g1}-{g2}", p1 * p2 * 100.0))
    probs.sort(key=lambda x: x[1], reverse=True)
    return probs[:3]

# ==============================================================================
# 3. PILAR 1 & 2: SCRAPER B2B DIRECTO (KAMBI CDN) + SCRAPER xG REAL (UNDERSTAT)
# ==============================================================================
def limpiar_texto(txt):
    if not txt: return ""
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u", "-": " ", "&": " "}
    t = str(txt).lower().strip()
    for k, v in reemplazos.items():
        t = t.replace(k, v)
    basura = ["fc", "cf", "ac", "sc", "as", "ud", "cd", "rcd", "club", "deportivo", "1909", "1913", "1899", "1907", "cfc", "bc", "afc"]
    palabras = [p for p in t.split() if p not in basura]
    limpio = " ".join(palabras) if palabras else t
    for k_espn, v_csv in MAPEO_EQUIPOS_CSV.items():
        if k_espn in limpio:
            return v_csv
    return limpio

def clave_cruce(loc, vis):
    return f"{limpiar_texto(loc)[:6]}_{limpiar_texto(vis)[:6]}"

def dec(c):
    if c is None: return 0.0
    try:
        c = float(c)
        if c >= 100: return (c / 100.0) + 1.0
        elif c <= -100: return (100.0 / abs(c)) + 1.0
        return c
    except Exception:
        return 0.0

def descargar_xg_understat():
    """
    PILAR 2: Extrae el xG (Expected Goals) y xGA real desde Understat
    para detectar equipos con 'mala racha' o sobre-rendimiento.
    """
    ligas_u = ["La_liga", "EPL", "Serie_A", "Bundesliga", "Ligue_1"]
    temporadas = ["2025", "2024"]
    mapa_xg = {}

    def fetch_u(liga_slug):
        datos_liga = {}
        for yr in temporadas:
            url = f"https://understat.com/league/{liga_slug}/{yr}"
            try:
                r = requests.get(url, headers=HEADERS_HTTP, timeout=7)
                if r.status_code == 200 and "teamsData" in r.text:
                    m = re.search(r"teamsData\s*=\s*JSON\.parse\('([^']+)'\)", r.text)
                    if m:
                        raw_str = codecs.decode(m.group(1), "unicode_escape")
                        obj = json.loads(raw_str)
                        for _, t_info in obj.items():
                            t_title = limpiar_texto(t_info.get("title", ""))
                            hist = t_info.get("history", [])[-15:]
                            if len(hist) >= 5:
                                xg_l = [float(h.get("xG", 1.3)) for h in hist]
                                xga_l = [float(h.get("xGA", 1.2)) for h in hist]
                                g_l = [float(h.get("scored", 1.3)) for h in hist]
                                datos_liga[t_title] = {
                                    "xg": float(np.mean(xg_l[-5:]) * 0.55 + np.mean(xg_l) * 0.45),
                                    "xga": float(np.mean(xga_l[-5:]) * 0.55 + np.mean(xga_l) * 0.45),
                                    "luck_diff": float(np.mean(g_l) - np.mean(xg_l))
                                }
                        if datos_liga:
                            break
            except Exception:
                continue
        return datos_liga

    with ThreadPoolExecutor(max_workers=5) as ex:
        for d in ex.map(fetch_u, ligas_u):
            mapa_xg.update(d)

    return mapa_xg

def ingesta_b2b_kambi_directo():
    """
    PILAR 1: Scraper Asincrono B2B directo al CDN de Kambi (Proveedor de Betsson / Betsafe / Latam)
    100% gratuito e ilimitado (Sin gastar creditos de API).
    """
    rutas_kambi = list({v[3] for v in LIGAS_CONFIG.values() if v[3]})
    cuotas_b2b = {}

    def fetch_kambi(ruta):
        url = f"https://eu-offering-api.kambicdn.com/offering/v2018/ub/listView/football/{ruta}/all/all/matches.json?lang=es_ES&market=ES&useCombined=true"
        hallados = {}
        try:
            r = requests.get(url, headers=HEADERS_HTTP, timeout=6)
            if r.status_code == 200:
                for ev_wrap in r.json().get("events", []):
                    ev = ev_wrap.get("event", {})
                    loc = ev.get("homeName", "")
                    vis = ev.get("awayName", "")
                    if not loc or not vis: continue
                    clv = clave_cruce(loc, vis)
                    c1, cx, c2, co25, cu25, btts_s = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

                    for bo in ev_wrap.get("betOffers", []):
                        crit = bo.get("criterion", {}).get("label", "").lower()
                        bo_type = bo.get("betOfferType", {}).get("name", "").lower()
                        outcomes = bo.get("outcomes", [])
                        if "match" in bo_type or "1x2" in crit or "resultado" in crit:
                            for o in outcomes:
                                pr = float(o.get("odds", 0)) / 1000.0
                                if o.get("type") == "OT_ONE": c1 = pr
                                elif o.get("type") == "OT_CROSS": cx = pr
                                elif o.get("type") == "OT_TWO": c2 = pr
                        elif "total" in crit or "goles" in crit:
                            for o in outcomes:
                                line = float(o.get("line", 0)) / 1000.0
                                if line == 2.5:
                                    pr = float(o.get("odds", 0)) / 1000.0
                                    if o.get("type") == "OT_OVER": co25 = pr
                                    elif o.get("type") == "OT_UNDER": cu25 = pr
                        elif "ambos" in crit or "both teams" in crit:
                            for o in outcomes:
                                pr = float(o.get("odds", 0)) / 1000.0
                                if o.get("type") == "OT_YES": btts_s = pr

                    if c1 > 1.05 and c2 > 1.05:
                        hallados[clv] = {
                            "c1": round(c1, 2), "casa1": "Kambi B2B (Betsson/Direct)",
                            "cx": round(cx if cx > 1.05 else 3.30, 2), "casax": "Kambi B2B",
                            "c2": round(c2, 2), "casa2": "Kambi B2B (Betsson/Direct)",
                            "co25": round(co25, 2), "casao25": "Kambi B2B",
                            "cu25": round(cu25, 2), "casau25": "Kambi B2B",
                            "cbtts": round(btts_s, 2),
                            "med1": c1, "medx": cx if cx > 1.05 else 3.30, "med2": c2
                        }
        except Exception:
            pass
        return hallados

    with ThreadPoolExecutor(max_workers=8) as ex:
        for h in ex.map(fetch_kambi, rutas_kambi):
            cuotas_b2b.update(h)

    return cuotas_b2b

def obtener_cuotas_hibridas(deportes_activos_hoy):
    ahora_ts = time.time()
    # 1. Ingesta B2B Directa (Ilimitada y en vivo)
    mapa_cuotas = ingesta_b2b_kambi_directo()
    creditos = "B2B Direct + API"

    if os.path.exists(ARCHIVO_CACHE_ODDS):
        try:
            with open(ARCHIVO_CACHE_ODDS, "r", encoding="utf-8") as f:
                cache = json.load(f)
            edad_horas = (ahora_ts - float(cache.get("timestamp", 0))) / 3600.0
            if edad_horas < TIEMPO_CACHE_HORAS and len(cache.get("partidos", {})) >= 8:
                guardados = cache.get("partidos", {})
                guardados.update(mapa_cuotas)
                return guardados, f"{cache.get('creditos_restantes', '470')} (+B2B)"
            for k_old, v_old in cache.get("partidos", {}).items():
                if k_old not in mapa_cuotas:
                    mapa_cuotas[k_old] = v_old
        except Exception:
            pass

    keys_validas = [k for k in ODDS_API_KEYS if k and len(k) > 15]
    if not keys_validas:
        return mapa_cuotas, creditos

    lista_deportes = [d for d in deportes_activos_hoy if d]
    if not lista_deportes:
        lista_deportes = [v[2] for v in LIGAS_CONFIG.values() if v[2]]

    for cod_dep in lista_deportes:
        for api_k in keys_validas:
            url = f"https://api.the-odds-api.com/v4/sports/{cod_dep}/odds/"
            params = {
                "apiKey": api_k,
                "regions": "eu,uk",
                "markets": "h2h,totals",
                "oddsFormat": "decimal"
            }
            try:
                r = requests.get(url, params=params, timeout=8)
                if r.status_code in [401, 429]:
                    continue
                if "x-requests-remaining" in r.headers:
                    creditos = f"{r.headers['x-requests-remaining']} (+B2B)"
                if r.status_code == 200:
                    for ev in r.json():
                        loc, vis = ev.get("home_team", ""), ev.get("away_team", "")
                        if not loc or not vis: continue
                        clv = clave_cruce(loc, vis)
                        c1, cx, c2, co25, cu25 = [], [], [], [], []
                        best_1, best_x, best_2 = (0.0, ""), (0.0, ""), (0.0, "")
                        best_o25, best_u25 = (0.0, ""), (0.0, "")

                        for bm in ev.get("bookmakers", []):
                            title = bm.get("title", "Market")
                            for m in bm.get("markets", []):
                                if m.get("key") == "h2h":
                                    for o in m.get("outcomes", []):
                                        pr = float(o.get("price", 0))
                                        if pr <= 1.02: continue
                                        if o.get("name") == loc:
                                            c1.append(pr)
                                            if pr > best_1[0]: best_1 = (pr, title)
                                        elif o.get("name") == vis:
                                            c2.append(pr)
                                            if pr > best_2[0]: best_2 = (pr, title)
                                        elif o.get("name") == "Draw":
                                            cx.append(pr)
                                            if pr > best_x[0]: best_x = (pr, title)
                                elif m.get("key") == "totals":
                                    for o in m.get("outcomes", []):
                                        if float(o.get("point", 0)) == 2.5:
                                            pr = float(o.get("price", 0))
                                            if o.get("name") == "Over":
                                                co25.append(pr)
                                                if pr > best_o25[0]: best_o25 = (pr, title)
                                            elif o.get("name") == "Under":
                                                cu25.append(pr)
                                                if pr > best_u25[0]: best_u25 = (pr, title)
                        if c1 and c2:
                            med_c1 = float(np.median(c1))
                            med_cx = float(np.median(cx)) if cx else 3.30
                            med_c2 = float(np.median(c2))
                            med_o25 = float(np.median(co25)) if co25 else 0.0
                            med_u25 = float(np.median(cu25)) if cu25 else 0.0
                            mapa_cuotas[clv] = {
                                "c1": round(max(med_c1, best_1[0] * 0.98), 2), "casa1": best_1[1] or "Smarkets/Pinnacle",
                                "cx": round(med_cx, 2), "casax": best_x[1] or "Smarkets/Pinnacle",
                                "c2": round(max(med_c2, best_2[0] * 0.98), 2), "casa2": best_2[1] or "Smarkets/Pinnacle",
                                "co25": round(med_o25, 2), "casao25": best_o25[1] or "Pinnacle/Betano",
                                "cu25": round(med_u25, 2), "casau25": best_u25[1] or "Pinnacle/Betano",
                                "med1": med_c1, "medx": med_cx, "med2": med_c2
                            }
                    break
            except Exception:
                continue

    if mapa_cuotas:
        try:
            with open(ARCHIVO_CACHE_ODDS, "w", encoding="utf-8") as f:
                json.dump({"timestamp": ahora_ts, "creditos_restantes": creditos, "partidos": mapa_cuotas}, f)
        except Exception:
            pass
    return mapa_cuotas, creditos

def bajar_csv_liga(args):
    nom_liga, cod_csv = args
    if not cod_csv: return nom_liga, pd.DataFrame()
    urls = [
        f"https://www.football-data.co.uk/mmz4281/2526/{cod_csv}.csv",
        f"https://www.football-data.co.uk/mmz4281/2425/{cod_csv}.csv",
        f"https://www.football-data.co.uk/new/{cod_csv}.csv"
    ]
    frames = []
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS_HTTP, timeout=8)
            if r.status_code == 200 and len(r.text) > 500:
                df = pd.read_csv(io.StringIO(r.text))
                df = df.rename(columns={'HG': 'FTHG', 'AG': 'FTAG', 'Home': 'HomeTeam', 'Away': 'AwayTeam'})
                if {'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}.issubset(df.columns):
                    frames.append(df)
        except Exception:
            pass
    if frames:
        return nom_liga, pd.concat(frames, ignore_index=True).tail(450)
    return nom_liga, pd.DataFrame()

def extraer_perfil_con_xg(df, equipo, mapa_understat, b_falt, b_sot, b_cor, b_tarj, es_local=True):
    eq_limpio = limpiar_texto(equipo)
    u_info = mapa_understat.get(eq_limpio)

    for k, v in DB_EQUIPOS_LATAM.items():
        if k in eq_limpio:
            return {
                "gf": v["gf"] * (1.08 if es_local else 0.92), "gc": v["gc"],
                "xg": v["xg"] * (1.08 if es_local else 0.92), "xga": v["xga"],
                "luck_diff": v["gf"] - v["xg"],
                "f_propia": v["f_com"], "sot_propio": v["sot"],
                "cor_propio": v["cor"], "tarj_propia": v["tarj"],
                "serie_f": [v["f_com"]] * 15, "serie_cor": [v["cor"]] * 15,
                "serie_sot": [v["sot"]] * 15, "serie_tarj": [v["tarj"]] * 15,
                "verificado": True, "fuente_xg": "xG Latam Pro"
            }

    if df is None or df.empty:
        if u_info:
            return {
                "gf": u_info["xg"] * (1.07 if es_local else 0.93), "gc": u_info["xga"],
                "xg": u_info["xg"] * (1.07 if es_local else 0.93), "xga": u_info["xga"],
                "luck_diff": u_info["luck_diff"],
                "f_propia": b_falt / 2.0, "sot_propio": b_sot / 2.0,
                "cor_propio": b_cor / 2.0, "tarj_propia": b_tarj / 2.0,
                "serie_f": [], "serie_cor": [], "serie_sot": [], "serie_tarj": [],
                "verificado": False, "fuente_xg": "Understat xG"
            }
        return None

    col_h = df['HomeTeam'].astype(str).apply(limpiar_texto)
    col_a = df['AwayTeam'].astype(str).apply(limpiar_texto)

    partidos = df[(col_h == eq_limpio) | (col_a == eq_limpio)].tail(20)
    if len(partidos) < 5:
        tokens = [t for t in eq_limpio.split() if len(t) >= 3]
        patron = tokens[0] if tokens else eq_limpio[:5]
        partidos = df[col_h.str.contains(patron, na=False) | col_a.str.contains(patron, na=False)].tail(20)

    if len(partidos) < 5:
        return None

    gf, gc, xg_synth, xga_synth = [], [], [], []
    f_p, sot_p, cor_p, tarj_p = [], [], [], []
    ok_f = {'HF', 'AF'}.issubset(partidos.columns)
    ok_s = {'HST', 'AST'}.issubset(partidos.columns)
    ok_sh = {'HS', 'AS'}.issubset(partidos.columns)
    ok_c = {'HC', 'AC'}.issubset(partidos.columns)
    ok_t = {'HY', 'AY'}.issubset(partidos.columns)

    for _, row in partidos.iterrows():
        h_name = limpiar_texto(str(row['HomeTeam']))
        fue_local = (eq_limpio in h_name) or (h_name in eq_limpio)
        g_favor = float(row['FTHG'] if fue_local else row['FTAG'])
        g_contra = float(row['FTAG'] if fue_local else row['FTHG'])
        gf.append(g_favor)
        gc.append(g_contra)

        if ok_f and pd.notna(row['HF']) and pd.notna(row['AF']):
            f_p.append(float(row['HF'] if fue_local else row['AF']))
        if ok_s and pd.notna(row['HST']) and pd.notna(row['AST']):
            s_fav = float(row['HST'] if fue_local else row['AST'])
            s_con = float(row['AST'] if fue_local else row['HST'])
            sot_p.append(s_fav)
            if ok_sh and pd.notna(row['HS']) and pd.notna(row['AS']):
                sh_fav = max(s_fav, float(row['HS'] if fue_local else row['AS']))
                sh_con = max(s_con, float(row['AS'] if fue_local else row['HS']))
                # Modelo Shot-Quality xG cuando no es liga Understat
                xg_synth.append((s_fav * 0.31) + ((sh_fav - s_fav) * 0.07))
                xga_synth.append((s_con * 0.31) + ((sh_con - s_con) * 0.07))
            else:
                xg_synth.append(s_fav * 0.34)
                xga_synth.append(s_con * 0.34)
        if ok_c and pd.notna(row['HC']) and pd.notna(row['AC']):
            cor_p.append(float(row['HC'] if fue_local else row['AC']))
        if ok_t and pd.notna(row['HY']) and pd.notna(row['AY']):
            tarj_p.append(float(row['HY'] if fue_local else row['AY']))

    pond = lambda arr, def_v: (np.mean(arr[-5:]) * 0.45 + np.mean(arr[-10:]) * 0.35 + np.mean(arr) * 0.20) if len(arr) >= 5 else def_v
    gf_val = pond(gf, 1.32)
    gc_val = pond(gc, 1.18)

    if u_info:
        xg_val = u_info["xg"]
        xga_val = u_info["xga"]
        luck_d = u_info["luck_diff"]
        f_xg = "Understat xG Real"
    else:
        xg_val = pond(xg_synth, gf_val)
        xga_val = pond(xga_synth, gc_val)
        luck_d = gf_val - xg_val
        f_xg = "Shot-Quality xG"

    # Si un equipo tiene mala racha (genera mas xG que goles reales), ponderamos 65% su xG real
    motor_ataque = ((xg_val * 0.65) + (gf_val * 0.35)) * (1.07 if es_local else 0.93)
    motor_defensa = (xga_val * 0.65) + (gc_val * 0.35)

    return {
        "gf": motor_ataque, "gc": motor_defensa,
        "xg": xg_val * (1.07 if es_local else 0.93), "xga": xga_val,
        "luck_diff": luck_d,
        "f_propia": pond(f_p, b_falt / 2.0),
        "sot_propio": pond(sot_p, b_sot / 2.0),
        "cor_propio": pond(cor_p, b_cor / 2.0),
        "tarj_propia": pond(tarj_p, b_tarj / 2.0),
        "serie_f": f_p, "serie_cor": cor_p, "serie_sot": sot_p, "serie_tarj": tarj_p,
        "verificado": (len(cor_p) >= 5 or len(sot_p) >= 5),
        "fuente_xg": f_xg
    }

def metricas_ventanas_l5_l10_l20(serie_a, serie_b, factor, linea, modo="OVER"):
    n = min(len(serie_a), len(serie_b))
    if n < 5:
        return 0.0, 0.0, 0.0
    tot = (np.array(serie_a[-n:]) + np.array(serie_b[-n:])) * factor
    if modo == "OVER":
        l5 = float(np.mean(tot[-5:] > linea) * 100.0)
        l10 = float(np.mean(tot[-10:] > linea) * 100.0)
        l20 = float(np.mean(tot > linea) * 100.0)
    else:
        l5 = float(np.mean(tot[-5:] < linea) * 100.0)
        l10 = float(np.mean(tot[-10:] < linea) * 100.0)
        l20 = float(np.mean(tot < linea) * 100.0)
    consenso = (l5 * 0.40) + (l10 * 0.35) + (l20 * 0.25)
    return l5, l10, consenso

# ==============================================================================
# 4. AUDITOR TRANSPARENTE (GANADO / PERDIDO) CON STATS REALES DE ESPN
# ==============================================================================
def cargar_y_sincronizar_auditoria():
    estructura_base = {
        "bankroll_inicial": BANKROLL_BASE,
        "bankroll_actual": BANKROLL_BASE,
        "ganancia_neta": 0.0,
        "roi_pct": 0.0,
        "win_rate": 85.0,
        "ganados": 0,
        "perdidos": 0,
        "racha": 0,
        "historial": []
    }
    if os.path.exists(ARCHIVO_AUDITORIA):
        try:
            with open(ARCHIVO_AUDITORIA, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                res_prev = raw.get("resumen", {}) if isinstance(raw.get("resumen"), dict) else {}
                estructura_base["bankroll_inicial"] = float(raw.get("bankroll_inicial", res_prev.get("bankroll_inicial", BANKROLL_BASE)) or BANKROLL_BASE)
                hist_raw = raw.get("historial", [])
                if isinstance(hist_raw, list):
                    for item in hist_raw:
                        if not isinstance(item, dict): continue
                        if item.get("estado") == "PENDIENTE" and float(item.get("cuota", 0)) < 1.40:
                            continue
                        if "partido" not in item and ("local" in item and "visita" in item):
                            item["partido"] = f"{item['local']} vs {item['visita']}"
                        if "clave_partido" not in item and "partido" in item:
                            partes = str(item["partido"]).split(" vs ")
                            if len(partes) == 2:
                                item["clave_partido"] = clave_cruce(partes[0], partes[1])
                        estructura_base["historial"].append(item)
        except Exception as e:
            print(f"Aviso en migracion: {e}")
    return estructura_base

def obtener_stats_finales_espn(event_id):
    if not event_id: return {}
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/summary?event={event_id}"
    try:
        r = requests.get(url, headers=HEADERS_HTTP, timeout=6)
        if r.status_code == 200:
            teams = r.json().get("boxscore", {}).get("teams", [])
            tot = {"fouls": 0.0, "sot": 0.0, "corners": 0.0, "cards": 0.0}
            if len(teams) == 2:
                for t in teams:
                    for st in t.get("statistics", []):
                        nam = st.get("name", "")
                        try:
                            val = float(st.get("displayValue", "0") or 0)
                        except Exception:
                            val = 0.0
                        if nam == "foulsCommitted": tot["fouls"] += val
                        elif nam in ["shotsOnTarget", "shotsOnGoal"]: tot["sot"] += val
                        elif nam in ["wonCorners", "cornerKicks"]: tot["corners"] += val
                        elif nam in ["yellowCards", "redCards"]: tot["cards"] += val
            return tot
    except Exception:
        pass
    return {}

def auditar_partidos_con_espn(auditoria, resultados_espn):
    bank_ini = float(auditoria.get("bankroll_inicial", BANKROLL_BASE) or BANKROLL_BASE)
    auditoria["bankroll_inicial"] = bank_ini

    for pick in auditoria.get("historial", []):
        try:
            if pick.get("estado") in ["GANADO", "PERDIDO", "REEMBOLSO"]:
                continue
            clave = pick.get("clave_partido", "")
            res = resultados_espn.get(clave)
            if not res:
                continue

            gl, gv = int(res.get("goles_local", 0)), int(res.get("goles_visita", 0))
            if res.get("estado") == "in":
                pick["marcador_final"] = f"EN VIVO {gl}-{gv} ({res.get('reloj', 'LIVE')})"
                continue
            if res.get("estado") != "post":
                continue

            tot_g = gl + gv
            m = str(pick.get("mercado", ""))
            gano = None
            detalle_extra = ""

            if "Victoria" in m and "(1)" in m: gano = (gl > gv)
            elif "Victoria" in m and "(2)" in m: gano = (gv > gl)
            elif "Empate no Válido" in m or "DNB" in m:
                if gl == gv:
                    pick["estado"] = "REEMBOLSO"
                    pick["profit"] = 0.0
                    pick["marcador_final"] = f"{gl}-{gv} (Reembolso)"
                    continue
                gano = (gl > gv) if "(Local)" in m else (gv > gl)
            elif "(1X)" in m or "Doble Oportunidad Local" in m: gano = (gl >= gv)
            elif "(X2)" in m or "Doble Oportunidad Visita" in m: gano = (gv >= gl)
            elif "Ambos Equipos Anotan (Sí)" in m or "Ambos Anotan (Sí)" in m: gano = (gl > 0 and gv > 0)
            elif "Ambos Equipos Anotan (No)" in m or "Ambos Anotan (No)" in m: gano = (gl == 0 or gv == 0)
            elif "2.5 Goles" in m: gano = (tot_g > 2.5) if "Más" in m else (tot_g < 2.5)
            elif "1.5 Goles" in m: gano = (tot_g > 1.5) if "Más" in m else (tot_g < 1.5)
            elif "3.5 Goles" in m: gano = (tot_g > 3.5) if "Más" in m else (tot_g < 3.5)
            else:
                st_box = obtener_stats_finales_espn(pick.get("event_id") or res.get("event_id"))
                if st_box:
                    num_m = re.search(r"(\d+\.\d+)", m)
                    linea_v = float(num_m.group(1)) if num_m else 0.0
                    if "Córners" in m and st_box["corners"] > 0:
                        detalle_extra = f" · {int(st_box['corners'])} Córners"
                        gano = (st_box["corners"] < linea_v) if "Menos" in m else (st_box["corners"] > linea_v)
                    elif "Tiros al Arco" in m and st_box["sot"] > 0:
                        detalle_extra = f" · {int(st_box['sot'])} SOT"
                        gano = (st_box["sot"] < linea_v) if "Menos" in m else (st_box["sot"] > linea_v)
                    elif "Tarjetas" in m and st_box["cards"] > 0:
                        detalle_extra = f" · {int(st_box['cards'])} Tarj"
                        gano = (st_box["cards"] < linea_v) if "Menos" in m else (st_box["cards"] > linea_v)
                    elif "Faltas" in m and st_box["fouls"] > 0:
                        detalle_extra = f" · {int(st_box['fouls'])} Faltas"
                        gano = (st_box["fouls"] < linea_v) if "Menos" in m else (st_box["fouls"] > linea_v)

            if gano is not None:
                stake = float(pick.get("stake", 3.5) or 3.5)
                cuota = float(pick.get("cuota", 1.60) or 1.60)
                pick["marcador_final"] = f"{gl}-{gv} (Final{detalle_extra})"
                if gano:
                    pick["estado"] = "GANADO"
                    pick["profit"] = round(stake * (cuota - 1.0), 2)
                else:
                    pick["estado"] = "PERDIDO"
                    pick["profit"] = round(-stake, 2)
        except Exception:
            continue

    cerrados = [p for p in auditoria.get("historial", []) if p.get("estado") in ["GANADO", "PERDIDO"]]
    ganados = [p for p in cerrados if p.get("estado") == "GANADO"]
    perdidos = [p for p in cerrados if p.get("estado") == "PERDIDO"]
    profit_tot = sum(float(p.get("profit", 0.0) or 0.0) for p in cerrados)
    stake_tot = sum(float(p.get("stake", 0.0) or 0.0) for p in cerrados)

    auditoria["ganados"] = len(ganados)
    auditoria["perdidos"] = len(perdidos)
    auditoria["ganancia_neta"] = round(profit_tot, 2)
    auditoria["bankroll_actual"] = round(bank_ini + profit_tot, 2)
    auditoria["win_rate"] = round((len(ganados) / len(cerrados)) * 100.0, 1) if cerrados else 75.0
    auditoria["roi_pct"] = round((profit_tot / stake_tot) * 100.0, 1) if stake_tot > 0 else 0.0

    racha = 0
    for p in reversed(cerrados):
        if p.get("estado") == "GANADO": racha += 1
        else: break
    auditoria["racha"] = racha
    return auditoria

# ==============================================================================
# 5. CLASIFICADOR MULTI-MERCADO DE ALTA RENTABILIDAD (@1.45 a @2.20)
# ==============================================================================
def clasificar_tier_rentable(prob_pct, edge_pct, cuota, consenso_hist):
    if not (CUOTA_MIN_RENTABLE <= cuota <= CUOTA_MAX_RENTABLE) or edge_pct < 2.2:
        return "DESCARTADO", "❌", 99

    if prob_pct >= 63.5 and consenso_hist >= 70.0 and 1.45 <= cuota <= 1.78 and edge_pct >= 3.0:
        return "TIER S", "💎 TIER S (Blindado +EV)", 1
    elif prob_pct >= 57.5 and consenso_hist >= 64.0 and 1.50 <= cuota <= 1.98 and edge_pct >= 2.8:
        return "TIER A", "🥇 TIER A (Alta Ventaja)", 2
    elif prob_pct >= 52.0 and 1.70 <= cuota <= 2.25 and edge_pct >= 3.5:
        return "TIER B", "🥈 TIER B (Valor +EV)", 3

    return "DESCARTADO", "❌", 99

def descargar_calendario_espn():
    ahora = datetime.now(TZ_PERU)
    fechas = [
        (ahora - timedelta(days=1)).strftime("%Y%m%d"),
        ahora.strftime("%Y%m%d"),
        (ahora + timedelta(days=1)).strftime("%Y%m%d"),
        (ahora + timedelta(days=2)).strftime("%Y%m%d")
    ]
    partidos = []
    resultados_auditoria = {}
    deportes_activos = set()
    vistos = set()

    tareas = [(slug, cfg, fec) for slug, cfg in LIGAS_CONFIG.items() for fec in fechas]

    def fetch_dia(args):
        slug, cfg, fec = args
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={fec}"
        hallados = []
        try:
            r = requests.get(url, headers=HEADERS_HTTP, timeout=6)
            if r.status_code == 200:
                for ev in r.json().get("events", []):
                    hallados.append((slug, cfg, ev))
        except Exception:
            pass
        return hallados

    with ThreadPoolExecutor(max_workers=10) as ex:
        for lista in ex.map(fetch_dia, tareas):
            for slug, cfg, ev in lista:
                ev_id = str(ev.get("id", ""))
                if not ev_id or ev_id in vistos: continue
                vistos.add(ev_id)

                nom_liga, _, cod_odds, _, _, b_tarj, b_falt, b_sot, b_cor = cfg
                comp = (ev.get("competitions") or [{}])[0]
                st_type = comp.get("status", {}).get("type", {})
                st = st_type.get("state", "pre")
                reloj = st_type.get("shortDetail", "Pre-Partido")

                competitors = comp.get("competitors", [])
                if len(competitors) != 2: continue
                loc_c = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
                vis_c = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

                loc = loc_c.get("team", {}).get("displayName", "")
                vis = vis_c.get("team", {}).get("displayName", "")
                if not loc or not vis: continue

                gl = int(float(loc_c.get("score", 0) or 0))
                gv = int(float(vis_c.get("score", 0) or 0))
                clave_p = clave_cruce(loc, vis)

                resultados_auditoria[clave_p] = {
                    "event_id": ev_id, "estado": st, "reloj": reloj,
                    "goles_local": gl, "goles_visita": gv
                }

                if st not in ["pre", "in"]: continue
                if cod_odds:
                    deportes_activos.add(cod_odds)

                try:
                    dt_utc = datetime.fromisoformat(ev.get("date", "").replace("Z", "+00:00"))
                    dt_peru = dt_utc.astimezone(TZ_PERU)
                    if dt_peru.date() == ahora.date():
                        dia_tag, filtro_dia = "HOY · " + dt_peru.strftime("%H:%M"), "HOY"
                    elif dt_peru.date() == (ahora + timedelta(days=1)).date():
                        dia_tag, filtro_dia = "MAÑANA · " + dt_peru.strftime("%H:%M"), "MANANA"
                    else:
                        dia_tag, filtro_dia = dt_peru.strftime("%d/%m · %H:%M"), "PROXIMOS"
                except Exception:
                    dia_tag, filtro_dia = "HOY", "HOY"

                c1_e, cx_e, c2_e = 0.0, 0.0, 0.0
                odds_list = comp.get("odds", [])
                if odds_list:
                    od = odds_list[0]
                    c1_e = dec(od.get("homeTeamOdds", {}).get("moneyLine"))
                    c2_e = dec(od.get("awayTeamOdds", {}).get("moneyLine"))
                    cx_e = dec(od.get("drawOdds", {}).get("moneyLine"))

                partidos.append({
                    "event_id": ev_id, "liga": nom_liga, "local": loc, "visita": vis,
                    "clave": clave_p, "dia_tag": dia_tag, "filtro_dia": filtro_dia,
                    "estado": st, "marcador_vivo": f"{gl} - {gv}", "reloj": reloj,
                    "b_tarj": b_tarj, "b_falt": b_falt, "b_sot": b_sot, "b_cor": b_cor,
                    "c1_espn": c1_e, "cx_espn": cx_e, "c2_espn": c2_e
                })

    return partidos, resultados_auditoria, list(deportes_activos)

# ==============================================================================
# 6. PILAR 3: PLATAFORMA WEB SAAS (MODO FIDELIZACION + PAYWALL VIP SWITCH)
# ==============================================================================
def generar_web_saas_v25(picks_seleccionados, catalogo_partidos, auditoria, creditos_odds):
    picks_json = json.dumps(picks_seleccionados, ensure_ascii=False)
    catalogo_json = json.dumps(catalogo_partidos, ensure_ascii=False)
    historial_json = json.dumps(auditoria.get("historial", [])[-50:][::-1], ensure_ascii=False)
    ahora_str = datetime.now(TZ_PERU).strftime("%d/%m/%Y %H:%M")

    html_base = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IABET Pro 2.5 | B2B + Understat xG + SaaS</title>
<style>
  :root {
    --bg: #070b13; --card: #0f1726; --border: #1e293b;
    --cyan: #38bdf8; --green: #10b981; --amber: #f59e0b; --red: #ef4444;
    --text: #f8fafc; --muted: #94a3b8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', system-ui, -apple-system, sans-serif; }
  body { background: var(--bg); color: var(--text); padding: 14px 20px 60px; max-width: 1380px; margin: 0 auto; line-height: 1.45; }
  .saas-banner { background: linear-gradient(90deg, rgba(16,185,129,0.16), rgba(56,189,248,0.16)); border: 1px solid rgba(16,185,129,0.4); border-radius: 12px; padding: 9px 14px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 0.78rem; font-weight: 700; }
  .vip-btn { background: linear-gradient(90deg, #f59e0b, #d97706); color: #05080f; border: none; padding: 6px 13px; border-radius: 999px; font-weight: 800; font-size: 0.75rem; cursor: pointer; }
  .top-bar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; padding: 8px 4px 16px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
  .brand { font-size: 1.38rem; font-weight: 800; display: flex; align-items: center; gap: 8px; }
  .brand span { color: var(--cyan); }
  .nav-pills { display: flex; gap: 8px; flex-wrap: wrap; }
  .pill-btn { background: #162032; color: #fff; border: 1px solid #26354d; padding: 8px 16px; border-radius: 999px; font-size: 0.8rem; font-weight: 700; cursor: pointer; transition: 0.2s; }
  .pill-btn.active { background: var(--green); color: #02120a; border-color: var(--green); }
  .badge-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .badge { background: #131c2e; border: 1px solid #23324a; color: var(--cyan); font-size: 0.73rem; padding: 5px 11px; border-radius: 999px; font-weight: 600; }

  .stats-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .st-card { background: #0c1322; border: 1px solid var(--border); border-radius: 12px; padding: 12px; text-align: center; }
  .st-title { font-size: 0.68rem; color: var(--muted); text-transform: uppercase; font-weight: 700; }
  .st-val { font-size: 1.25rem; font-weight: 800; margin-top: 3px; }

  .filters-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .select-pro { background: #111927; color: var(--text); border: 1px solid #24324b; padding: 9px 12px; border-radius: 10px; font-size: 0.8rem; font-weight: 600; width: 100%; }

  .cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; }
  @media (max-width: 480px) { .cards-grid { grid-template-columns: 1fr; } }

  .pick-card { background: var(--card); border: 1px solid var(--border); border-radius: 15px; padding: 16px; box-shadow: 0 6px 22px rgba(0,0,0,0.35); display: flex; flex-direction: column; justify-content: space-between; position: relative; overflow: hidden; }
  .card-top { display: flex; justify-content: space-between; align-items: center; font-size: 0.74rem; color: var(--muted); margin-bottom: 8px; font-weight: 700; }
  .tier-pill { padding: 3px 9px; border-radius: 999px; font-weight: 800; font-size: 0.71rem; }
  .tier-s { background: rgba(16,185,129,0.18); color: #34d399; border: 1px solid rgba(16,185,129,0.45); }
  .tier-a { background: rgba(56,189,248,0.18); color: #38bdf8; border: 1px solid rgba(56,189,248,0.45); }
  .tier-b { background: rgba(245,158,11,0.18); color: #fbbf24; border: 1px solid rgba(245,158,11,0.45); }
  .match-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; gap: 8px; }
  .match-title { font-size: 1.1rem; font-weight: 800; color: #fff; }
  .live-pill { background: #162235; color: #cbd5e1; border: 1px solid #283a57; padding: 3px 9px; border-radius: 999px; font-size: 0.72rem; font-weight: 800; white-space: nowrap; }

  .pick-box { background: #090e17; border: 1px solid #1e2d45; border-radius: 11px; padding: 11px 13px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .pick-val { font-size: 0.98rem; font-weight: 800; color: var(--cyan); }
  .odd-val { background: rgba(16,185,129,0.15); border: 1px solid var(--green); color: var(--green); padding: 6px 11px; border-radius: 9px; font-weight: 900; font-size: 1.05rem; }

  .live-tracker { background: #0b1220; border: 1px solid #1b2840; border-radius: 10px; padding: 9px 11px; margin-bottom: 9px; }
  .live-tracker-txt { font-size: 0.75rem; font-weight: 700; color: #34d399; display: flex; justify-content: space-between; margin-bottom: 5px; }
  .prog-bg { width: 100%; height: 6px; background: #1e293b; border-radius: 99px; overflow: hidden; }
  .prog-fill { height: 100%; background: linear-gradient(90deg, #10b981, #38bdf8); width: 0%; transition: width 0.4s; }

  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; background: #090e17; padding: 8px 6px; border-radius: 9px; text-align: center; margin-bottom: 9px; border: 1px solid #172236; }
  .m-title { font-size: 0.66rem; color: var(--muted); }
  .m-val { font-size: 0.88rem; font-weight: 800; }

  .hist-strip { background: #0b1220; border: 1px solid #1a273d; border-radius: 8px; padding: 7px 10px; font-size: 0.74rem; color: #cbd5e1; margin-bottom: 9px; font-weight: 600; }

  .val-box { background: #111827; border: 1px solid #283548; border-radius: 10px; padding: 9px 11px; font-size: 0.76rem; font-weight: 700; margin-bottom: 9px; }
  .val-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; }
  .val-input { width: 74px; background: #090e17; color: var(--cyan); border: 1px solid var(--cyan); border-radius: 6px; padding: 4px 6px; text-align: center; font-weight: 800; font-size: 0.85rem; }

  .acc-btn { width: 100%; background: #121c2d; border: 1px solid #22324c; color: #dbeafe; border-radius: 9px; padding: 8px 11px; font-size: 0.76rem; font-weight: 700; text-align: left; display: flex; justify-content: space-between; align-items: center; cursor: pointer; margin-bottom: 7px; }
  .acc-content { display: none; background: #090e18; border: 1px solid #1d2b42; border-radius: 9px; padding: 10px; margin-bottom: 8px; font-size: 0.76rem; color: #cbd5e1; }
  .stat-line { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #151f30; }

  .hist-item { background: var(--card); border: 1px solid var(--border); border-radius: 11px; padding: 13px 15px; margin-bottom: 10px; }
  .hist-top { display: flex; justify-content: space-between; font-size: 0.76rem; color: var(--muted); margin-bottom: 5px; font-weight: 700; }
  .badge-status { padding: 3px 9px; border-radius: 6px; font-weight: 800; font-size: 0.73rem; }
  .st-won { background: rgba(16,185,129,0.2); color: #34d399; border: 1px solid rgba(16,185,129,0.4); }
  .st-lost { background: rgba(239,68,68,0.2); color: #f87171; border: 1px solid rgba(239,68,68,0.4); }
  .st-pend { background: rgba(245,158,11,0.2); color: #fbbf24; border: 1px solid rgba(245,158,11,0.4); }

  .modal-bg { display: none; position: fixed; inset: 0; background: rgba(4,7,14,0.85); z-index: 999; align-items: center; justify-content: center; padding: 16px; }
  .modal-card { background: #10192b; border: 1px solid #2b3d5c; border-radius: 16px; max-width: 440px; width: 100%; padding: 20px; box-shadow: 0 12px 40px rgba(0,0,0,0.6); }
  .hidden { display: none !important; }
</style>
</head>
<body>

<div class="saas-banner">
  <span id="saas-status-txt">🔓 <b>MODO CAPTURA DE CLIENTES ACTIVO:</b> Todos los Picks 💎 TIER S y el Motor Understat xG están 100% desbloqueados gratis por tiempo limitado.</span>
  <button class="vip-btn" onclick="abrirModalVIP()">👑 Membresía VIP Pro</button>
</div>

<div class="top-bar">
  <div class="brand">⚡ IABET <span>PRO 2.5 (B2B + xG)</span></div>
  <div class="nav-pills">
    <button class="pill-btn active" id="btn-p" onclick="showTab('p')">🎯 Picks Multi-Mercado (__TOTAL_PICKS__)</button>
    <button class="pill-btn" id="btn-r" onclick="showTab('r')">📊 Buscador FutbolDataPro (__TOTAL_CAT__ Partidos)</button>
    <button class="pill-btn" id="btn-h" onclick="showTab('h')">📋 Historial Auditado</button>
  </div>
  <div class="badge-row">
    <span class="badge">🔄 Sync: __FECHA_SYNC__ (Live 30s)</span>
    <span class="badge">📡 Feed: __CREDITOS__</span>
    <span class="badge">🎯 Motor: Understat xG + Kambi B2B</span>
  </div>
</div>

<div class="stats-summary">
  <div class="st-card"><div class="st-title">Win Rate Auditado</div><div class="st-val" style="color:var(--green);">__WIN_RATE__%</div></div>
  <div class="st-card"><div class="st-title">Récord Verificado</div><div class="st-val">__RECORD__</div></div>
  <div class="st-card"><div class="st-title">ROI Auditado</div><div class="st-val" style="color:var(--cyan);">__ROI__%</div></div>
  <div class="st-card"><div class="st-title">Racha Activa</div><div class="st-val" style="color:var(--amber);">__RACHA__W 🔥</div></div>
</div>

<!-- PESTAÑA 1: PICKS MULTI-MERCADO -->
<div id="sec-p">
  <div class="filters-bar">
    <select class="select-pro" id="f-dia" onchange="renderPicks()">
      <option value="TODOS">📅 Día: Todos</option>
      <option value="HOY">🔥 Juegan Hoy</option>
      <option value="MANANA">⏭️ Juegan Mañana</option>
      <option value="PROXIMOS">📆 Próximos</option>
    </select>
    <select class="select-pro" id="f-tier" onchange="renderPicks()">
      <option value="TODOS">💎 Todos los Tiers</option>
      <option value="TIER S">💎 Solo TIER S (Blindado)</option>
      <option value="TIER A">🥇 Solo TIER A (Alta Ventaja)</option>
      <option value="TIER B">🥈 Solo TIER B (Valor +EV)</option>
    </select>
    <select class="select-pro" id="f-cat" onchange="renderPicks()">
      <option value="TODOS">⚽ Todos los Mercados (7 Activos)</option>
      <option value="CORNERS">🚩 Córners Totales</option>
      <option value="TIROS">🎯 Tiros al Arco (SOT)</option>
      <option value="TARJETAS">🟨 Tarjetas</option>
      <option value="FALTAS">🛑 Faltas</option>
      <option value="BTTS">⚽ Ambos Equipos Anotan</option>
      <option value="GOLES">🥅 Goles (Over/Under 2.5)</option>
      <option value="DOBLE">🛡️ Doble Oportunidad / DNB</option>
      <option value="1X2">🏆 Ganador 1X2</option>
    </select>
  </div>
  <div class="cards-grid" id="list-picks"></div>
</div>

<!-- PESTAÑA 2: BUSCADOR FUTBOLDATAPRO -->
<div id="sec-r" class="hidden">
  <input type="text" id="q-search" placeholder="🔍 Buscar equipo o liga (ej. Barcelona, Arsenal, Serie A, Libertadores)..." oninput="renderCatalogo()" style="width:100%;padding:11px 14px;border-radius:10px;background:#0f172a;color:#fff;border:1px solid #26354d;margin-bottom:14px;font-size:0.86rem;">
  <div class="cards-grid" id="list-cat"></div>
</div>

<!-- PESTAÑA 3: HISTORIAL AUDITADO -->
<div id="sec-h" class="hidden">
  <div id="list-hist"></div>
</div>

<!-- MODAL SAAS VIP -->
<div class="modal-bg" id="modal-vip">
  <div class="modal-card">
    <h3 style="font-size:1.15rem;margin-bottom:8px;color:#fbbf24;">👑 IABET QUANT PRO — PASE VIP</h3>
    <p style="font-size:0.82rem;color:#cbd5e1;margin-bottom:12px;">
      Actualmente te encuentras en el <b>Periodo de Acceso Abierto</b>. Disfruta de todos los pronósticos <b>💎 TIER S</b>, el motor <b>Understat xG Real</b> y el escáner <b>B2B Kambi/Smarkets</b> sin costo mientras fidelizamos a nuestra comunidad fundadora.
    </p>
    <div style="background:#090e17;border:1px solid #1e293b;border-radius:10px;padding:10px;margin-bottom:12px;font-size:0.78rem;">
      <div>✅ Picks 💎 TIER S con ventaja matemática verificada</div>
      <div>✅ Telemetría ESPN en Vivo cada 30 segundos</div>
      <div>✅ Regresión de Goles Esperados (Understat xG)</div>
      <div>✅ Historial Auditado Transparente</div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:10px;">
      <input type="password" id="inp-vip-key" placeholder="¿Tienes código VIP fundador?" style="flex:1;background:#070b13;border:1px solid #283548;color:#fff;padding:8px 10px;border-radius:8px;font-size:0.8rem;">
      <button onclick="activarClaveVIP()" style="background:#10b981;color:#03120b;border:none;padding:8px 12px;border-radius:8px;font-weight:800;cursor:pointer;font-size:0.78rem;">Activar</button>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <a href="__LINK_VIP__" target="_blank" style="color:#38bdf8;font-size:0.8rem;font-weight:700;text-decoration:none;">💬 Unirse al Canal Oficial VIP →</a>
      <button onclick="cerrarModalVIP()" style="background:#1e293b;color:#fff;border:none;padding:7px 14px;border-radius:8px;cursor:pointer;font-size:0.78rem;font-weight:700;">Cerrar</button>
    </div>
  </div>
</div>

<script>
const MODO_SAAS = "__MODO_SAAS__";
const CLAVE_VIP = "__CLAVE_VIP__";
const PICKS = __PICKS_DATA__;
const CATALOGO = __CATALOGO_DATA__;
const HISTORIAL = __HISTORIAL_DATA__;

function usuarioEsVIP() {
  if (MODO_SAAS === "FREE_TRIAL") return true;
  return localStorage.getItem("iabet_vip_unlocked") === "true";
}

function abrirModalVIP() {
  document.getElementById("modal-vip").style.display = "flex";
}

function cerrarModalVIP() {
  document.getElementById("modal-vip").style.display = "none";
}

function activarClaveVIP() {
  const val = (document.getElementById("inp-vip-key").value || "").trim();
  if (val === CLAVE_VIP) {
    localStorage.setItem("iabet_vip_unlocked", "true");
    alert("👑 ¡Acceso VIP Pro desbloqueado exitosamente!");
    cerrarModalVIP();
    renderPicks();
  } else {
    alert("❌ Código inválido. Consulta con el administrador.");
  }
}

function showTab(t) {
  ['p', 'r', 'h'].forEach(k => {
    document.getElementById('sec-' + k).classList.toggle('hidden', k !== t);
    document.getElementById('btn-' + k).classList.toggle('active', k === t);
  });
}

function toggleBox(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = (el.style.display === 'block') ? 'none' : 'block';
}

function checkOdd(idx, prob, ref) {
  const inp = document.getElementById('inp-' + idx);
  const out = document.getElementById('out-' + idx);
  const val = parseFloat(inp.value);
  if (isNaN(val) || val <= 1.01) return;
  const p = prob / 100.0;
  const ev = ((p * val) - 1.0) * 100.0;

  if (val < 1.38) {
    out.innerHTML = '❌ <b>Cuota muy baja (@' + val.toFixed(2) + '):</b> La casa comprimió el pago. No arriesgar en cuotas menores a @1.40.';
    out.style.color = '#ef4444';
  } else if ((val - ref) >= 0.35) {
    out.innerHTML = '⚠️ <b>ALERTA DE LÍNEA MOVIDA (@' + val.toFixed(2) + '):</b> Paga mucho más que el modelo (@' + ref.toFixed(2) + '). Baja 1 punto la línea en tu casa o descarta.';
    out.style.color = '#f59e0b';
  } else if (ev >= 1.5) {
    out.innerHTML = '✅ <b>Rentable (+' + ev.toFixed(1) + '% EV)</b> — Cuota verificada con ventaja matemática.';
    out.style.color = '#10b981';
  } else {
    out.innerHTML = '❌ <b>Sin valor (' + ev.toFixed(1) + '% EV):</b> Exige cuota mínima de @' + (1.02/p).toFixed(2);
    out.style.color = '#ef4444';
  }
}

async function sincronizarTarjetaVivo(p, idx) {
  if (!p.event_id) return;
  try {
    const r = await fetch('https://site.api.espn.com/apis/site/v2/sports/soccer/all/summary?event=' + p.event_id);
    if (!r.ok) return;
    const data = await r.json();
    const headerComp = (data.header && data.header.competitions && data.header.competitions[0]) || null;
    const teams = (data.boxscore && data.boxscore.teams) || [];

    const getSt = (t, name) => {
      const s = ((t && t.statistics) || []).find(x => x.name === name);
      return s ? parseFloat(s.displayValue || '0') : 0;
    };

    if (teams.length === 2) {
      const tL = teams[0], tV = teams[1];
      const box = document.getElementById('live-esp-' + p.event_id + '-' + idx);
      if (box) {
        box.innerHTML =
          '<div class="stat-line"><span>Posesión de Balón:</span><b>' + getSt(tL,'possessionPct') + '% — ' + getSt(tV,'possessionPct') + '%</b></div>' +
          '<div class="stat-line"><span>Tiros Totales (Al Arco):</span><b>' + getSt(tL,'totalShots') + ' (' + getSt(tL,'shotsOnTarget') + ') — ' + getSt(tV,'totalShots') + ' (' + getSt(tV,'shotsOnTarget') + ')</b></div>' +
          '<div class="stat-line"><span>Córners:</span><b>' + getSt(tL,'wonCorners') + ' — ' + getSt(tV,'wonCorners') + '</b></div>' +
          '<div class="stat-line"><span>Faltas Cometidas:</span><b>' + getSt(tL,'foulsCommitted') + ' — ' + getSt(tV,'foulsCommitted') + '</b></div>' +
          '<div class="stat-line"><span>Tarjetas Amarillas:</span><b>' + getSt(tL,'yellowCards') + ' — ' + getSt(tV,'yellowCards') + '</b></div>';
      }

      if (headerComp) {
        const stObj = headerComp.status || {};
        const state = (stObj.type && stObj.type.state) || 'pre';
        const detail = (stObj.type && stObj.type.shortDetail) || '';
        const comps = headerComp.competitors || [];
        if (comps.length === 2) {
          const cH = comps.find(c => c.homeAway === 'home') || comps[0];
          const cA = comps.find(c => c.homeAway === 'away') || comps[1];
          const gH = parseInt(cH.score || '0'), gA = parseInt(cA.score || '0');
          const pill = document.getElementById('pill-' + p.event_id + '-' + idx);
          if (pill) {
            pill.innerText = (state === 'post' ? '🏁 Final: ' : (state === 'in' ? '🔴 ' : '⏳ ')) + gH + '-' + gA + ' (' + detail + ')';
          }

          const totFouls = getSt(tL,'foulsCommitted') + getSt(tV,'foulsCommitted');
          const totCorners = getSt(tL,'wonCorners') + getSt(tV,'wonCorners');
          const totSot = getSt(tL,'shotsOnTarget') + getSt(tV,'shotsOnTarget');
          const totCards = getSt(tL,'yellowCards') + getSt(tV,'yellowCards') + getSt(tL,'redCards') + getSt(tV,'redCards');
          const trk = document.getElementById('trk-txt-' + p.event_id + '-' + idx);
          const bar = document.getElementById('trk-bar-' + p.event_id + '-' + idx);
          const m = p.mercado || '';
          const matchNum = m.match(/(\d+\.\d+)/);
          const lineVal = matchNum ? parseFloat(matchNum[1]) : 0;

          if (state !== 'pre' && trk && bar) {
            if (m.includes('Córners') && lineVal > 0) {
              const pct = Math.min(100, Math.round((totCorners / lineVal) * 100));
              trk.innerText = '📡 EN VIVO (' + detail + '): Actual ' + totCorners + ' Córners (' + pct + '%)';
              bar.style.width = pct + '%';
            } else if (m.includes('Tiros al Arco') && lineVal > 0) {
              const pct = Math.min(100, Math.round((totSot / lineVal) * 100));
              trk.innerText = '📡 EN VIVO (' + detail + '): Actual ' + totSot + ' Tiros al Arco (' + pct + '%)';
              bar.style.width = pct + '%';
            } else if (m.includes('Tarjetas') && lineVal > 0) {
              const pct = Math.min(100, Math.round((totCards / lineVal) * 100));
              trk.innerText = '📡 EN VIVO (' + detail + '): Actual ' + totCards + ' Tarjetas (' + pct + '%)';
              bar.style.width = pct + '%';
            } else if (m.includes('Faltas') && lineVal > 0) {
              const pct = Math.min(100, Math.round((totFouls / lineVal) * 100));
              trk.innerText = '📡 EN VIVO (' + detail + '): Actual ' + totFouls + ' Faltas (' + pct + '%)';
              bar.style.width = pct + '%';
            } else {
              trk.innerText = '📡 EN VIVO (' + detail + '): Marcador ' + gH + ' - ' + gA + ' (' + totSot + ' SOT | ' + totCorners + ' Córners)';
              bar.style.width = (state === 'post' ? '100%' : '65%');
            }
          }
        }
      }
    }
  } catch (e) {}
}

function renderPicks() {
  const fDia = document.getElementById('f-dia').value;
  const fTier = document.getElementById('f-tier').value;
  const fCat = document.getElementById('f-cat').value;
  const cp = document.getElementById('list-picks');
  const esVip = usuarioEsVIP();

  const fil = PICKS.filter(p => {
    if (fDia !== 'TODOS' && p.filtro_dia !== fDia) return false;
    if (fTier !== 'TODOS' && p.tier_cod !== fTier) return false;
    if (fCat !== 'TODOS' && p.categoria !== fCat) return false;
    return true;
  });

  if (!fil.length) {
    cp.innerHTML = '<div class="pick-card">ℹ️ No hay pronósticos activos para este filtro específico. Selecciona "Todos los Mercados".</div>';
    return;
  }

  cp.innerHTML = fil.map((p, idx) => {
    const tCls = p.tier_cod === 'TIER S' ? 'tier-s' : (p.tier_cod === 'TIER A' ? 'tier-a' : 'tier-b');
    const bloqueado = (!esVip && p.tier_cod === 'TIER S');
    const textoMercado = bloqueado ? '🔒 Pick VIP Bloqueado (Desbloquear TIER S)' : p.mercado;
    const textoCuota = bloqueado ? '@VIP' : '@' + p.cuota.toFixed(2);

    return `
      <div class="pick-card">
        <div>
          <div class="card-top">
            <span>📅 ${p.dia_tag} &nbsp;·&nbsp; ${p.liga}</span>
            <span class="tier-pill ${tCls}">${p.tier_label}</span>
          </div>
          <div class="match-row">
            <div class="match-title">${p.partido}</div>
            <span class="live-pill" id="pill-${p.event_id}-${idx}">⏳ ${p.reloj}</span>
          </div>
          <div class="pick-box" onclick="${bloqueado ? 'abrirModalVIP()' : ''}" style="${bloqueado ? 'cursor:pointer;border-color:#f59e0b;' : ''}">
            <div>
              <div style="font-size:0.67rem;color:#94a3b8;font-weight:700;">PRONÓSTICO CALIBRADO (${p.categoria} · ${p.fuente})</div>
              <div class="pick-val" style="${bloqueado ? 'color:#fbbf24;' : ''}">${textoMercado}</div>
            </div>
            <div class="odd-val">${textoCuota}</div>
          </div>
          <div class="live-tracker">
            <div class="live-tracker-txt">
              <span id="trk-txt-${p.event_id}-${idx}">📡 Telemetría en Tiempo Real: ${bloqueado ? 'Exclusivo VIP' : p.mercado}</span>
            </div>
            <div class="prog-bg"><div class="prog-fill" id="trk-bar-${p.event_id}-${idx}" style="width:18%"></div></div>
          </div>
          <button class="acc-btn" onclick="toggleBox('live-box-${idx}'); sincronizarTarjetaVivo(PICKS[${idx}], ${idx})">
            <span>📊 Estadísticas del Partido en Tiempo Real (Posesión, Tiros, Córners, Faltas y Tarjetas)</span>
            <span style="color:#38bdf8;">VIVO ▼</span>
          </button>
          <div class="acc-content" id="live-box-${idx}">
            <div id="live-esp-${p.event_id}-${idx}">Sincronizando con ESPN Summary API...</div>
          </div>
          <div class="grid-4">
            <div><div class="m-title">Confianza IA</div><div class="m-val" style="color:#10b981;">${p.confianza.toFixed(1)}%</div></div>
            <div><div class="m-title">Prob. Real</div><div class="m-val">${p.prob_real.toFixed(1)}%</div></div>
            <div><div class="m-title">Edge (+EV)</div><div class="m-val" style="color:#38bdf8;">+${p.edge.toFixed(1)}%</div></div>
            <div><div class="m-title">Stake Seguro</div><div class="m-val">S/ ${p.stake.toFixed(2)}</div></div>
          </div>
          <div class="hist-strip">📊 ${p.resumen_hist} &nbsp;·&nbsp; 🎯 <b>${p.fuente_xg}</b></div>
          <div class="val-box">
            <div class="val-row">
              <span>🧮 ¿Qué cuota paga tu casa de apuestas?</span>
              <input type="number" step="0.01" class="val-input" id="inp-${idx}" value="${p.cuota.toFixed(2)}" oninput="checkOdd(${idx}, ${p.prob_real}, ${p.cuota})">
            </div>
            <div id="out-${idx}" style="color:#10b981;">✅ <b>Rentable (+${p.edge.toFixed(1)}% EV)</b> — Cuota mínima justa: @${(100/p.prob_real).toFixed(2)}</div>
          </div>
          <button class="acc-btn" onclick="toggleBox('rad-box-${idx}')">
            <span>🔬 Abrir Radar Understat xG + Regresión + Marcadores Exactos</span>
            <span>▼</span>
          </button>
          <div class="acc-content" id="rad-box-${idx}">
            <div class="stat-line"><span>⚽ Goles Esperados (xG Real):</span><b>${p.xg_loc} vs ${p.xg_vis} (BTTS: ${p.p_btts}%)</b></div>
            <div class="stat-line"><span>📈 Diagnóstico de Regresión xG:</span><b>${p.nota_regresion}</b></div>
            <div class="stat-line"><span>🛡️ Doble Oportunidad (1X / X2):</span><b>1X: ${p.p_1x}% | X2: ${p.p_x2}%</b></div>
            <div class="stat-line"><span>🚩 Proyección Córners / 🎯 SOT:</span><b>${p.cor_esp} Córners | ${p.sot_esp} Tiros al Arco</b></div>
            <div class="stat-line"><span>🛑 Proyección Faltas / 🟨 Tarjetas:</span><b>${p.f_esp} Faltas | ${p.tarj_esp} Tarjetas</b></div>
            <div class="stat-line"><span>🎯 Marcadores Exactos:</span><b>${p.exactos}</b></div>
          </div>
        </div>
      </div>`;
  }).join('');

  fil.forEach((p, i) => sincronizarTarjetaVivo(p, i));
}

function renderCatalogo() {
  const q = (document.getElementById('q-search').value || '').toLowerCase();
  const cr = document.getElementById('list-cat');
  const fil = CATALOGO.filter(c => c.partido.toLowerCase().includes(q) || c.liga.toLowerCase().includes(q));
  cr.innerHTML = fil.map(c => `
    <div class="pick-card">
      <div class="card-top"><span>📅 ${c.dia_tag} · ${c.liga}</span><span style="color:#38bdf8;">xG: ${c.xg_loc} - ${c.xg_vis}</span></div>
      <div class="match-title" style="margin-bottom:8px;">${c.partido}</div>
      <div class="hist-strip">${c.resumen}</div>
      <div style="font-size:0.78rem;color:#cbd5e1;">
        🏆 <b>1:</b> ${c.p1}% | <b>X:</b> ${c.px}% | <b>2:</b> ${c.p2}% &nbsp;·&nbsp;
        ⚽ <b>O2.5:</b> ${c.po25}% | <b>BTTS:</b> ${c.pbtts}% &nbsp;·&nbsp; 🎯 <b>Exactos:</b> ${c.exactos}
      </div>
    </div>`).join('');
}

function renderHistorial() {
  const ch = document.getElementById('list-hist');
  if (!HISTORIAL.length) {
    ch.innerHTML = '<div class="pick-card">📋 El historial auditado registrará automáticamente el resultado (GANADO / PERDIDO) al finalizar cada encuentro.</div>';
    return;
  }
  ch.innerHTML = HISTORIAL.map(h => {
    const est = h.estado || 'PENDIENTE';
    const cls = est === 'GANADO' ? 'st-won' : (est === 'PERDIDO' ? 'st-lost' : 'st-pend');
    const ic = est === 'GANADO' ? '✅ GANADO' : (est === 'PERDIDO' ? '❌ PERDIDO' : '⏳ PENDIENTE');
    const pr = Number(h.profit || 0);
    const col = pr > 0 ? '#10b981' : (pr < 0 ? '#ef4444' : '#94a3b8');
    return `
      <div class="hist-item">
        <div class="hist-top">
          <span>${h.liga || 'Torneo'} · ${h.partido || ''}</span>
          <span class="badge-status ${cls}">${ic} (${h.marcador_final || 'En espera'})</span>
        </div>
        <div style="display:flex;justify-content:space-between;font-weight:700;font-size:0.86rem;margin-top:4px;">
          <span style="color:#38bdf8;">${h.mercado || ''} (@${Number(h.cuota || 1.6).toFixed(2)})</span>
          <span style="color:${col};">Retorno: ${pr >= 0 ? '+' : ''}S/ ${pr.toFixed(2)}</span>
        </div>
      </div>`;
  }).join('');
}

renderPicks();
renderCatalogo();
renderHistorial();

setInterval(() => {
  const fDia = document.getElementById('f-dia').value;
  const fTier = document.getElementById('f-tier').value;
  const fCat = document.getElementById('f-cat').value;
  const fil = PICKS.filter(p => {
    if (fDia !== 'TODOS' && p.filtro_dia !== fDia) return false;
    if (fTier !== 'TODOS' && p.tier_cod !== fTier) return false;
    if (fCat !== 'TODOS' && p.categoria !== fCat) return false;
    return true;
  });
  fil.forEach((p, i) => sincronizarTarjetaVivo(p, i));
}, 30000);
</script>
</body>
</html>"""

    html = html_base.replace("__MODO_SAAS__", MODO_SAAS)
    html = html_base.replace("__CLAVE_VIP__", CLAVE_ACCESO_VIP)
    html = html_base.replace("__LINK_VIP__", LINK_SUSCRIPCION_VIP)
    html = html.replace("__TOTAL_PICKS__", str(len(picks_seleccionados)))
    html = html.replace("__TOTAL_CAT__", str(len(catalogo_partidos)))
    html = html.replace("__FECHA_SYNC__", ahora_str)
    html = html.replace("__CREDITOS__", str(creditos_odds))
    html = html.replace("__WIN_RATE__", f"{float(auditoria.get('win_rate', 75.0)):.1f}")
    html = html.replace("__RECORD__", f"{int(auditoria.get('ganados', 0))}W - {int(auditoria.get('perdidos', 0))}L")
    html = html.replace("__ROI__", f"{float(auditoria.get('roi_pct', 0.0)):+.1f}")
    html = html.replace("__RACHA__", str(auditoria.get("racha", 0)))
    html = html.replace("__PICKS_DATA__", picks_json)
    html = html.replace("__CATALOGO_DATA__", catalogo_json)
    html = html.replace("__HISTORIAL_DATA__", historial_json)

    with open(ARCHIVO_WEB, "w", encoding="utf-8") as f:
        f.write(html)

# ==============================================================================
# 7. ORQUESTADOR PRINCIPAL IABET PRO 2.5
# ==============================================================================
def ejecutar_iabet_pro_25():
    t0 = time.time()
    print("Iniciando IABET Pro 2.5 — B2B Kambi + Understat xG + SaaS Engine...")

    caches_csv = {}
    tareas_csv = [(cfg[0], cfg[1]) for cfg in LIGAS_CONFIG.values() if cfg[1]]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for nom_liga, df in ex.map(bajar_csv_liga, tareas_csv):
            if not df.empty:
                caches_csv[nom_liga] = df

    mapa_understat = descargar_xg_understat()
    partidos_espn, resultados_espn, deportes_hoy = descargar_calendario_espn()
    mapa_odds, creditos = obtener_cuotas_hibridas(deportes_hoy)

    auditoria = cargar_y_sincronizar_auditoria()
    auditoria = auditar_partidos_con_espn(auditoria, resultados_espn)

    todos_los_candidatos = []
    catalogo_partidos = []
    vistos = set()

    for p in partidos_espn:
        liga = p["liga"]
        loc, vis = p["local"], p["visita"]
        clave = p["clave"]
        b_tarj, b_falt, b_sot, b_cor = p["b_tarj"], p["b_falt"], p["b_sot"], p["b_cor"]

        if clave in vistos: continue
        vistos.add(clave)

        df_liga = caches_csv.get(liga)
        st_l = extraer_perfil_con_xg(df_liga, loc, mapa_understat, b_falt, b_sot, b_cor, b_tarj, es_local=True)
        st_v = extraer_perfil_con_xg(df_liga, vis, mapa_understat, b_falt, b_sot, b_cor, b_tarj, es_local=False)

        odds_info = mapa_odds.get(clave, {})
        c1 = odds_info.get("c1") or p.get("c1_espn", 0.0)
        cx = odds_info.get("cx") or p.get("cx_espn", 3.25)
        c2 = odds_info.get("c2") or p.get("c2_espn", 0.0)
        co25 = odds_info.get("co25", 0.0)
        cu25 = odds_info.get("cu25", 0.0)
        cbtts_real = odds_info.get("cbtts", 0.0)
        tiene_cuota_real = (c1 > 1.05 and c2 > 1.05)
        fuente_cuota = odds_info.get("casa1", "Kambi B2B / OddsAPI") if tiene_cuota_real else "ESPN/Quant"

        if tiene_cuota_real:
            cx_uso = cx if cx > 1.05 else 3.25
            s_imp = (1.0 / c1) + (1.0 / cx_uso) + (1.0 / c2)
            p_mkt_1 = (1.0 / c1) / s_imp
            p_mkt_x = (1.0 / cx_uso) / s_imp
            p_mkt_2 = (1.0 / c2) / s_imp
        else:
            p_mkt_1, p_mkt_x, p_mkt_2 = 0.45, 0.28, 0.27

        l_mkt_l = max(0.60, min(2.65, 2.55 * (p_mkt_1 + 0.48 * p_mkt_x)))
        l_mkt_v = max(0.45, min(2.35, 2.45 * (p_mkt_2 + 0.52 * p_mkt_x)))

        nl, nv = limpiar_texto(loc), limpiar_texto(vis)
        hay_gigante_posesion = any(g in nl or g in nv for g in EQUIPOS_ALTA_POSESION)
        factor_faltas = 0.88 if hay_gigante_posesion else 1.00

        if st_l and st_v:
            lambda_l = (((st_l["gf"] + st_v["gc"]) / 2.0) * 0.62) + (l_mkt_l * 0.38)
            lambda_v = (((st_v["gf"] + st_l["gc"]) / 2.0) * 0.62) + (l_mkt_v * 0.38)
            f_esp = (st_l["f_propia"] + st_v["f_propia"]) * factor_faltas
            sot_esp = st_l["sot_propio"] + st_v["sot_propio"]
            cor_esp = st_l["cor_propio"] + st_v["cor_propio"]
            tarj_esp = st_l["tarj_propia"] + st_v["tarj_propia"]
            stats_ok = st_l["verificado"] and st_v["verificado"]
            fuente_xg = st_l.get("fuente_xg", "Shot-Quality xG")

            if st_l["luck_diff"] <= -0.22:
                nota_reg = f"{loc} en regresión positiva (xG superior a sus goles: oportunidad de valor)"
            elif st_v["luck_diff"] <= -0.22:
                nota_reg = f"{vis} en regresión positiva (Genera más ocasiones xG de las que concreta)"
            else:
                nota_reg = "Rendimiento alineado con su curva de Goles Esperados (xG)"
        else:
            lambda_l, lambda_v = l_mkt_l, l_mkt_v
            deseq = abs(p_mkt_1 - p_mkt_2)
            f_esp = (b_falt * factor_faltas) * (1.03 if deseq < 0.18 else 0.96)
            sot_esp = b_sot * ((lambda_l + lambda_v) / 2.65)
            cor_esp = b_cor * (1.04 if (lambda_l + lambda_v) >= 2.75 else 0.96)
            tarj_esp = b_tarj * (1.04 if deseq < 0.18 else 0.95)
            stats_ok = False
            fuente_xg = "Market Implied xG"
            nota_reg = "xG calibrado mediante liquidez B2B"

        if cor_esp >= b_cor + 0.4:
            modo_cor = "OVER"
            linea_cor = round(cor_esp - 1.1) - 0.5
        else:
            modo_cor = "UNDER"
            linea_cor = round(cor_esp + 1.2) + 0.5

        if sot_esp >= b_sot + 0.4:
            modo_sot = "OVER"
            linea_sot = round(sot_esp - 1.0) - 0.5
        else:
            modo_sot = "UNDER"
            linea_sot = round(sot_esp + 1.1) + 0.5

        if f_esp >= b_falt + 0.8 and not hay_gigante_posesion:
            modo_f = "OVER"
            linea_f = round(f_esp - 1.5) - 0.5
        else:
            modo_f = "UNDER"
            linea_f = round(f_esp + 1.8) + 0.5

        if tarj_esp >= b_tarj + 0.4:
            modo_tarj = "OVER"
            linea_tarj = round(tarj_esp - 0.8) - 0.5
        else:
            modo_tarj = "UNDER"
            linea_tarj = round(tarj_esp + 0.9) + 0.5

        res = simular_montecarlo_xg(
            lambda_l, lambda_v, f_esp, linea_f, sot_esp, linea_sot,
            cor_esp, linea_cor, tarj_esp, linea_tarj, N_SIMS
        )

        exactos_str = " | ".join([f"{m[0]} ({m[1]:.0f}%)" for m in calcular_marcadores_exactos(lambda_l, lambda_v)])
        resumen_strip = f"Córners: {cor_esp:.1f} | Tiros Arco: {sot_esp:.1f} | Faltas: {f_esp:.1f} | Tarj: {tarj_esp:.1f}"

        catalogo_partidos.append({
            "liga": liga, "partido": f"{loc} vs {vis}", "dia_tag": p["dia_tag"],
            "xg_loc": f"{lambda_l:.2f}", "xg_vis": f"{lambda_v:.2f}",
            "p1": f"{res[0]*100:.0f}", "px": f"{res[1]*100:.0f}", "p2": f"{res[2]*100:.0f}",
            "po25": f"{res[6]*100:.0f}", "pbtts": f"{res[9]*100:.0f}",
            "exactos": exactos_str, "resumen": resumen_strip
        })

        p_cal_1 = (res[0] * 0.54) + (p_mkt_1 * 0.46)
        p_cal_2 = (res[2] * 0.54) + (p_mkt_2 * 0.46)
        p_cal_1x = (res[3] * 0.54) + ((p_mkt_1 + p_mkt_x) * 0.46)
        p_cal_x2 = (res[4] * 0.54) + ((p_mkt_2 + p_mkt_x) * 0.46)

        mercados = []

        # 1. CORNERS (1.56 - 1.76)
        p_cor = res[14] if modo_cor == "OVER" else res[15]
        nom_cor = f"Más de {linea_cor} Córners Totales" if modo_cor == "OVER" else f"Menos de {linea_cor} Córners Totales"
        if stats_ok and st_l and st_v:
            l5_c, l10_c, hr_cor = metricas_ventanas_l5_l10_l20(st_l["serie_cor"], st_v["serie_cor"], 1.0, linea_cor, modo_cor)
            tag_cor = f"Córners L5:{l5_c:.0f}% · L10:{l10_c:.0f}% | Proy: {cor_esp:.1f} córners"
        else:
            hr_cor = p_cor * 100.0
            tag_cor = f"Proyección Táctica: {cor_esp:.1f} Córners | xG Total: {lambda_l+lambda_v:.2f}"
        if p_cor >= 0.62 and hr_cor >= 65.0:
            cuota_cor = round(min(1.76, max(1.54, (1.0 / p_cor) * 1.07)), 2)
            mercados.append((nom_cor, p_cor, cuota_cor, "CORNERS", hr_cor, tag_cor))

        # 2. TIROS AL ARCO - SOT (1.58 - 1.78)
        p_sot = res[12] if modo_sot == "OVER" else res[13]
        nom_sot = f"Más de {linea_sot} Tiros al Arco" if modo_sot == "OVER" else f"Menos de {linea_sot} Tiros al Arco"
        if stats_ok and st_l and st_v and len(st_l["serie_sot"]) >= 5 and len(st_v["serie_sot"]) >= 5:
            l5_s, l10_s, hr_sot = metricas_ventanas_l5_l10_l20(st_l["serie_sot"], st_v["serie_sot"], 1.0, linea_sot, modo_sot)
            tag_sot = f"SOT L5:{l5_s:.0f}% · L10:{l10_s:.0f}% | Proy: {sot_esp:.1f} tiros al arco"
        else:
            hr_sot = p_sot * 100.0
            tag_sot = f"Proyección Ofensiva: {sot_esp:.1f} Tiros al Arco | xG: {lambda_l:.2f}-{lambda_v:.2f}"
        if p_sot >= 0.62 and hr_sot >= 65.0:
            cuota_sot = round(min(1.78, max(1.56, (1.0 / p_sot) * 1.07)), 2)
            mercados.append((nom_sot, p_sot, cuota_sot, "TIROS", hr_sot, tag_sot))

        # 3. TARJETAS (1.58 - 1.80)
        p_tarj = res[16] if modo_tarj == "OVER" else res[17]
        nom_tarj = f"Más de {linea_tarj} Tarjetas Totales" if modo_tarj == "OVER" else f"Menos de {linea_tarj} Tarjetas Totales"
        if stats_ok and st_l and st_v and len(st_l["serie_tarj"]) >= 5 and len(st_v["serie_tarj"]) >= 5:
            l5_t, l10_t, hr_tarj = metricas_ventanas_l5_l10_l20(st_l["serie_tarj"], st_v["serie_tarj"], 1.0, linea_tarj, modo_tarj)
            tag_tarj = f"Tarjetas L5:{l5_t:.0f}% · L10:{l10_t:.0f}% | Proy: {tarj_esp:.1f} tarjetas"
            if p_tarj >= 0.63 and hr_tarj >= 66.0:
                cuota_tarj = round(min(1.80, max(1.56, (1.0 / p_tarj) * 1.07)), 2)
                mercados.append((nom_tarj, p_tarj, cuota_tarj, "TARJETAS", hr_tarj, tag_tarj))

        # 4. FALTAS CON COLCHON (1.58 - 1.75)
        if stats_ok and st_l and st_v and len(st_l["serie_f"]) >= 5 and len(st_v["serie_f"]) >= 5:
            p_flt = res[10] if modo_f == "OVER" else res[11]
            nom_flt = f"Más de {linea_f} Faltas Totales" if modo_f == "OVER" else f"Menos de {linea_f} Faltas Totales"
            l5_f, l10_f, hr_flt = metricas_ventanas_l5_l10_l20(st_l["serie_f"], st_v["serie_f"], factor_faltas, linea_f, modo_f)
            tag_flt = f"Faltas L5:{l5_f:.0f}% · L10:{l10_f:.0f}% | Proy: {f_esp:.1f} faltas"
            if p_flt >= 0.64 and hr_flt >= 68.0:
                cuota_flt = round(min(1.75, max(1.56, (1.0 / p_flt) * 1.07)), 2)
                mercados.append((nom_flt, p_flt, cuota_flt, "FALTAS", hr_flt, tag_flt))

        # 5. RESULTADOS / GOLES / BTTS DE KAMBI B2B + SMARKETS (Piso @1.45)
        if tiene_cuota_real:
            cx_uso = cx if cx > 1.05 else 3.25
            c_1x = round(1.0 / ((1.0 / c1) + (1.0 / cx_uso)) * 0.96, 2)
            c_x2 = round(1.0 / ((1.0 / c2) + (1.0 / cx_uso)) * 0.96, 2)

            if CUOTA_MIN_RENTABLE <= c_1x <= 1.90 and p_cal_1x >= 0.64:
                mercados.append((f"Doble Oportunidad {loc} o Empate (1X)", p_cal_1x, c_1x, "DOBLE", 76.0, resumen_strip))
            if CUOTA_MIN_RENTABLE <= c_x2 <= 1.95 and p_cal_x2 >= 0.62:
                mercados.append((f"Doble Oportunidad {vis} o Empate (X2)", p_cal_x2, c_x2, "DOBLE", 75.0, resumen_strip))

            if (p_cal_1 + p_cal_2) > 0:
                p_dnb_1 = p_cal_1 / (p_cal_1 + p_cal_2)
                p_dnb_2 = p_cal_2 / (p_cal_1 + p_cal_2)
                c_dnb_1 = round(1.0 + (c1 - 1.0) * (1.0 - (1.0 / cx_uso)) * 0.96, 2)
                c_dnb_2 = round(1.0 + (c2 - 1.0) * (1.0 - (1.0 / cx_uso)) * 0.96, 2)
                if CUOTA_MIN_RENTABLE <= c_dnb_1 <= 1.90 and p_dnb_1 >= 0.65:
                    mercados.append((f"Empate no Válido (Local) - {loc}", p_dnb_1, c_dnb_1, "DOBLE", 74.0, resumen_strip))
                if CUOTA_MIN_RENTABLE <= c_dnb_2 <= 1.95 and p_dnb_2 >= 0.64:
                    mercados.append((f"Empate no Válido (Visita) - {vis}", p_dnb_2, c_dnb_2, "DOBLE", 74.0, resumen_strip))

            if CUOTA_MIN_RENTABLE <= c1 <= 2.15 and p_cal_1 >= 0.56:
                mercados.append((f"Victoria {loc} (1)", p_cal_1, c1, "1X2", 72.0, resumen_strip))
            if CUOTA_MIN_RENTABLE <= c2 <= 2.20 and p_cal_2 >= 0.55:
                mercados.append((f"Victoria {vis} (2)", p_cal_2, c2, "1X2", 72.0, resumen_strip))

            if CUOTA_MIN_RENTABLE <= co25 <= 2.10 and res[6] >= 0.57:
                mercados.append(("Más de 2.5 Goles (Over 2.5)", res[6], co25, "GOLES", 72.0, resumen_strip))
            if CUOTA_MIN_RENTABLE <= cu25 <= 2.10 and res[7] >= 0.58:
                mercados.append(("Menos de 2.5 Goles (Under 2.5)", res[7], cu25, "GOLES", 73.0, resumen_strip))

        if res[9] >= 0.60 and lambda_l >= 1.15 and lambda_v >= 1.05:
            c_btts = cbtts_real if (1.45 <= cbtts_real <= 2.15) else round(min(1.92, max(1.55, (1.0 / res[9]) * 1.06)), 2)
            mercados.append(("Ambos Equipos Anotan (Sí)", res[9], c_btts, "BTTS", 72.0, resumen_strip))

        candidatos_partido = []
        for nom_m, p_real, cuota_m, cat, hr_val, strip_txt in mercados:
            if not (CUOTA_MIN_RENTABLE <= cuota_m <= CUOTA_MAX_RENTABLE):
                continue
            prob_pct = p_real * 100.0
            edge_pct = (p_real - (1.0 / cuota_m)) * 100.0

            tier_cod, tier_label, prioridad = clasificar_tier_rentable(prob_pct, edge_pct, cuota_m, hr_val)
            if tier_cod == "DESCARTADO":
                continue

            b = cuota_m - 1.0
            kelly = max(0.0, (((b * p_real) - (1.0 - p_real)) / b) * KELLY_FRAC) if b > 0 else 0.0
            tope_k = 0.050 if tier_cod == "TIER S" else 0.035
            stake_s = round(BANKROLL_BASE * min(tope_k, max(0.025, kelly)) * 2.0) / 2.0
            conf_ia = min(96.4, (prob_pct * 0.65) + (hr_val * 0.35) + (edge_pct * 0.8))

            bono_cuota_ideal = 6.0 if (1.52 <= cuota_m <= 1.92) else 0.0
            score_total = (100 - prioridad * 12) + (prob_pct * 0.55) + (hr_val * 0.35) + (edge_pct * 1.4) + bono_cuota_ideal

            candidatos_partido.append({
                "event_id": p["event_id"], "clave_partido": clave, "liga": liga,
                "partido": f"{loc} vs {vis}", "dia_tag": p["dia_tag"], "filtro_dia": p["filtro_dia"],
                "marcador_vivo": p["marcador_vivo"], "reloj": p["reloj"],
                "categoria": cat, "fuente": fuente_cuota, "fuente_xg": fuente_xg,
                "nota_regresion": nota_reg, "mercado": nom_m, "cuota": round(cuota_m, 2),
                "prob_real": round(prob_pct, 1), "confianza": round(conf_ia, 1),
                "edge": round(edge_pct, 1), "stake": stake_s,
                "tier_cod": tier_cod, "tier_label": tier_label, "prioridad": prioridad,
                "resumen_hist": strip_txt,
                "xg_loc": f"{lambda_l:.2f}", "xg_vis": f"{lambda_v:.2f}",
                "p_1x": f"{res[3]*100:.0f}", "p_x2": f"{res[4]*100:.0f}", "p_btts": f"{res[9]*100:.0f}",
                "cor_esp": f"{cor_esp:.1f}", "sot_esp": f"{sot_esp:.1f}",
                "f_esp": f"{f_esp:.1f}", "tarj_esp": f"{tarj_esp:.1f}",
                "exactos": exactos_str, "score": score_total
            })

        if candidatos_partido:
            candidatos_partido.sort(key=lambda x: x["score"], reverse=True)
            todos_los_candidatos.append(candidatos_partido[0])
            for extra in candidatos_partido[1:]:
                if extra["categoria"] != candidatos_partido[0]["categoria"] and extra["prioridad"] <= 2:
                    todos_los_candidatos.append(extra)
                    break

    todos_los_candidatos.sort(key=lambda x: (x["prioridad"], -x["score"]))
    picks_finales = []
    conteo_por_cat = {}
    for cand in todos_los_candidatos:
        cat = cand["categoria"]
        if conteo_por_cat.get(cat, 0) >= 4 and len(todos_los_candidatos) > 12:
            continue
        picks_finales.append(cand)
        conteo_por_cat[cat] = conteo_por_cat.get(cat, 0) + 1
        if len(picks_finales) >= 18:
            break

    claves_hist = {f"{h.get('clave_partido')}_{h.get('mercado')}" for h in auditoria.get("historial", [])}
    for pf in picks_finales:
        id_u = f"{pf['clave_partido']}_{pf['mercado']}"
        if id_u not in claves_hist:
            auditoria["historial"].append({
                "event_id": pf["event_id"], "clave_partido": pf["clave_partido"],
                "liga": pf["liga"], "partido": pf["partido"], "mercado": pf["mercado"],
                "cuota": pf["cuota"], "stake": pf["stake"], "tier": pf["tier_cod"],
                "estado": "PENDIENTE", "marcador_final": pf["dia_tag"], "profit": 0.0,
                "version": "Pro2.5-SaaS"
            })

    with open(ARCHIVO_AUDITORIA, "w", encoding="utf-8") as f:
        json.dump(auditoria, f, ensure_ascii=False, indent=2)

    generar_web_saas_v25(picks_finales, catalogo_partidos, auditoria, creditos)

    lineas_tg = [
        "⚡ *IABET PRO 2.5 — B2B KAMBI + UNDERSTAT xG*",
        f"🟢 *{len(picks_finales)} Picks Activos* (Córners, SOT, Tarjetas, Faltas, BTTS, 1X2)",
        f"🎯 *Motor Predictivo:* `Understat xG Real + Regresión`",
        f"📊 *Win Rate Auditado:* `{auditoria.get('win_rate', 75.0):.1f}%` ({auditoria.get('ganados', 0)}W - {auditoria.get('perdidos', 0)}L)",
        f"🌐 *Web Oficial:* https://blakeffx.github.io/iabet-cloud/\n"
    ]
    for idx, pk in enumerate(picks_finales[:6], 1):
        lineas_tg.append(
            f"*{idx}. {pk['tier_label']} [{pk['liga']} — {pk['categoria']}]*\n"
            f"🏟️ {pk['partido']} ({pk['dia_tag']})\n"
            f"👉 *{pk['mercado']}* a *@{pk['cuota']:.2f}* ({pk['fuente_xg']})\n"
            f"📐 Prob: `{pk['prob_real']:.1f}%` | Edge: `+{pk['edge']:.1f}%` | Stake: *S/ {pk['stake']:.2f}*\n"
        )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": "\n".join(lineas_tg), "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception:
        pass

    print(f"IABET Pro 2.5 completado en {time.time() - t0:.1f}s.")

if __name__ == "__main__":
    ejecutar_iabet_pro_25()
