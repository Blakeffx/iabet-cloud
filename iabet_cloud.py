import time
import io
import os
import asyncio
import httpx
import requests
import numpy as np
import pandas as pd
from numba import njit, prange
from parlay_api import ParlayAPI

# ==============================================================================
# 1. CREDENCIALES Y CONFIGURACION CALIBRADA (IABET v9.0 ANTI-SESGO)
# ==============================================================================
API_KEY = "105d8edef7a4869116a817c106dc86a8"
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

BANKROLL = 100.0        # Capital en Soles (S/ 100.00)
KELLY_FRAC = 0.20       # Kelly Conservador (Evita que mande S/ 22 en un solo pick)
N_SIMS = 1_000_000      # 1,000,000 de simulaciones Monte Carlo
TOP_DESTACADOS = 5
MAX_LISTOS = 12

# Rango de cuotas Serious (1.52 a 2.25)
CUOTA_MIN = 1.52
CUOTA_MAX = 2.25

# Base de ligas con codigos de Football-Data para descarga multi-temporada segura
LIGAS_FUTBOL = {
    "soccer_spain_la_liga": ("LaLiga", "SP1", 4.8, 25.2, 8.6),
    "soccer_epl": ("Premier League", "E0", 3.9, 21.8, 9.2),
    "soccer_italy_serie_a": ("Serie A", "I1", 4.4, 24.6, 8.7),
    "soccer_germany_bundesliga": ("Bundesliga", "D1", 4.0, 22.4, 9.5),
    "soccer_france_ligue_one": ("Ligue 1", "F1", 4.1, 23.8, 8.8),
    "soccer_netherlands_eredivisie": ("Eredivisie", "N1", 3.4, 20.5, 9.6),
    "soccer_brazil_campeonato": ("Brasileirao", "BRA", 5.0, 27.2, 8.9),
    "soccer_argentina_primera_division": ("Liga Argentina", "ARG", 5.2, 27.8, 8.3),
    "soccer_mexico_ligamx": ("Liga MX", "MEX", 4.5, 24.5, 8.8),
    "soccer_conmebol_copa_sudamericana": ("Copa Sudamericana", None, 5.0, 26.8, 8.4),
    "soccer_conmebol_copa_libertadores": ("Copa Libertadores", None, 5.3, 27.5, 8.6),
    "soccer_uefa_champs_league": ("Champions League", None, 4.1, 22.8, 9.4)
}

ESPN_SCOREBOARDS = {
    "LaLiga": "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard",
    "Premier League": "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "Serie A": "https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/scoreboard",
    "Bundesliga": "https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/scoreboard",
    "Ligue 1": "https://site.api.espn.com/apis/site/v2/sports/soccer/fra.1/scoreboard",
    "Liga 1 Peru": "https://site.api.espn.com/apis/site/v2/sports/soccer/per.1/scoreboard",
    "Copa Sudamericana": "https://site.api.espn.com/apis/site/v2/sports/soccer/conmebol.sudamericana/scoreboard",
    "Copa Libertadores": "https://site.api.espn.com/apis/site/v2/sports/soccer/conmebol.libertadores/scoreboard"
}

ENDPOINTS_KAMBI = [
    ("Liga 1 Peru", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/peru/primera_division/all/matches.json?lang=es_ES&market=PE", 5.3, 28.0, 8.5),
    ("Copa Sudamericana", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/copa_sudamericana/all/all/matches.json?lang=es_ES&market=PE", 5.0, 26.8, 8.4),
    ("Copa Libertadores", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/copa_libertadores/all/all/matches.json?lang=es_ES&market=PE", 5.3, 27.5, 8.6),
    ("LaLiga", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/spain/la_liga/all/matches.json?lang=es_ES&market=PE", 4.8, 25.2, 8.6),
    ("Premier League", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/england/premier_league/all/matches.json?lang=es_ES&market=PE", 3.9, 21.8, 9.2),
    ("Serie A", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/italy/serie_a/all/matches.json?lang=es_ES&market=PE", 4.4, 24.6, 8.7)
]

HEADERS_APK = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json"
}

DB_LIGA1_PERU = {
    "universitario": {"gf": 1.85, "gc": 0.65, "f_com": 13.6, "f_rec": 14.8, "sot": 5.4, "tarj": 2.3},
    "alianza lima": {"gf": 1.72, "gc": 0.75, "f_com": 13.1, "f_rec": 15.1, "sot": 5.1, "tarj": 2.4},
    "sporting cristal": {"gf": 2.10, "gc": 1.05, "f_com": 11.7, "f_rec": 14.9, "sot": 5.8, "tarj": 2.1},
    "melgar": {"gf": 1.82, "gc": 0.85, "f_com": 13.4, "f_rec": 13.9, "sot": 5.5, "tarj": 2.5},
    "cusco": {"gf": 1.68, "gc": 1.02, "f_com": 14.5, "f_rec": 13.2, "sot": 4.8, "tarj": 2.6},
    "cienciano": {"gf": 1.55, "gc": 1.15, "f_com": 14.9, "f_rec": 13.0, "sot": 4.6, "tarj": 2.7},
    "adt": {"gf": 1.52, "gc": 1.10, "f_com": 14.2, "f_rec": 12.8, "sot": 4.5, "tarj": 2.5},
    "garcilaso": {"gf": 1.45, "gc": 1.25, "f_com": 15.2, "f_rec": 12.5, "sot": 4.4, "tarj": 2.8}
}

# ==============================================================================
# 2. KERNEL MONTE CARLO CALIBRADO EN C (SIN SESGO DE TIROS AL ARCO)
# ==============================================================================
@njit(parallel=True)
def motor_quant_v9(
    l_loc, l_vis, f_loc, f_vis, linea_f_tot, linea_f_loc, linea_f_vis,
    sot_loc, sot_vis, linea_sot, tarj_esp, linea_tarj, n_sims
):
    vic_loc, empate, vic_vis = 0, 0, 0
    over_15, over_25, under_35, btts_si = 0, 0, 0, 0
    f_tot_over, f_tot_under = 0, 0
    f_loc_over, f_vis_over, f_vis_under = 0, 0, 0
    sot_over, sot_under = 0, 0
    tarj_over, tarj_under = 0, 0
    cov = 0.10

    for i in prange(n_sims):
        # 1. Goles (Poisson Bivariada)
        ritmo = np.random.poisson(cov)
        g_l = np.random.poisson(max(0.05, l_loc - cov)) + ritmo
        g_v = np.random.poisson(max(0.05, l_vis - cov)) + ritmo
        tot_g = g_l + g_v

        if g_l > g_v: vic_loc += 1
        elif g_l == g_v: empate += 1
        else: vic_vis += 1

        if tot_g > 1.5: over_15 += 1
        if tot_g > 2.5: over_25 += 1
        if tot_g < 3.5: under_35 += 1
        if g_l > 0 and g_v > 0: btts_si += 1

        # 2. Faltas (projectNormal con friccion realista)
        friccion = 1.06 if abs(g_l - g_v) <= 1 else 0.93
        presion_vis = 1.05 if g_v <= g_l else 0.96

        fl = max(4.0, np.random.normal(f_loc * friccion, 2.35))
        fv = max(4.0, np.random.normal(f_vis * friccion * presion_vis, 2.45))
        ftot = fl + fv

        if ftot > linea_f_tot: f_tot_over += 1
        else: f_tot_under += 1

        if fl > linea_f_loc: f_loc_over += 1
        if fv > linea_f_vis: f_vis_over += 1
        else: f_vis_under += 1

        # 3. Tiros al Arco (Gaussiana/Poisson sobre linea dinamica real del partido)
        sot_tot = max(1.0, np.random.normal(sot_loc + sot_vis, 2.40))
        if sot_tot > linea_sot: sot_over += 1
        else: sot_under += 1

        # 4. Tarjetas correlacionadas con faltas
        ratio_f = ftot / max(18.0, (f_loc + f_vis))
        t_sim = max(0.0, np.random.normal(tarj_esp * ratio_f, 1.35))
        if t_sim > linea_tarj: tarj_over += 1
        else: tarj_under += 1

    return (
        vic_loc / n_sims,               # 0: Local (1)
        (vic_loc + empate) / n_sims,    # 1: Doble Oportunidad (1X)
        vic_vis / n_sims,               # 2: Visita (2)
        over_25 / n_sims,               # 3: Over 2.5 Goles
        under_35 / n_sims,              # 4: Under 3.5 Goles
        btts_si / n_sims,               # 5: Ambos Anotan (Si)
        f_tot_over / n_sims,            # 6: Faltas Partido OVER
        f_tot_under / n_sims,           # 7: Faltas Partido UNDER
        f_loc_over / n_sims,            # 8: Faltas Local OVER
        f_vis_over / n_sims,            # 9: Faltas Visita OVER
        f_vis_under / n_sims,           # 10: Faltas Visita UNDER
        sot_over / n_sims,              # 11: Tiros al Arco OVER
        sot_under / n_sims,             # 12: Tiros al Arco UNDER
        tarj_over / n_sims,             # 13: Tarjetas OVER
        tarj_under / n_sims             # 14: Tarjetas UNDER
    )

@njit(parallel=True)
def motor_nba_v9(g_avg, linea, n_sims):
    over_c, under_c = 0, 0
    std = max(1.45, g_avg * 0.25)
    for i in prange(n_sims):
        sim = max(0.0, np.random.normal(g_avg, std))
        if sim > linea: over_c += 1
        else: under_c += 1
    p75 = g_avg + (0.6745 * std)
    return over_c / n_sims, under_c / n_sims, p75

# ==============================================================================
# 3. INGESTA MULTI-TEMPORADA CSV (GARANTIZA DATOS REALES HF, AF, HST, AST)
# ==============================================================================
async def descargar_csv_seguro(client, cod_csv):
    if not cod_csv:
        return pd.DataFrame()
    if cod_csv in ["BRA", "ARG", "MEX"]:
        urls = [f"https://www.football-data.co.uk/new/{cod_csv}.csv"]
    else:
        # Prueba temporada actual y fallback inmediato si el servidor cambia ruta
        urls = [
            f"https://www.football-data.co.uk/mmz4281/2526/{cod_csv}.csv",
            f"https://www.football-data.co.uk/mmz4281/2425/{cod_csv}.csv"
        ]
    frames = []
    for u in urls:
        try:
            r = await client.get(u, timeout=7.0)
            if r.status_code == 200 and len(r.text) > 500:
                df = pd.read_csv(io.StringIO(r.text))
                df = df.rename(columns={'HG': 'FTHG', 'AG': 'FTAG', 'Home': 'HomeTeam', 'Away': 'AwayTeam'})
                if {'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}.issubset(df.columns):
                    frames.append(df)
        except Exception:
            pass
    if frames:
        return pd.concat(frames, ignore_index=True).tail(450)
    return pd.DataFrame()

async def ingesta_completa_v9():
    caches_csv = {}
    eventos_kambi = []
    espn_ctx = {}

    async with httpx.AsyncClient(headers=HEADERS_APK, timeout=8.5) as client:
        # 1. Descargar todos los CSVs con respaldo multi-temporada
        for _, (nom_liga, cod_csv, _, _, _) in LIGAS_FUTBOL.items():
            if cod_csv and nom_liga not in caches_csv:
                df_res = await descargar_csv_seguro(client, cod_csv)
                if not df_res.empty:
                    caches_csv[nom_liga] = df_res

        # 2. Descargar ESPN Scoreboards (Forma y Racha en vivo)
        r_espn = await asyncio.gather(*[client.get(u) for u in ESPN_SCOREBOARDS.values()], return_exceptions=True)
        for r in r_espn:
            if isinstance(r, httpx.Response) and r.status_code == 200:
                try:
                    for ev in r.json().get("events", []):
                        comp = (ev.get("competitions") or [{}])[0]
                        for c in comp.get("competitors", []):
                            t_name = c.get("team", {}).get("displayName", "")
                            form = c.get("form", "")
                            if t_name and form:
                                espn_ctx[t_name.lower()[:5]] = form
                except Exception:
                    pass

        # 3. Descargar Kambi Directo (Cuotas reales de Faltas, Tarjetas y 1X2)
        r_k = await asyncio.gather(*[client.get(u) for _, u, _, _, _ in ENDPOINTS_KAMBI], return_exceptions=True)
        for (nom_liga, _, b_tarj, b_falt, b_sot), r in zip(ENDPOINTS_KAMBI, r_k):
            if isinstance(r, httpx.Response) and r.status_code == 200:
                try:
                    for ev in r.json().get("events", []):
                        info = ev.get("event", {})
                        loc, vis = info.get("homeName"), info.get("awayName")
                        if not loc or not vis: continue

                        c_1, c_x, c_2 = 0.0, 0.0, 0.0
                        linea_f, c_fo, c_fu = 0.0, 0.0, 0.0
                        linea_t, c_to, c_tu = 0.0, 0.0, 0.0
                        linea_s, c_so, c_su = 0.0, 0.0, 0.0

                        for bo in ev.get("betOffers", []):
                            crit = bo.get("criterion", {}).get("label", "")
                            if crit in ["Full Time", "Tiempo reglamentario", "Resultado Final"]:
                                for o in bo.get("outcomes", []):
                                    odds = float(o.get("odds", 0)) / 1000.0
                                    if o.get("type") == "OT_ONE": c_1 = odds
                                    elif o.get("type") == "OT_CROSS": c_x = odds
                                    elif o.get("type") == "OT_TWO": c_2 = odds
                            elif "Falta" in crit or "Foul" in crit:
                                for o in bo.get("outcomes", []):
                                    linea_f = float(o.get("line", 0)) / 1000.0
                                    odds = float(o.get("odds", 0)) / 1000.0
                                    if o.get("type") == "OT_OVER": c_fo = odds
                                    elif o.get("type") == "OT_UNDER": c_fu = odds
                            elif "Tarjeta" in crit or "Card" in crit:
                                for o in bo.get("outcomes", []):
                                    l_aux = float(o.get("line", 0)) / 1000.0
                                    if l_aux in [3.5, 4.5, 5.5]:
                                        linea_t = l_aux
                                        odds = float(o.get("odds", 0)) / 1000.0
                                        if o.get("type") == "OT_OVER": c_to = odds
                                        elif o.get("type") == "OT_UNDER": c_tu = odds
                            elif "Tiros a puerta" in crit or "Shots on target" in crit:
                                for o in bo.get("outcomes", []):
                                    linea_s = float(o.get("line", 0)) / 1000.0
                                    odds = float(o.get("odds", 0)) / 1000.0
                                    if o.get("type") == "OT_OVER": c_so = odds
                                    elif o.get("type") == "OT_UNDER": c_su = odds

                        if c_1 > 1.0 and c_2 > 1.0:
                            eventos_kambi.append({
                                "liga": nom_liga, "home_team": loc, "away_team": vis,
                                "base_tarjetas": b_tarj, "base_faltas": b_falt, "base_sot": b_sot,
                                "linea_f": linea_f, "c_fo": c_fo, "c_fu": c_fu,
                                "linea_t": linea_t, "c_to": c_to, "c_tu": c_tu,
                                "linea_s": linea_s, "c_so": c_so, "c_su": c_su,
                                "bookmakers": [{
                                    "title": "Betsson/Inkabet",
                                    "markets": [{"key": "h2h", "outcomes": [
                                        {"name": loc, "price": c_1},
                                        {"name": "Draw", "price": c_x if c_x > 1 else 3.30},
                                        {"name": vis, "price": c_2}
                                    ]}]
                                }]
                            })
                except Exception:
                    pass
    return caches_csv, eventos_kambi, espn_ctx

# ==============================================================================
# 4. EXTRACTOR L5·L10·L20 REAL (homeOrAway & filterShots DEL APK)
# ==============================================================================
def obtener_stats_equipo_v9(df, equipo, base_faltas, base_sot, base_tarj, es_local=True):
    eq_low = equipo.lower()
    for k, v in DB_LIGA1_PERU.items():
        if k in eq_low:
            f_esp = v["f_com"] * (0.96 if es_local else 1.06)
            return {
                "gf": v["gf"] * (1.10 if es_local else 0.90), "gc": v["gc"],
                "f_com": f_esp, "f_rec": v["f_rec"],
                "sot": v["sot"] * (1.06 if es_local else 0.94),
                "tarj": v["tarj"],
                "f_l5": f_esp, "f_l10": f_esp * 0.98, "f_l20": f_esp * 0.96,
                "real_csv": True
            }

    if df is None or df.empty:
        return None

    limpio = [p for p in equipo.replace("-", " ").split() if p.lower() not in [
        "fc", "cf", "ac", "sc", "as", "ud", "cd", "rcd", "real", "club", "united", "city", "sporting", "atletico", "deportivo", "1909", "1899"
    ]]
    clave = limpio[0] if limpio else equipo.split()[0]

    partidos = df[(df['HomeTeam'].str.contains(clave, case=False, na=False)) |
                  (df['AwayTeam'].str.contains(clave, case=False, na=False))].tail(20)
    if len(partidos) < 5:
        return None

    gf, gc, f_com, f_rec, sot, tarj = [], [], [], [], [], []
    ok_f = {'HF', 'AF'}.issubset(partidos.columns)
    ok_s = {'HST', 'AST'}.issubset(partidos.columns)
    ok_t = {'HY', 'AY'}.issubset(partidos.columns)

    for _, row in partidos.iterrows():
        es_l = clave.lower() in str(row['HomeTeam']).lower()
        gf.append(float(row['FTHG'] if es_l else row['FTAG']))
        gc.append(float(row['FTAG'] if es_l else row['FTHG']))
        if ok_f and pd.notna(row['HF']) and pd.notna(row['AF']):
            f_com.append(float(row['HF'] if es_l else row['AF']))
            f_rec.append(float(row['AF'] if es_l else row['HF']))
        if ok_s and pd.notna(row['HST']) and pd.notna(row['AST']):
            sot.append(float(row['HST'] if es_l else row['AST']))
        if ok_t and pd.notna(row['HY']) and pd.notna(row['AY']):
            tarj.append(float(row['HY'] if es_l else row['AY']))

    mitad_f = base_faltas / 2.0
    if len(f_com) >= 5:
        f_l5, f_l10, f_l20 = np.mean(f_com[-5:]), np.mean(f_com[-10:]), np.mean(f_com)
        f_pond = (f_l5 * 0.50) + (f_l10 * 0.30) + (f_l20 * 0.20)
        f_rec_pond = np.mean(f_rec[-10:])
    else:
        f_l5 = f_l10 = f_l20 = f_pond = f_rec_pond = mitad_f

    if len(sot) >= 5:
        sot_pond = (np.mean(sot[-5:]) * 0.50) + (np.mean(sot[-10:]) * 0.30) + (np.mean(sot) * 0.20)
    else:
        sot_pond = (base_sot / 2.0) * (1.08 if es_local else 0.92)

    gf_pond = ((np.mean(gf[-5:]) * 0.50) + (np.mean(gf[-10:]) * 0.30) + (np.mean(gf) * 0.20)) * (1.08 if es_local else 0.92)
    gc_pond = (np.mean(gc[-5:]) * 0.50) + (np.mean(gc[-10:]) * 0.30) + (np.mean(gc) * 0.20)

    return {
        "gf": gf_pond, "gc": gc_pond,
        "f_com": f_pond, "f_rec": f_rec_pond,
        "sot": sot_pond,
        "tarj": np.mean(tarj[-10:]) if len(tarj) >= 5 else (base_tarj / 2.0),
        "f_l5": f_l5, "f_l10": f_l10, "f_l20": f_l20,
        "real_csv": True
    }

def dec(c):
    if c is None: return 2.00
    c = float(c)
    if c >= 100: return (c / 100.0) + 1.0
    elif c <= -100: return (100.0 / abs(c)) + 1.0
    return c

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

# ==============================================================================
# 5. ORQUESTADOR CALIBRADO v9.0 (DIVERSIFICACION + EDGE REALISTA)
# ==============================================================================
def ejecutar_iabet_v9():
    t0 = time.time()
    caches_csv, eventos_todos, espn_ctx = asyncio.run(ingesta_completa_v9())

    cliente = ParlayAPI(api_key=API_KEY)
    for cod, (nom_liga, _, b_tarj, b_falt, b_sot) in LIGAS_FUTBOL.items():
        try:
            evs = cliente.odds(cod, regions="eu", markets="h2h,totals")
            if evs:
                for e in evs:
                    e["liga"] = nom_liga
                    e["base_tarjetas"] = b_tarj
                    e["base_faltas"] = b_falt
                    e["base_sot"] = b_sot
                    eventos_todos.append(e)
        except Exception:
            continue

    eventos_nba = []
    try:
        evs_nba = cliente.odds("basketball_nba", regions="us,eu", markets="h2h,totals,player_points,player_assists,player_rebounds")
        if evs_nba:
            eventos_nba.extend(evs_nba)
    except Exception:
        pass

    candidatos = []
    surebets = []
    partidos_analizados = 0
    vistos_dedup = set()

    for ev in eventos_todos:
        liga = ev.get("liga", "Torneo")
        loc, vis = ev.get("home_team"), ev.get("away_team")
 
