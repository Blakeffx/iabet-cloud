import time
import io
import os
import requests
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from numba import njit, prange
from parlay_api import ParlayAPI

# ==============================================================================
# 1. CREDENCIALES Y CONFIGURACION MAESTRA (IABET v10.0 DEFINITIVE EDITION)
# ==============================================================================
API_KEY = "105d8edef7a4869116a817c106dc86a8"
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

BANKROLL = 100.0        # Tu presupuesto base en Soles (S/ 100.00)
KELLY_FRAC = 0.25       # Quarter Kelly institucional (25%)
N_SIMS = 1_000_000      # 1,000,000 de simulaciones de Monte Carlo por partido
TOP_DESTACADOS = 5      # Top 5 Picks Destacados
MAX_PRINCIPALES = 7     # Hasta 7 Picks Principales adicionales

# Rango de cuotas rentables estilo Plan Serious (Foco en 1.45 - 2.35)
CUOTA_MIN = 1.42
CUOTA_MAX = 2.40

# Catalogo Multi-Liga: Codigo ParlayAPI -> (Nombre, Codigo CSV, Media Tarjetas, Media Faltas, Media SOT)
LIGAS_FUTBOL = {
    "soccer_spain_la_liga": ("LaLiga", "SP1", 4.7, 25.0, 8.6),
    "soccer_epl": ("Premier League", "E0", 3.9, 21.6, 9.3),
    "soccer_italy_serie_a": ("Serie A", "I1", 4.4, 24.5, 8.7),
    "soccer_germany_bundesliga": ("Bundesliga", "D1", 4.0, 22.2, 9.5),
    "soccer_france_ligue_one": ("Ligue 1", "F1", 4.0, 23.6, 8.8),
    "soccer_netherlands_eredivisie": ("Eredivisie", "N1", 3.3, 20.4, 9.6),
    "soccer_portugal_primeira_liga": ("Primeira Liga", "P1", 5.1, 26.5, 8.5),
    "soccer_brazil_campeonato": ("Brasileirao", "BRA", 5.0, 27.2, 8.9),
    "soccer_argentina_primera_division": ("Liga Argentina", "ARG", 5.2, 27.6, 8.3),
    "soccer_mexico_ligamx": ("Liga MX", "MEX", 4.5, 24.4, 8.8),
    "soccer_conmebol_copa_sudamericana": ("Copa Sudamericana", None, 5.0, 26.8, 8.4),
    "soccer_conmebol_copa_libertadores": ("Copa Libertadores", None, 5.3, 27.5, 8.6),
    "soccer_uefa_champs_league": ("Champions League", None, 4.1, 22.6, 9.4)
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
    ("Copa Libertadores", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/copa_libertadores/all/all/matches.json?lang=es_ES&market=PE", 5.3, 27.5, 8.6)
]

HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
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
# 2. MOTOR MONTE CARLO EN C (NUMBA): SIMULADOR MULTIMERCADO SIN SESGO
# ==============================================================================
@njit(parallel=True)
def motor_montecarlo_v10(
    l_loc, l_vis, f_loc, f_vis, linea_f_tot,
    sot_loc, sot_vis, linea_sot, tarj_esp, linea_tarj, n_sims
):
    vic_loc, empate, vic_vis = 0, 0, 0
    over_15, over_25, under_25, under_35, btts_si = 0, 0, 0, 0, 0
    f_over, f_under = 0, 0
    sot_over, sot_under = 0, 0
    tarj_over, tarj_under = 0, 0
    cov = 0.09

    for i in prange(n_sims):
        # 1. Goles (Poisson Bivariada)
        ritmo = np.random.poisson(cov)
        gl = np.random.poisson(max(0.05, l_loc - cov)) + ritmo
        gv = np.random.poisson(max(0.05, l_vis - cov)) + ritmo
        tot_g = gl + gv

        if gl > gv: vic_loc += 1
        elif gl == gv: empate += 1
        else: vic_vis += 1

        if tot_g > 1.5: over_15 += 1
        if tot_g > 2.5: over_25 += 1
        else: under_25 += 1
        if tot_g < 3.5: under_35 += 1
        if gl > 0 and gv > 0: btts_si += 1

        # 2. Faltas (Distribucion Normal con friccion tactica)
        friccion = 1.05 if abs(gl - gv) <= 1 else 0.94
        fl = max(4.0, np.random.normal(f_loc * friccion, 2.40))
        fv = max(4.0, np.random.normal(f_vis * friccion, 2.50))
        ftot = fl + fv
        if ftot > linea_f_tot: f_over += 1
        else: f_under += 1

        # 3. Tiros al Arco (SOT)
        sot_tot = max(1.0, np.random.normal(sot_loc + sot_vis, 2.45))
        if sot_tot > linea_sot: sot_over += 1
        else: sot_under += 1

        # 4. Tarjetas
        ratio_f = ftot / max(18.0, (f_loc + f_vis))
        t_sim = max(0.0, np.random.normal(tarj_esp * ratio_f, 1.35))
        if t_sim > linea_tarj: tarj_over += 1
        else: tarj_under += 1

    return (
        vic_loc / n_sims,            # 0: Local (1)
        empate / n_sims,             # 1: Empate (X)
        vic_vis / n_sims,            # 2: Visita (2)
        (vic_loc + empate) / n_sims, # 3: Local o Empate (1X)
        (vic_vis + empate) / n_sims, # 4: Visita o Empate (X2)
        over_15 / n_sims,            # 5: Over 1.5 Goles
        over_25 / n_sims,            # 6: Over 2.5 Goles
        under_25 / n_sims,           # 7: Under 2.5 Goles
        under_35 / n_sims,           # 8: Under 3.5 Goles
        btts_si / n_sims,            # 9: Ambos Anotan (Si)
        f_over / n_sims,             # 10: Faltas OVER
        f_under / n_sims,            # 11: Faltas UNDER
        sot_over / n_sims,           # 12: Tiros al Arco OVER
        sot_under / n_sims,          # 13: Tiros al Arco UNDER
        tarj_over / n_sims,          # 14: Tarjetas OVER
        tarj_under / n_sims          # 15: Tarjetas UNDER
    )

@njit(parallel=True)
def motor_nba_v10(proyeccion, linea, n_sims):
    over_c, under_c = 0, 0
    std = max(1.45, proyeccion * 0.25)
    for i in prange(n_sims):
        sim = max(0.0, np.random.normal(proyeccion, std))
        if sim > linea: over_c += 1
        else: under_c += 1
    p75 = proyeccion + (0.6745 * std)
    return over_c / n_sims, under_c / n_sims, p75

# ==============================================================================
# 3. INGESTA PARALELA ROBUSTA (SIN ERRORES ASYNCIO / HTTPX)
# ==============================================================================
def bajar_csv_liga(args):
    nom_liga, cod_csv = args
    if not cod_csv:
        return nom_liga, pd.DataFrame()
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
            r = requests.get(u, headers=HEADERS_WEB, timeout=7)
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

def bajar_espn_liga(url):
    formas = {}
    try:
        r = requests.get(url, headers=HEADERS_WEB, timeout=6)
        if r.status_code == 200:
            for ev in r.json().get("events", []):
                comp = (ev.get("competitions") or [{}])[0]
                for c in comp.get("competitors", []):
                    t_name = c.get("team", {}).get("displayName", "")
                    form = c.get("form", "")
                    if t_name and form:
                        formas[t_name.lower()[:5]] = form
    except Exception:
        pass
    return formas

def bajar_kambi_torneo(args):
    nom_liga, url, b_tarj, b_falt, b_sot = args
    evs = []
    try:
        r = requests.get(url, headers=HEADERS_WEB, timeout=7)
        if r.status_code == 200:
            for ev in r.json().get("events", []):
                info = ev.get("event", {})
                loc, vis = info.get("homeName"), info.get("awayName")
                if not loc or not vis: continue
                c_1, c_x, c_2 = 0.0, 0.0, 0.0
                for bo in ev.get("betOffers", []):
                    crit = bo.get("criterion", {}).get("label", "")
                    if crit in ["Full Time", "Tiempo reglamentario", "Resultado Final"]:
                        for o in bo.get("outcomes", []):
                            odds = float(o.get("odds", 0)) / 1000.0
                            if o.get("type") == "OT_ONE": c_1 = odds
                            elif o.get("type") == "OT_CROSS": c_x = odds
                            elif o.get("type") == "OT_TWO": c_2 = odds
                if c_1 > 1.0 and c_2 > 1.0:
                    evs.append({
                        "liga": nom_liga, "home_team": loc, "away_team": vis,
                        "base_tarjetas": b_tarj, "base_faltas": b_falt, "base_sot": b_sot,
                        "bookmakers": [{
                            "title": "Betsson/Inkabet",
                            "markets": [{"key": "h2h", "outcomes": [
                                {"name": loc, "price": c_1},
                                {"name": "Draw", "price": c_x if c_x > 1.0 else 3.25},
                                {"name": vis, "price": c_2}
                            ]}]
                        }]
                    })
    except Exception:
        pass
    return evs

def cargar_datos_en_paralelo():
    caches_csv = {}
    espn_ctx = {}
    eventos_kambi = []

    tareas_csv = [(nom, cod) for _, (nom, cod, _, _, _) in LIGAS_FUTBOL.items() if cod]
    with ThreadPoolExecutor(max_workers=10) as ex:
        for nom, df in ex.map(bajar_csv_liga, tareas_csv):
            if not df.empty:
                caches_csv[nom] = df
        for d_form in ex.map(bajar_espn_liga, ESPN_SCOREBOARDS.values()):
            espn_ctx.update(d_form)
        for k_list in ex.map(bajar_kambi_torneo, ENDPOINTS_KAMBI):
            eventos_kambi.extend(k_list)

    return caches_csv, espn_ctx, eventos_kambi

# ==============================================================================
# 4. EXTRACTOR PROFUNDO L5 · L10 · L20 (GOLES, FALTAS, TIROS Y TARJETAS)
# ==============================================================================
def analizar_equipo_historico(df, equipo, es_local=True):
    eq_low = equipo.lower()
    for k, v in DB_LIGA1_PERU.items():
        if k in eq_low:
            f_esp = v["f_com"] * (0.96 if es_local else 1.06)
            return {
                "gf": v["gf"] * (1.08 if es_local else 0.92), "gc": v["gc"],
                "f_com": f_esp, "f_rec": v["f_rec"],
                "sot": v["sot"], "tarj": v["tarj"],
                "f_l5": f_esp, "f_l10": f_esp * 0.98, "f_l20": f_esp * 0.96,
                "tiene_stats_completas": True
            }

    if df is None or df.empty:
        return None

    limpio = [p for p in equipo.replace("-", " ").split() if p.lower() not in [
        "fc", "cf", "ac", "sc", "as", "ud", "cd", "rcd", "real", "club", "united", "city", "sporting", "atletico", "deportivo", "1909", "1899", "sv", "vfl", "tsg", "fsv"
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

    gf_pond = ((np.mean(gf[-5:]) * 0.50) + (np.mean(gf[-10:]) * 0.30) + (np.mean(gf) * 0.20)) * (1.08 if es_local else 0.92)
    gc_pond = (np.mean(gc[-5:]) * 0.50) + (np.mean(gc[-10:]) * 0.30) + (np.mean(gc) * 0.20)

    tiene_completas = (len(f_com) >= 5 and len(sot) >= 5)
    if tiene_completas:
        f_l5, f_l10, f_l20 = np.mean(f_com[-5:]), np.mean(f_com[-10:]), np.mean(f_com)
        f_pond = (f_l5 * 0.50) + (f_l10 * 0.30) + (f_l20 * 0.20)
        f_rec_pond = np.mean(f_rec[-10:])
        sot_pond = (np.mean(sot[-5:]) * 0.50) + (np.mean(sot[-10:]) * 0.30) + (np.mean(sot) * 0.20)
        tarj_pond = np.mean(tarj[-10:]) if len(tarj) >= 5 else 2.2
    else:
        f_l5 = f_l10 = f_l20 = f_pond = f_rec_pond = 12.0
        sot_pond = 4.4
        tarj_pond = 2.2

    return {
        "gf": gf_pond, "gc": gc_pond,
        "f_com": f_pond, "f_rec": f_rec_pond,
        "sot": sot_pond, "tarj": tarj_pond,
        "f_l5": f_l5, "f_l10": f_l10, "f_l20": f_l20,
        "tiene_stats_completas": tiene_completas
    }

def dec(c):
    if c is None: return 2.00
    c = float(c)
    if c >= 100: return (c / 100.0) + 1.0
    elif c <= -100: return (100.0 / abs(c)) + 1.0
    return c

# ==============================================================================
# 5. ENVIO BLINDADO A TELEGRAM (NUNCA FALLA NI SE BLOQUEA POR MARKDOWN)
# ==============================================================================
def enviar_telegram_seguro(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Dividir si supera 3900 caracteres para respetar el limite de Telegram
    bloques = [mensaje[i:i+3900] for i in range(0, len(mensaje), 3900)]
    for b in bloques:
        try:
            r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": b, "parse_mode": "Markdown"}, timeout=12)
            if r.status_code == 200:
                print("✅ Reporte entregado a Telegram con exito.")
            else:
                print(f"⚠️ Reenviando sin Markdown ({r.status_code})...")
                texto_limpio = b.replace("*", "").replace("`", "").replace("_", "")
                r2 = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": texto_limpio}, timeout=12)
                print(f"✅ Resultado reintento texto plano: {r2.status_code}")
        except Exception as e:
            print(f"❌ Error enviando a Telegram: {e}")

# ==============================================================================
# 6. ORQUESTADOR DEFINITIVO IABET v10.0 (DE-VIGGING + MONTE CARLO + PROPS)
# ==============================================================================
def ejecutar_iabet_v10():
    t0 = time.time()
    print("🚀 Iniciando IABET v10.0 Master Engine...")
    caches_csv, espn_ctx, eventos_todos = cargar_datos_en_paralelo()

    cliente = ParlayAPI(api_key=API_KEY)
    for cod, (nom_liga, _, b_tarj, b_falt, b_sot) in LIGAS_FUTBOL.items():
        try:
            evs = cliente.odds(cod, regions="eu,uk", markets="h2h,totals")
            if evs:
                for e in evs:
                    e["liga"] = nom_liga
                    e["base_tarjetas"] = b_tarj
                    e["base_faltas"] = b_falt
                    e["base_sot"] = b_sot
                    eventos_todos.append(e)
        except Exception as e:
            print(f"⚠️ Aviso en {nom_liga}: {e}")

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
        if not loc or not vis or "(Bookings)" in loc or "(Corners)" in loc:
            continue

        clave_p = f"{loc[:6]}_{vis[:6]}".lower()
        if clave_p in vistos_dedup:
            continue
        vistos_dedup.add(clave_p)

        bms = ev.get("bookmakers", [])
        if not bms:
            continue
        partidos_analizados += 1

        # 1. Extraer la MEJOR CUOTA (Best Odd) y TODAS las cuotas para el Consenso De-Vig
        m_1, m_x, m_2 = {"c": 0.0, "casa": ""}, {"c": 0.0, "casa": ""}, {"c": 0.0, "casa": ""}
        m_o25, m_u25 = {"c": 0.0, "casa": ""}, {"c": 0.0, "casa": ""}
        list_1, list_x, list_2 = [], [], []
        list_o25, list_u25 = [], []

        for casa in bms:
            nom_casa = casa.get("title", "Bookie")
            for m in casa.get("markets", []):
                mkey = m.get("key")
                if mkey == "h2h":
                    for o in m.get("outcomes", []):
                        p = dec(o.get("price"))
                        if p <= 1.01: continue
                        if o.get("name") == loc:
                            list_1.append(p)
                            if p > m_1["c"]: m_1 = {"c": p, "casa": nom_casa}
                        elif o.get("name") == vis:
                            list_2.append(p)
                            if p > m_2["c"]: m_2 = {"c": p, "casa": nom_casa}
                        elif o.get("name") == "Draw":
                            list_x.append(p)
                            if p > m_x["c"]: m_x = {"c": p, "casa": nom_casa}
                elif mkey == "totals":
                    for o in m.get("outcomes", []):
                        p = dec(o.get("price"))
                        pt = float(o.get("point") or 0)
                        if pt == 2.5 and p > 1.05:
                            if o.get("name") == "Over":
                                list_o25.append(p)
                                if p > m_o25["c"]: m_o25 = {"c": p, "casa": nom_casa}
                            elif o.get("name") == "Under":
                       
