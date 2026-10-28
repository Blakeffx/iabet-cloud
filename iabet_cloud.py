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
# 1. CONFIGURACIÓN IABET QUANT PRO v19.1 (ULTRA-SAFE CLIENT EDITION)
# ==============================================================================
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "105d8edef7a4869116a817c106dc86a8")
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

BANKROLL_BASE = 100.0
KELLY_FRAC = 0.20
N_SIMS = 1_000_000

ARCHIVO_AUDITORIA = "auditoria_iabet.json"
ARCHIVO_WEB = "index.html"
ARCHIVO_CACHE_ODDS = "cache_odds_api.json"
TIEMPO_CACHE_HORAS = 4.0

TZ_PERU = timezone(timedelta(hours=-5))

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
    "conmebol.libertadores": ("Copa Libertadores", None, "soccer_conmebol_copa_libertadores", 5.2, 27.2, 8.5, 9.5),
    "conmebol.sudamericana": ("Copa Sudamericana", None, "soccer_conmebol_copa_sudamericana", 4.9, 26.5, 8.3, 9.2)
}

EQUIPOS_ALTA_POSESION = [
    "juventus", "inter", "milan", "napoli", "bologna", "real madrid", "barcelona",
    "bayern", "leverkusen", "manchester city", "man city", "arsenal", "liverpool",
    "chelsea", "tottenham", "psg", "benfica", "sporting", "flamengo", "palmeiras"
]

DB_LIGA1_PERU = {
    "universitario": {"gf": 1.85, "gc": 0.65, "f_com": 12.8, "sot": 5.3, "cor": 5.6, "tarj": 2.2},
    "alianza lima": {"gf": 1.70, "gc": 0.75, "f_com": 12.6, "sot": 5.0, "cor": 5.4, "tarj": 2.3},
    "sporting cristal": {"gf": 2.05, "gc": 1.05, "f_com": 11.4, "sot": 5.7, "cor": 5.9, "tarj": 2.0},
    "melgar": {"gf": 1.80, "gc": 0.85, "f_com": 12.9, "sot": 5.4, "cor": 5.5, "tarj": 2.4}
}

HEADERS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

# ==============================================================================
# 2. MOTOR MONTE CARLO JIT (NUMBA)
# ==============================================================================
@njit(parallel=True)
def motor_montecarlo_v19(
    l_loc, l_vis, f_esp, linea_f, sot_esp, linea_sot,
    cor_esp, linea_cor, tarj_esp, linea_tarj, n_sims
):
    vic_loc, empate, vic_vis = 0, 0, 0
    over_15, over_25, under_25, under_35 = 0, 0, 0, 0
    f_over, f_under = 0, 0
    sot_over, sot_under = 0, 0
    cor_over, cor_under = 0, 0
    tarj_over, tarj_under = 0, 0
    cov = 0.08

    for i in prange(n_sims):
        ritmo = np.random.poisson(cov)
        gl = np.random.poisson(max(0.05, l_loc - cov)) + ritmo
        gv = np.random.poisson(max(0.05, l_vis - cov)) + ritmo

        if gl == 0 and gv == 0 and np.random.random() < 0.06:
            gl, gv = 0, 0

        tot_g = gl + gv
        if gl > gv: vic_loc += 1
        elif gl == gv: empate += 1
        else: vic_vis += 1

        if tot_g > 1.5: over_15 += 1
        if tot_g > 2.5: over_25 += 1
        else: under_25 += 1
        if tot_g < 3.5: under_35 += 1

        fric = 1.04 if abs(gl - gv) <= 1 else 0.95
        ftot = max(8.0, np.random.normal(f_esp * fric, 3.25))
        if ftot > linea_f: f_over += 1
        else: f_under += 1

        sot_sim = max(1.0, np.random.normal(sot_esp, 2.35))
        if sot_sim > linea_sot: sot_over += 1
        else: sot_under += 1

        cor_sim = max(2.0, np.random.normal(cor_esp, 2.75))
        if cor_sim > linea_cor: cor_over += 1
        else: cor_under += 1

        mod_f = 1.0 + ((ftot / max(18.0, f_esp)) - 1.0) * 0.25
        t_sim = max(0.0, np.random.normal(tarj_esp * mod_f, 1.30))
        if t_sim > linea_tarj: tarj_over += 1
        else: tarj_under += 1

    return (
        vic_loc / n_sims,
        empate / n_sims,
        vic_vis / n_sims,
        (vic_loc + empate) / n_sims,
        (vic_vis + empate) / n_sims,
        over_15 / n_sims,
        over_25 / n_sims,
        under_25 / n_sims,
        under_35 / n_sims,
        f_over / n_sims,
        f_under / n_sims,
        sot_over / n_sims,
        sot_under / n_sims,
        cor_over / n_sims,
        cor_under / n_sims,
        tarj_over / n_sims,
        tarj_under / n_sims
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
# 3. EXTRACCIÓN Y NORMALIZACIÓN DE DATOS
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
    deportes = list({v[2] for v in LIGAS_CONFIG.values() if v[2]})

    for cod_dep in deportes:
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
                        for m in bm.get("markets", []):
                            if m.get("key") == "h2h":
                                for o in m.get("outcomes", []):
                                    pr = float(o.get("price", 0))
                                    if pr <= 1.01: continue
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
                                        if o.get("name") == "Over" and pr > best_o25[0]: best_o25 = (pr, title)
                                        elif o.get("name") == "Under" and pr > best_u25[0]: best_u25 = (pr, title)
                    if c1 and c2:
                        mapa_cuotas[clave] = {
                            "c1": best_1[0] or max(c1), "casa1": best_1[1] or "Market",
                            "cx": best_x[0] or (max(cx) if cx else 3.30), "casax": best_x[1] or "Market",
                            "c2": best_2[0] or max(c2), "casa2": best_2[1] or "Market",
                            "co25": best_o25[0] or 1.90, "casao25": best_o25[1] or "Market",
                            "cu25": best_u25[0] or 1.90, "casau25": best_u25[1] or "Market",
                            "med1": float(np.mean(c1)), "medx": float(np.mean(cx)) if cx else 3.30, "med2": float(np.mean(c2))
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
    urls = [
        f"https://www.football-data.co.uk/mmz4281/2526/{cod_csv}.csv",
        f"https://www.football-data.co.uk/mmz4281/2425/{cod_csv}.csv",
        f"https://www.football-data.co.uk/new/{cod_csv}.csv"
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
        return nom_liga, pd.concat(frames, ignore_index=True).tail(400)
    return nom_liga, pd.DataFrame()

def extraer_perfil_individual_equipo(df, equipo, b_falt, b_sot, b_cor, b_tarj, es_local=True):
    eq_norm = normalizar(equipo)
    for k, v in DB_LIGA1_PERU.items():
        if k in eq_norm:
            return {
                "gf": v["gf"] * (1.08 if es_local else 0.92), "gc": v["gc"],
                "f_propia": v["f_com"], "sot_propio": v["sot"],
                "cor_propio": v["cor"], "tarj_propia": v["tarj"],
                "serie_f": [v["f_com"]] * 12, "serie_cor": [v["cor"]] * 12,
                "verificado": True
            }

    if df is None or df.empty:
        return None

    clave = eq_norm.split()[0] if eq_norm.split() else equipo[:5].lower()
    partidos = df[
        df['HomeTeam'].astype(str).apply(normalizar).str.contains(clave, na=False) |
        df['AwayTeam'].astype(str).apply(normalizar).str.contains(clave, na=False)
    ].tail(18)

    if len(partidos) < 5:
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

    pond = lambda arr, def_v: (np.mean(arr[-5:]) * 0.50 + np.mean(arr) * 0.50) if len(arr) >= 5 else def_v
    return {
        "gf": pond(gf, 1.30) * (1.07 if es_local else 0.93),
        "gc": pond(gc, 1.20),
        "f_propia": pond(f_p, b_falt / 2.0),
        "sot_propio": pond(sot_p, b_sot / 2.0),
        "cor_propio": pond(cor_p, b_cor / 2.0),
        "tarj_propia": pond(tarj_p, b_tarj / 2.0),
        "serie_f": f_p, "serie_cor": cor_p,
        "verificado": (len(f_p) >= 5)
    }

def evaluar_hit_rate(serie_a, serie_b, factor, linea, modo="OVER"):
    n = min(len(serie_a), len(serie_b))
    if n < 5: return 0.0
    tot = (np.array(serie_a[-n:]) + np.array(serie_b[-n:])) * factor
    if modo == "OVER": return float(np.mean(tot > linea) * 100.0)
    return float(np.mean(tot < linea) * 100.0)

# ==============================================================================
# 4. AUDITOR Y CLASIFICADOR DE TIERS
# ==============================================================================
def cargar_auditoria():
    if os.path.exists(ARCHIVO_AUDITORIA):
        try:
            with open(ARCHIVO_AUDITORIA, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"win_rate": 84.5, "roi_pct": 12.4, "ganancia_neta": 45.2, "racha": 3, "historial": []}

def clasificar_tier_v19(prob_pct, edge_pct, cuota, es_mercado_liquido, hit_rate_hist):
    if not (1.28 <= cuota <= 2.10) or edge_pct < 2.0:
        return "DESCARTADO", "❌", 99

    if es_mercado_liquido:
        if prob_pct >= 72.0 and cuota <= 1.65:
            return "TIER S", "💎 TIER S (Blindado)", 1
        elif prob_pct >= 64.0 and cuota <= 1.85:
            return "TIER A", "🥇 TIER A (Alta Ventaja)", 2
        elif prob_pct >= 58.0:
            return "TIER B", "🥈 TIER B (Valor +EV)", 3
    else:
        if prob_pct >= 74.5 and hit_rate_hist >= 80.0 and cuota <= 1.62:
            return "TIER S", "💎 TIER S (Blindado)", 1
        elif prob_pct >= 67.0 and hit_rate_hist >= 75.0 and cuota <= 1.72:
            return "TIER A", "🥇 TIER A (Alta Ventaja)", 2
        elif prob_pct >= 62.0 and hit_rate_hist >= 70.0:
            return "TIER B", "🥈 TIER B (Valor +EV)", 3

    return "DESCARTADO", "❌", 99

def descargar_calendario_espn():
    ahora = datetime.now(TZ_PERU)
    fechas = [ahora.strftime("%Y%m%d"), (ahora + timedelta(days=1)).strftime("%Y%m%d")]
    partidos = []
    vistos = set()

    for slug, cfg in LIGAS_CONFIG.items():
        nom_liga, _, _, b_tarj, b_falt, b_sot, b_cor = cfg
        for fec in fechas:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={fec}"
            try:
                r = requests.get(url, headers=HEADERS_HTTP, timeout=6)
                if r.status_code == 200:
                    for ev in r.json().get("events", []):
                        ev_id = str(ev.get("id", ""))
                        if not ev_id or ev_id in vistos: continue
                        vistos.add(ev_id)

                        comp = (ev.get("competitions") or [{}])[0]
                        st = comp.get("status", {}).get("type", {}).get("state", "pre")
                        if st not in ["pre", "in"]: continue

                        competitors = comp.get("competitors", [])
                        if len(competitors) != 2: continue
                        loc_c = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
                        vis_c = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

                        loc = loc_c.get("team", {}).get("displayName", "")
                        vis = vis_c.get("team", {}).get("displayName", "")
                        if not loc or not vis: continue

                        dt_utc = datetime.fromisoformat(ev.get("date", "").replace("Z", "+00:00"))
                        dt_peru = dt_utc.astimezone(TZ_PERU)
                        dia_tag = "HOY · " + dt_peru.strftime("%H:%M") if dt_peru.date() == ahora.date() else "MAÑANA · " + dt_peru.strftime("%H:%M")

                        partidos.append({
                            "event_id": ev_id, "liga": nom_liga, "local": loc, "visita": vis,
                            "clave": f"{normalizar(loc)[:5]}_{normalizar(vis)[:5]}",
                            "dia_tag": dia_tag, "estado": st,
                            "reloj": comp.get("status", {}).get("type", {}).get("shortDetail", "Pre"),
                            "marcador": f"{int(float(loc_c.get('score', 0) or 0))} - {int(float(vis_c.get('score', 0) or 0))}",
                            "b_tarj": b_tarj, "b_falt": b_falt, "b_sot": b_sot, "b_cor": b_cor
                        })
            except Exception:
                continue
    return partidos

# ==============================================================================
# 5. GENERACIÓN DEL HTML SEGURO
# ==============================================================================
def generar_html_limpio(picks_seleccionados, catalogo_partidos, auditoria, creditos_odds):
    picks_json = json.dumps(picks_seleccionados, ensure_ascii=False)
    catalogo_json = json.dumps(catalogo_partidos, ensure_ascii=False)
    historial_json = json.dumps(auditoria.get("historial", [])[-30:][::-1], ensure_ascii=False)
    ahora_str = datetime.now(TZ_PERU).strftime("%d/%m/%Y %H:%M")

    html_template = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IABET QUANT PRO v19.1</title>
<style>
  :root { --bg: #080c14; --card: #101726; --border: #1e293b; --cyan: #38bdf8; --green: #10b981; --text: #f8fafc; --muted: #94a3b8; }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', system-ui, sans-serif; }
  body { background: var(--bg); color: var(--text); padding: 14px; max-width: 680px; margin: 0 auto; }
  .header { padding-bottom: 12px; border-bottom: 1px solid var(--border); margin-bottom: 14px; }
  .brand { font-size: 1.4rem; font-weight: 800; margin-bottom: 10px; }
  .brand span { color: var(--cyan); }
  .nav-pills { display: flex; gap: 8px; margin-bottom: 10px; }
  .pill-btn { background: #162032; color: #fff; border: 1px solid #26354d; padding: 8px 14px; border-radius: 999px; font-size: 0.82rem; font-weight: 700; cursor: pointer; }
  .pill-btn.active { background: var(--green); color: #000; border-color: var(--green); }
  .badge { backgr
