import time
import io
import os
import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from numba import njit, prange

# ==============================================================================
# 1. CONFIGURACIÓN INSTITUCIONAL — IABET QUANT PRO v19.0 (ULTRA-SAFE EDITION)
# ==============================================================================
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "105d8edef7a4869116a817c106dc86a8")
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

MODO_COMERCIAL = "PRUEBA"       # "PRUEBA" (Todo abierto) | "VENTA" (Bloquea VIP)
CONTACTO_VIP = "https://t.me/tu_usuario_telegram"

BANKROLL_BASE = 100.0           # Capital base de referencia (S/ 100.00)
KELLY_FRAC = 0.20               # Kelly fraccional conservador
N_SIMS = 1_000_000              # 1,000,000 de simulaciones de Monte Carlo en C

ARCHIVO_AUDITORIA = "auditoria_iabet.json"
ARCHIVO_WEB = "index.html"
ARCHIVO_CACHE_ODDS = "cache_odds_api.json"
TIEMPO_CACHE_HORAS = 4.0

TZ_PERU = timezone(timedelta(hours=-5))

# Catálogo de Ligas: Slug ESPN -> (Nombre, Código CSV, Código OddsAPI, Base Tarj, Base Faltas, Base SOT, Base Córners)
LIGAS_CONFIG = {
    "esp.1": ("LaLiga", "SP1", "soccer_spain_la_liga", 4.6, 24.8, 8.4, 9.4),
    "eng.1": ("Premier League", "E0", "soccer_epl", 3.8, 21.4, 9.2, 10.4),
    "ita.1": ("Serie A", "I1", "soccer_italy_serie_a", 4.2, 23.8, 8.5, 9.5),
    "ger.1": ("Bundesliga", "D1", "soccer_germany_bundesliga", 3.9, 22.0, 9.4, 9.8),
    "fra.1": ("Ligue 1", "F1", "soccer_france_ligue_one", 3.9, 23.4, 8.6, 9.3),
    "ned.1": ("Eredivisie", "N1", "soccer_netherlands_eredivisie", 3.2, 20.0, 9.5, 10.2),
    "por.1": ("Primeira Liga", "P1", "soccer_portugal_primeira_liga", 5.0, 26.2, 8.4, 9.8),
    "bra.1": ("Brasileirao", "BRA", "soccer_brazil_campeonato", 4.9, 26.8, 8.7, 10.3),
    "arg.1": ("Liga Argentina", "ARG", "soccer_argentina_primera_division", 5.1, 27.2, 8.1, 9.1),
    "mex.1": ("Liga MX", "MEX", "soccer_mexico_ligamx", 4.4, 24.0, 8.7, 9.7),
    "per.1": ("Liga 1 Perú", None, None, 5.2, 27.5, 8.4, 9.2),
    "conmebol.libertadores": ("Copa Libertadores", None, "soccer_conmebol_copa_libertadores", 5.2, 27.2, 8.5, 9.5),
    "conmebol.sudamericana": ("Copa Sudamericana", None, "soccer_conmebol_copa_sudamericana", 4.9, 26.5, 8.3, 9.2),
    "uefa.champions": ("Champions League", None, "soccer_uefa_champs_league", 4.0, 22.2, 9.4, 9.9)
}

# Equipos de posesión dominante que reducen el ritmo de faltas del partido (-14%)
EQUIPOS_ALTA_POSESION = [
    "juventus", "inter", "milan", "napoli", "bologna", "real madrid", "barcelona",
    "girona", "bayern", "leverkusen", "stuttgart", "manchester city", "man city",
    "arsenal", "liverpool", "chelsea", "tottenham", "psg", "paris", "psv", "feyenoord",
    "benfica", "sporting", "flamengo", "palmeiras", "river plate", "universitario", "cristal"
]

DB_LIGA1_PERU = {
    "universitario": {"gf": 1.85, "gc": 0.65, "f_com": 12.8, "sot": 5.3, "cor": 5.6, "tarj": 2.2},
    "alianza lima": {"gf": 1.70, "gc": 0.75, "f_com": 12.6, "sot": 5.0, "cor": 5.4, "tarj": 2.3},
    "sporting cristal": {"gf": 2.05, "gc": 1.05, "f_com": 11.4, "sot": 5.7, "cor": 5.9, "tarj": 2.0},
    "melgar": {"gf": 1.80, "gc": 0.85, "f_com": 12.9, "sot": 5.4, "cor": 5.5, "tarj": 2.4},
    "cusco": {"gf": 1.65, "gc": 1.05, "f_com": 13.8, "sot": 4.7, "cor": 4.8, "tarj": 2.5},
    "cienciano": {"gf": 1.50, "gc": 1.15, "f_com": 14.1, "sot": 4.5, "cor": 4.7, "tarj": 2.6}
}

HEADERS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

# ==============================================================================
# 2. NÚCLEO MONTE CARLO EN C (NUMBA) CON CORRECCIÓN DIXON-COLES Y PERCENTILES
# ==============================================================================
@njit(parallel=True)
def motor_montecarlo_v19(
    l_loc, l_vis, f_esp, linea_f, sot_esp, linea_sot,
    cor_esp, linea_cor, tarj_esp, linea_tarj, n_sims
):
    vic_loc, empate, vic_vis = 0, 0, 0
    over_15, over_25, under_25, under_35 = 0, 0, 0, 0
    btts_si, btts_no = 0, 0
    f_over, f_under = 0, 0
    sot_over, sot_under = 0, 0
    cor_over, cor_under = 0, 0
    tarj_over, tarj_under = 0, 0
    cov = 0.08

    for i in prange(n_sims):
        ritmo = np.random.poisson(cov)
        gl = np.random.poisson(max(0.05, l_loc - cov)) + ritmo
        gv = np.random.poisson(max(0.05, l_vis - cov)) + ritmo

        # Ajuste táctico Dixon-Coles para marcadores cerrados (0-0, 1-1)
        if gl == 0 and gv == 0 and np.random.random() < 0.06:
            gl, gv = 0, 0
        elif gl == 1 and gv == 0 and np.random.random() < 0.04:
            gv = 1

        tot_g = gl + gv
        if gl > gv: vic_loc += 1
        elif gl == gv: empate += 1
        else: vic_vis += 1

        if tot_g > 1.5: over_15 += 1
        if tot_g > 2.5: over_25 += 1
        else: under_25 += 1
        if tot_g < 3.5: under_35 += 1

        if gl > 0 and gv > 0: btts_si += 1
        else: btts_no += 1

        # Faltas con varianza realista (Desviación 3.25)
        fric = 1.04 if abs(gl - gv) <= 1 else 0.95
        ftot = max(8.0, np.random.normal(f_esp * fric, 3.25))
        if ftot > linea_f: f_over += 1
        else: f_under += 1

        # Tiros al arco
        sot_sim = max(1.0, np.random.normal(sot_esp, 2.35))
        if sot_sim > linea_sot: sot_over += 1
        else: sot_under += 1

        # Córners
        cor_sim = max(2.0, np.random.normal(cor_esp, 2.75))
        if cor_sim > linea_cor: cor_over += 1
        else: cor_under += 1

        # Tarjetas desacopladas (solo 25% de sensibilidad a las faltas)
        mod_f = 1.0 + ((ftot / max(18.0, f_esp)) - 1.0) * 0.25
        t_sim = max(0.0, np.random.normal(tarj_esp * mod_f, 1.30))
        if t_sim > linea_tarj: tarj_over += 1
        else: tarj_under += 1

    return (
        vic_loc / n_sims,            # 0: Local (1)
        empate / n_sims,             # 1: Empate (X)
        vic_vis / n_sims,            # 2: Visita (2)
        (vic_loc + empate) / n_sims, # 3: 1X
        (vic_vis + empate) / n_sims, # 4: X2
        over_15 / n_sims,            # 5: Over 1.5 Goles
        over_25 / n_sims,            # 6: Over 2.5 Goles
        under_25 / n_sims,           # 7: Under 2.5 Goles
        under_35 / n_sims,           # 8: Under 3.5 Goles
        btts_si / n_sims,            # 9: BTTS Sí
        btts_no / n_sims,            # 10: BTTS No
        f_over / n_sims,             # 11: Faltas Over
        f_under / n_sims,            # 12: Faltas Under
        sot_over / n_sims,           # 13: SOT Over
        sot_under / n_sims,          # 14: SOT Under
        cor_over / n_sims,           # 15: Córners Over
        cor_under / n_sims,          # 16: Córners Under
        tarj_over / n_sims,          # 17: Tarjetas Over
        tarj_under / n_sims          # 18: Tarjetas Under
    )

def calcular_marcadores_exactos(l_loc, l_vis):
    from math import exp, factorial
    probs = []
    for g1 in range(5):
        for g2 in range(5):
            p1 = (exp(-l_loc) * (l_loc ** g1)) / factorial(g1)
            p2 = (exp(-l_vis) * (l_vis ** g2)) / factorial(g2)
            probs.append((f"{g1}-{g2}", p1 * p2 * 100.0))
    probs.sort(key=lambda x: x[1], reverse=True)
    return probs[:3]

# ==============================================================================
# 3. INGESTA DE DATOS (THE ODDS API CON CACHÉ + ESPN MULTI-DÍA + CSV INDIVIDUAL)
# ==============================================================================
def normalizar(txt):
    if not txt: return ""
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "-": " "}
    t = txt.lower().strip()
    for k, v in reemplazos.items():
        t = t.replace(k, v)
    basura = ["fc", "cf", "ac", "sc", "as", "ud", "cd", "rcd", "club", "deportivo", "sporting", "1909", "1913", "1899", "cfc", "bc"]
    palabras = [p for p in t.split() if p not in basura]
    return " ".join(palabras) if palabras else t

def dec(c):
    if c is None: return 0.0
    try:
        c = float(c)
        if c >= 100: return (c / 100.0) + 1.0
        elif c <= -100: return (100.0 / abs(c)) + 1.0
        return c
    except Exception:
        return 0.0

def obtener_cuotas_odds_api():
    ahora_ts = time.time()
    if os.path.exists(ARCHIVO_CACHE_ODDS):
        try:
            with open(ARCHIVO_CACHE_ODDS, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if (ahora_ts - cache.get("timestamp", 0)) < (TIEMPO_CACHE_HORAS * 3600) and cache.get("partidos"):
                return cache.get("partidos", {}), cache.get("creditos_restantes", "478")
        except Exception:
            pass

    if not THE_ODDS_API_KEY or len(THE_ODDS_API_KEY) < 15:
        return {}, "N/A"

    mapa_cuotas = {}
    creditos_restantes = "475"
    deportes_consultar = list({v[2] for v in LIGAS_CONFIG.values() if v[2]})

    for cod_dep in deportes_consultar:
        url = f"https://api.the-odds-api.com/v4/sports/{cod_dep}/odds/"
        params = {"apiKey": THE_ODDS_API_KEY, "regions": "eu,uk", "markets": "h2h,totals", "oddsFormat": "decimal"}
        try:
            r = requests.get(url, params=params, timeout=8)
            if "x-requests-remaining" in r.headers:
                creditos_restantes = str(r.headers["x-requests-remaining"])
            if r.status_code == 200:
                for ev in r.json():
                    loc, vis = ev.get("home_team", ""), ev.get("away_team", "")
                    if not loc or not vis: continue
                    clave = f"{normalizar(loc)[:5]}_{normalizar(vis)[:5]}"
                    c1, cx, c2, co25, cu25 = [], [], [], [], []
                    best_1, best_x, best_2 = (0.0, ""), (0.0, ""), (0.0, "")
                    best_o25, best_u25 = (0.0, ""), (0.0, "")

                    for bm in ev.get("bookmakers", []):
                        title = bm.get("title", "Bookie")
                        es_local_latam = any(k in title.lower() for k in ["betano", "betsson", "pinnacle", "betfair", "1xbet", "william", "unibet", "marathon"])
                        peso = 1.02 if "betano" in title.lower() else 1.0
                        for m in bm.get("markets", []):
                            if m.get("key") == "h2h":
                                for o in m.get("outcomes", []):
                                    pr = float(o.get("price", 0))
                                    if pr <= 1.01: continue
                                    if o.get("name") == loc:
                                        c1.append(pr)
                                        if pr * peso > best_1[0] and es_local_latam: best_1 = (pr, title)
                                    elif o.get("name") == vis:
                                        c2.append(pr)
                                        if pr * peso > best_2[0] and es_local_latam: best_2 = (pr, title)
                                    elif o.get("name") == "Draw":
                                        cx.append(pr)
                                        if pr * peso > best_x[0] and es_local_latam: best_x = (pr, title)
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
                        mapa_cuotas[clave] = {
                            "c1": best_1[0] or max(c1), "casa1": best_1[1] or "Market",
                            "cx": best_x[0] or (max(cx) if cx else 3.30), "casax": best_x[1] or "Market",
                            "c2": best_2[0] or max(c2), "casa2": best_2[1] or "Market",
                            "co25": best_o25[0] or (max(co25) if co25 else 0.0), "casao25": best_o25[1] or "Market",
                            "cu25": best_u25[0] or (max(cu25) if cu25 else 0.0), "casau25": best_u25[1] or "Market",
                            "med1": float(np.mean(c1)), "medx": float(np.mean(cx)) if cx else 3.30, "med2": float(np.mean(c2)),
                            "med_o25": float(np.mean(co25)) if co25 else 1.90, "med_u25": float(np.mean(cu25)) if cu25 else 1.90
                        }
        except Exception:
            continue

    if mapa_cuotas:
        try:
            with open(ARCHIVO_CACHE_ODDS, "w", encoding="utf-8") as f:
                json.dump({"timestamp": ahora_ts, "creditos_restantes": creditos_restantes, "partidos": mapa_cuotas}, f)
        except Exception:
            pass
    return mapa_cuotas, creditos_restantes

def bajar_csv_liga(args):
    nom_liga, cod_csv = args
    if not cod_csv: return nom_liga, pd.DataFrame()
    if cod_csv in ["BRA", "ARG", "MEX"]:
        urls = [f"https://www.football-data.co.uk/new/{cod_csv}.csv"]
    else:
        urls = [
            f"https://www.football-data.co.uk/mmz4281/2526/{cod_csv}.csv",
            f"https://www.football-data.co.uk/mmz4281/2425/{cod_csv}.csv"
        ]
    frames = []
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS_HTTP, timeout=7)
            if r.status_code == 200 and len(r.text) > 500:
                df = pd.read_csv(io.StringIO(r.text))
                df = df.rename(columns={'HG': 'FTHG', 'AG': 'FTAG', 'Home': 'HomeTeam', 'Away': 'AwayTeam'})
                if {'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}.issubset(df.columns):
                    frames.append(df)
        except Exception:
            pass
    if frames:
        return nom_liga, pd.concat(frames, ignore_index=True).tail(420)
    return nom_liga, pd.DataFrame()

def extraer_perfil_individual_equipo(df, equipo, b_falt, b_sot, b_cor, b_tarj, es_local=True):
    """
    CORRECCIÓN CRÍTICA v19.0:
    Aísla estrictamente las estadísticas COMETIDAS POR EL PROPIO EQUIPO
    sin sumar las faltas o tarjetas de sus rivales pasados.
    """
    eq_norm = normalizar(equipo)
    for k, v in DB_LIGA1_PERU.items():
        if k in eq_norm:
            return {
                "gf": v["gf"] * (1.08 if es_local else 0.92), "gc": v["gc"],
                "f_propia": v["f_com"], "sot_propio": v["sot"],
                "cor_propio": v["cor"], "tarj_propia": v["tarj"],
                "serie_f": [v["f_com"]] * 15, "serie_sot": [v["sot"]] * 15,
                "serie_cor": [v["cor"]] * 15, "serie_tarj": [v["tarj"]] * 15,
                "verificado": True
            }

    if df is None or df.empty:
        return None

    clave = eq_norm.split()[0] if eq_norm.split() else equipo[:5].lower()
    if len(clave) < 3 and len(eq_norm.split()) > 1:
        clave = eq_norm.split()[1]

    partidos = df[
        df['HomeTeam'].astype(str).apply(normalizar).str.contains(clave, na=False) |
        df['AwayTeam'].astype(str).apply(normalizar).str.contains(clave, na=False)
    ].tail(20)

    if len(partidos) < 6:
        return None

    gf, gc, f_p, sot_p, cor_p, tarj_p = [], [], [], [], [], []
    ok_f = {'HF', 'AF'}.issubset(partidos.columns)
    ok_s = {'HST', 'AST'}.issubset(partidos.columns)
    ok_c = {'HC', 'AC'}.issubset(partidos.columns)
    ok_t = {'HY', 'AY'}.issubset(partidos.columns)

    for _, row in partidos.iterrows():
        fue_local = clave in normalizar(str(row['HomeTeam']))
        gf.append(float(row['FTHG'] if fue_local else row['FTAG']))
        gc.append(float(row['FTAG'] if fue_local else row['FTHG']))
        if ok_f and pd.notna(row['HF']) and pd.notna(row['AF']):
            f_p.append(float(row['HF'] if fue_local else row['AF']))
        if ok_s and pd.notna(row['HST']) and pd.notna(row['AST']):
            sot_p.append(float(row['HST'] if fue_local else row['AST']))
        if ok_c and pd.notna(row['HC']) and pd.notna(row['AC']):
            cor_p.append(float(row['HC'] if fue_local else row['AC']))
        if ok_t and pd.notna(row['HY']) and pd.notna(row['AY']):
            tarj_p.append(float(row['HY'] if fue_local else row['AY']))

    tiene_stats = len(f_p) >= 6 and len(sot_p) >= 6
    pond = lambda arr, def_val: (np.mean(arr[-5:]) * 0.45 + np.mean(arr[-10:]) * 0.35 + np.mean(arr) * 0.20) if len(arr) >= 6 else def_val

    return {
        "gf": pond(gf, 1.35) * (1.07 if es_local else 0.93),
        "gc": pond(gc, 1.20),
        "f_propia": pond(f_p, b_falt / 2.0),
        "sot_propio": pond(sot_p, b_sot / 2.0),
        "cor_propio": pond(cor_p, b_cor / 2.0),
        "tarj_propia": pond(tarj_p, b_tarj / 2.0),
        "serie_f": f_p,
        "serie_sot": sot_p,
        "serie_cor": cor_p,
        "serie_tarj": tarj_p,
        "verificado": tiene_stats
    }

def evaluar_hit_rate_combinado(serie_a, serie_b, factor_ajuste, linea, modo="OVER"):
    n = min(len(serie_a), len(serie_b))
    if n < 6:
        return 0.0, 0.0, 0.0
    totales = (np.array(serie_a[-n:]) + np.array(serie_b[-n:])) * factor_ajuste
    l5_val = float(np.mean(totales[-5:]))
    l20_val = float(np.mean(totales))
    if modo == "OVER":
        hr_10 = float(np.mean(totales[-10:] > linea) * 100.0)
        hr_20 = float(np.mean(totales > linea) * 100.0)
    else:
        hr_10 = float(np.mean(totales[-10:] < linea) * 100.0)
        hr_20 = float(np.mean(totales < linea) * 100.0)
    return l5_val, l20_val, min(hr_10, hr_20)

# ==============================================================================
# 4. AUDITOR AUTOMÁTICO CON CONEXIÓN PROFUNDA A ESPN SUMMARY API
# ==============================================================================
def cargar_y_limpiar_auditoria():
    if os.path.exists(ARCHIVO_AUDITORIA):
        try:
            with open(ARCHIVO_AUDITORIA, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Limpieza de picks descalibrados pendientes de versiones previas
            historial_limpio = [
                p for p in data.get("historial", [])
                if p.get("estado") in ["GANADO", "PERDIDO"] or p.get("version") == "v19.0"
            ]
            data["historial"] = historial_limpio
            return data
        except Exception:
            pass
    return {"bankroll_inicial": 100.0, "bankroll_actual": 100.0, "ganancia_neta": 0.0, "roi_pct": 0.0, "win_rate": 0.0, "racha": 0, "historial": []}

def obtener_stats_finales_espn(event_id):
    if not event_id: return {}
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/summary?event={event_id}"
    try:
        r = requests.get(url, headers=HEADERS_HTTP, timeout=6)
        if r.status_code == 200:
            teams = r.json().get("boxscore", {}).get("teams", [])
            if len(teams) == 2:
                tot = {"fouls": 0.0, "sot": 0.0, "corners": 0.0, "cards": 0.0}
                for t in teams:
                    for st in t.get("statistics", []):
                        nam = st.get("name", "")
                        val = float(st.get("displayValue", "0") or 0)
                        if nam == "foulsCommitted": tot["fouls"] += val
                        elif nam in ["shotsOnTarget", "shotsOnGoal"]: tot["sot"] += val
                        elif nam in ["wonCorners", "cornerKicks"]: tot["corners"] += val
                        elif nam in ["yellowCards", "redCards"]: tot["cards"] += val
                return tot
    except Exception:
        pass
    return {}

def auditar_resultados_con_espn(auditoria, result
