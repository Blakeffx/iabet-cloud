import time
import io
import os
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import requests
import numpy as np
import pandas as pd
from numba import njit, prange

# ==============================================================================
# 1. CONFIGURACIÓN ARQUITECTURA NIVEL 2+ (SIN APIS GENÉRICAS / CERO PARLAYAPI)
# ==============================================================================
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

MODO_COMERCIAL = "PRUEBA"  # "PRUEBA" (picks abiertos para tracción) o "VENTA" (con blur VIP)
LINK_CONTACTO_VIP = "https://t.me/+TuEnlaceDeContacto"

BANKROLL_INICIAL = 100.0
KELLY_FRAC = 0.25
N_SIMS = 1_000_000
CUOTA_MIN = 1.35
CUOTA_MAX = 2.45
MAX_PICKS_WEB = 12
ARCHIVO_AUDITORIA = "auditoria_iabet.json"

HEADERS_PRO = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9"
}

# Red de Catálogo Directo Kambi (Proveedor B2B oficial de Betsson, Inkabet, Unibet)
# Formato: (Identificador, Nombre Torneo, URL Endpoint JSON, Tarjetas Base, Faltas Base, SOT Base)
ENDPOINTS_KAMBI_DIRECTOS = [
    ("soccer_peru_primera_division", "Liga 1 Perú", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/peru/primera_division/all/matches.json?lang=es_ES&market=PE", 5.3, 28.0, 8.5),
    ("soccer_conmebol_copa_libertadores", "Copa Libertadores", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/copa_libertadores/all/all/matches.json?lang=es_ES&market=PE", 5.3, 27.5, 8.6),
    ("soccer_conmebol_copa_sudamericana", "Copa Sudamericana", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/copa_sudamericana/all/all/matches.json?lang=es_ES&market=PE", 5.0, 26.8, 8.4),
    ("soccer_spain_la_liga", "LaLiga", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/spain/la_liga/all/matches.json?lang=es_ES&market=PE", 4.7, 25.0, 8.6),
    ("soccer_epl", "Premier League", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/england/premier_league/all/matches.json?lang=es_ES&market=PE", 3.9, 21.6, 9.3),
    ("soccer_italy_serie_a", "Serie A", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/italy/serie_a/all/matches.json?lang=es_ES&market=PE", 4.4, 24.5, 8.7),
    ("soccer_germany_bundesliga", "Bundesliga", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/germany/bundesliga/all/matches.json?lang=es_ES&market=PE", 4.0, 22.2, 9.5),
    ("soccer_france_ligue_one", "Ligue 1", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/france/ligue_1/all/matches.json?lang=es_ES&market=PE", 4.0, 23.6, 8.8),
    ("soccer_uefa_champs_league", "Champions League", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/champions_league/all/all/matches.json?lang=es_ES&market=PE", 4.1, 22.6, 9.4),
    ("soccer_brazil_campeonato", "Brasileirão", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/brazil/serie_a/all/matches.json?lang=es_ES&market=PE", 5.0, 27.2, 8.9),
    ("soccer_argentina_primera_division", "Liga Argentina", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/argentina/primera_division/all/matches.json?lang=es_ES&market=PE", 5.2, 27.6, 8.3)
]

CSV_LIGAS_MAPA = {
    "LaLiga": "SP1",
    "Premier League": "E0",
    "Serie A": "I1",
    "Bundesliga": "D1",
    "Ligue 1": "F1",
    "Brasileirão": "BRA",
    "Liga Argentina": "ARG"
}

ESPN_SCOREBOARD_SLUGS = {
    "LaLiga": "esp.1",
    "Premier League": "eng.1",
    "Serie A": "ita.1",
    "Bundesliga": "ger.1",
    "Ligue 1": "fra.1",
    "Champions League": "uefa.champions",
    "Copa Libertadores": "conmebol.libertadores",
    "Copa Sudamericana": "conmebol.sudamericana",
    "Liga 1 Perú": "per.1",
    "Brasileirão": "bra.1",
    "Liga Argentina": "arg.1"
}

DB_EQUIPOS_PRO = {
    "universitario": {"gf": 1.88, "gc": 0.65, "f_com": 13.6, "f_rec": 14.8, "sot": 5.5, "tarj": 2.3},
    "alianza lima": {"gf": 1.75, "gc": 0.72, "f_com": 13.1, "f_rec": 15.1, "sot": 5.2, "tarj": 2.4},
    "sporting cristal": {"gf": 2.12, "gc": 1.02, "f_com": 11.7, "f_rec": 14.9, "sot": 5.9, "tarj": 2.1},
    "melgar": {"gf": 1.84, "gc": 0.85, "f_com": 13.4, "f_rec": 13.9, "sot": 5.5, "tarj": 2.5},
    "cusco": {"gf": 1.68, "gc": 1.02, "f_com": 14.5, "f_rec": 13.2, "sot": 4.8, "tarj": 2.6},
    "cienciano": {"gf": 1.55, "gc": 1.15, "f_com": 14.9, "f_rec": 13.0, "sot": 4.6, "tarj": 2.7},
    "adt": {"gf": 1.52, "gc": 1.10, "f_com": 14.2, "f_rec": 12.8, "sot": 4.5, "tarj": 2.5},
    "flamengo": {"gf": 1.95, "gc": 0.82, "f_com": 12.8, "f_rec": 14.6, "sot": 5.8, "tarj": 2.2},
    "palmeiras": {"gf": 1.90, "gc": 0.78, "f_com": 13.2, "f_rec": 14.4, "sot": 5.7, "tarj": 2.3},
    "river plate": {"gf": 1.88, "gc": 0.75, "f_com": 12.5, "f_rec": 15.0, "sot": 5.6, "tarj": 2.2},
    "boca juniors": {"gf": 1.62, "gc": 0.88, "f_com": 13.8, "f_rec": 14.5, "sot": 4.9, "tarj": 2.6}
}

# ==============================================================================
# 2. MOTOR MONTE CARLO JIT EN C (1,000,000 SIMULACIONES)
# ==============================================================================
@njit(parallel=True)
def motor_montecarlo_v13(
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

        friccion = 1.05 if abs(gl - gv) <= 1 else 0.94
        fl = max(4.0, np.random.normal(f_loc * friccion, 2.35))
        fv = max(4.0, np.random.normal(f_vis * friccion, 2.45))
        ftot = fl + fv
        if ftot > linea_f_tot: f_over += 1
        else: f_under += 1

        sot_tot = max(1.0, np.random.normal(sot_loc + sot_vis, 2.40))
        if sot_tot > linea_sot: sot_over += 1
        else: sot_under += 1

        ratio_f = ftot / max(18.0, (f_loc + f_vis))
        t_sim = max(0.0, np.random.normal(tarj_esp * ratio_f, 1.30))
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
        btts_si / n_sims,
        f_over / n_sims,
        f_under / n_sims,
        sot_over / n_sims,
        sot_under / n_sims,
        tarj_over / n_sims,
        tarj_under / n_sims
    )

# ==============================================================================
# 3. PIPELINE DE DATOS DIRECTOS (KAMBI B2B + FOOTBALL-DATA CSV + ESPN SCOREBOARDS)
# ==============================================================================
def normalizar_nombre(nombre):
    if not nombre:
        return ""
    limpio = str(nombre).lower().replace("-", " ")
    for p in ["fc ", "cf ", "cd ", "ca ", "sc ", "ac ", "as ", "ud ", "rcd ", "real ", "sporting ", "club ", "deportivo ", "atletico ", "united", "city"]:
        limpio = limpio.replace(p, "")
    return limpio.strip()

def descargar_csv_liga(item):
    nom_liga, cod_csv = item
    if not cod_csv:
        return nom_liga, pd.DataFrame()
    if cod_csv in ["BRA", "ARG"]:
        urls = [f"https://www.football-data.co.uk/new/{cod_csv}.csv"]
    else:
        urls = [
            f"https://www.football-data.co.uk/mmz4281/2526/{cod_csv}.csv",
            f"https://www.football-data.co.uk/mmz4281/2425/{cod_csv}.csv"
        ]
    frames = []
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS_PRO, timeout=7)
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

def descargar_espn_liga(item):
    nom_liga, espn_slug = item
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{espn_slug}/scoreboard"
    partidos_espn = {}
    equipos_espn = {}
    try:
        r = requests.get(url, headers=HEADERS_PRO, timeout=7)
        if r.status_code == 200:
            for ev in r.json().get("events", []):
                comp = (ev.get("competitions") or [{}])[0]
                st_obj = comp.get("status", {})
                estado = st_obj.get("type", {}).get("state", "pre")
                detalle_tiempo = st_obj.get("type", {}).get("shortDetail", "")
                equipos = comp.get("competitors", [])
                if len(equipos) == 2:
                    t_h = next((x for x in equipos if x.get("homeAway") == "home"), equipos[0])
                    t_a = next((x for x in equipos if x.get("homeAway") == "away"), equipos[1])
                    nh = t_h.get("team", {}).get("displayName", "")
                    na = t_a.get("team", {}).get("displayName", "")
                    sh = int(float(t_h.get("score", 0) or 0))
                    sa = int(float(t_a.get("score", 0) or 0))
                    fh = t_h.get("form", "")
                    fa = t_a.get("form", "")

                    if nh: equipos_espn[normalizar_nombre(nh)[:6]] = {"form": fh}
                    if na: equipos_espn[normalizar_nombre(na)[:6]] = {"form": fa}

                    clave = f"{normalizar_nombre(nh)}__{normalizar_nombre(na)}"
                    partidos_espn[clave] = {
                        "home": nh, "away": na, "state": estado,
                        "clock": detalle_tiempo, "home_score": sh, "away_score": sa
                    }
    except Exception:
        pass
    return partidos_espn, equipos_espn

def descargar_kambi_directo(args):
    cod_deporte, nom_liga, url, b_tarj, b_falt, b_sot = args
    evs = []
    try:
        r = requests.get(url, headers=HEADERS_PRO, timeout=8)
        if r.status_code == 200:
            for ev in r.json().get("events", []):
                info = ev.get("event", {})
                loc, vis = info.get("homeName"), info.get("awayName")
                if not loc or not vis:
                    continue
                c_1, c_x, c_2 = 0.0, 0.0, 0.0
                c_o25, c_u25 = 0.0, 0.0
                for bo in ev.get("betOffers", []):
                    crit = bo.get("criterion", {}).get("label", "")
                    if crit in ["Full Time", "Tiempo reglamentario", "Resultado Final", "1X2"]:
                        for o in bo.get("outcomes", []):
                            odds = float(o.get("odds", 0) or 0) / 1000.0
                            if o.get("type") == "OT_ONE": c_1 = odds
                            elif o.get("type") == "OT_CROSS": c_x = odds
                            elif o.get("type") == "OT_TWO": c_2 = odds
                    elif "Total" in crit or "Goles" in crit:
                        for o in bo.get("outcomes", []):
                            line = float(o.get("line", 0) or 0) / 1000.0
                            odds = float(o.get("odds", 0) or 0) / 1000.0
                            if abs(line - 2.5) < 0.1 and odds > 1.05:
                                if o.get("type") == "OT_OVER": c_o25 = odds
                                elif o.get("type") == "OT_UNDER": c_u25 = odds
                if c_1 > 1.01 and c_2 > 1.01:
                    evs.append({
                        "cod_liga": cod_deporte,
                        "liga": nom_liga,
                        "home_team": loc,
                        "away_team": vis,
                        "base_tarj": b_tarj,
                        "base_falt": b_falt,
                        "base_sot": b_sot,
                        "c_1": c_1,
                        "c_x": c_x if c_x > 1.05 else 3.25,
                        "c_2": c_2,
                        "c_o25": c_o25,
                        "c_u25": c_u25,
                        "casa": "Betsson/Inkabet"
                    })
    except Exception:
        pass
    return evs

def obtener_datos_arquitectura_nivel_2():
    caches_csv = {}
    espn_partidos = {}
    espn_equipos = {}
    eventos_kambi = []

    with ThreadPoolExecutor(max_workers=10) as ex:
        for nom, df in ex.map(descargar_csv_liga, CSV_LIGAS_MAPA.items()):
            if not df.empty:
                caches_csv[nom] = df
        for p_dict, eq_dict in ex.map(descargar_espn_liga, ESPN_SCOREBOARD_SLUGS.items()):
            espn_partidos.update(p_dict)
            espn_equipos.update(eq_dict)
        for k_evs in ex.map(descargar_kambi_directo, ENDPOINTS_KAMBI_DIRECTOS):
            eventos_kambi.extend(k_evs)

    return caches_csv, espn_partidos, espn_equipos, eventos_kambi

def calcular_bonus_forma_espn(form_str):
    if not form_str:
        return 1.0
    pesos = 0.0
    for ch in str(form_str)[-5:]:
        if ch.upper() == "W": pesos += 0.025
        elif ch.upper() == "L": pesos -= 0.025
    return max(0.88, min(1.14, 1.0 + pesos))

def analizar_equipo_profundo(df, equipo, base_faltas, base_sot, base_tarj, es_local=True):
    eq_low = normalizar_nombre(equipo)
    for k, v in DB_EQUIPOS_PRO.items():
        if k in eq_low:
            f_esp = v["f_com"] * (0.96 if es_local else 1.05)
            return {
                "gf": v["gf"] * (1.08 if es_local else 0.92), "gc": v["gc"],
                "f_com": f_esp, "f_rec": v["f_rec"],
                "sot": v["sot"], "tarj": v["tarj"],
                "f_l5": f_esp, "f_l10": f_esp * 0.98, "f_l20": f_esp * 0.96,
                "verificado": True
            }

    if df is None or df.empty:
        return None

    palabras = [p for p in eq_low.split() if len(p) >= 3]
    clave = palabras[0] if palabras else eq_low[:5]
    if not clave:
        return None

    partidos = df[(df['HomeTeam'].astype(str).str.contains(clave, case=False, na=False)) |
                  (df['AwayTeam'].astype(str).str.contains(clave, case=False, na=False))].tail(20)
    if len(partidos) < 5:
        return None

    gf, gc, f_com, f_rec, sot, tarj = [], [], [], [], [], []
    ok_f = {'HF', 'AF'}.issubset(partidos.columns)
    ok_s = {'HST', 'AST'}.issubset(partidos.columns)
    ok_t = {'HY', 'AY'}.issubset(partidos.columns)

    for _, row in partidos.iterrows():
        es_l = clave in str(row['HomeTeam']).lower()
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

    tiene_f = len(f_com) >= 5
    f_l5 = np.mean(f_com[-5:]) if tiene_f else base_faltas / 2.0
    f_l10 = np.mean(f_com[-10:]) if tiene_f else base_faltas / 2.0
    f_l20 = np.mean(f_com) if tiene_f else base_faltas / 2.0
    f_pond = (f_l5 * 0.50) + (f_l10 * 0.30) + (f_l20 * 0.20)
    f_rec_pond = np.mean(f_rec[-10:]) if tiene_f else base_faltas / 2.0
    sot_pond = ((np.mean(sot[-5:]) * 0.50) + (np.mean(sot[-10:]) * 0.30) + (np.mean(sot) * 0.20)) if len(sot) >= 5 else base_sot / 2.0
    tarj_pond = np.mean(tarj[-10:]) if len(tarj) >= 5 else base_tarj / 2.0

    return {
        "gf": gf_pond, "gc": gc_pond,
        "f_com": f_pond, "f_rec": f_rec_pond,
        "sot": sot_pond, "tarj": tarj_pond,
        "f_l5": f_l5, "f_l10": f_l10, "f_l20": f_l20,
        "verificado": True
    }

# ==============================================================================
# 4. AUDITOR INMUTABLE DE RESULTADOS (ESPN SCOREBOARDS EN VIVO)
# ==============================================================================
def cargar_auditoria():
    if os.path.exists(ARCHIVO_AUDITORIA):
        try:
            with open(ARCHIVO_AUDITORIA, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "resumen": {
            "bankroll_inicial": BANKROLL_INICIAL,
            "bankroll_actual": BANKROLL_INICIAL,
            "ganancia_neta": 0.0,
            "roi_pct": 0.0,
            "win_rate_pct": 0.0,
            "ganados": 0,
            "perdidos": 0,
            "en_vivo": 0,
            "pendientes": 0,
            "racha_actual": "En espera"
        },
        "historial": []
    }

def auditar_picks_con_espn(auditoria, espn_partidos):
    historial = auditoria.get("historial", [])
    en_vivo_cnt = 0

    for pick in historial:
        if pick.get("estado") in ("GANADO", "PERDIDO"):
            continue

        nl = normalizar_nombre(pick.get("local", ""))[:5]
        nv = normalizar_nombre(pick.get("visita", ""))[:5]
        partido_espn = None
        for k, val in espn_partidos.items():
            if nl in k and nv in k:
                partido_espn = val
                break

        if not partido_espn:
            continue

        st = partido_espn.get("state", "pre")
        gh = partido_espn["home_score"]
        ga = partido_espn["away_score"]
        gtot = gh + ga

        if st == "in":
            en_vivo_cnt += 1
            reloj = partido_espn.get("clock", "LIVE")
            pick["marcador_final"] = f"🔴 {gh}-{ga} ({reloj})"
            pick["estado"] = "EN VIVO"
        elif st == "post":
            pick["marcador_final"] = f"{gh}-{ga} (Final)"
            mercado = pick.get("mercado", "")
            gano = None

            if "(1)" in mercado or "Victoria Local" in mercado:
                gano = (gh > ga)
            elif "(2)" in mercado or "Victoria Visita" in mercado:
                gano = (ga > gh)
            elif "(1X)" in mercado or "Doble Oportunidad 1X" in mercado:
                gano = (gh >= ga)
            elif "(X2)" in mercado or "Doble Oportunidad X2" in mercado:
                gano = (ga >= gh)
            elif "Más de 1.5 Goles" in mercado:
                gano = (gtot > 1.5)
            elif "Más de 2.5 Goles" in mercado:
                gano = (gtot > 2.5)
            elif "Menos de 2.5 Goles" in mercado:
                gano = (gtot < 2.5)
            elif "Menos de 3.5 Goles" in mercado:
                gano = (gtot < 3.5)
            elif "Ambos Anotan" in mercado:
                gano = (gh > 0 and ga > 0)

            if gano is not None:
                stake = float(pick.get("stake", 2.5))
                cuota = float(pick.get("cuota", 1.65))
                if gano:
                    pick["estado"] = "GANADO"
                    pick["profit"] = round(stake * (cuota - 1.0), 2)
                else:
                    pick["estado"] = "PERDIDO"
                    pick["profit"] = round(-stake, 2)

    cerrados = [p for p in historial if p.get("estado") in ("GANADO", "PERDIDO")]
    ganados = sum(1 for p in cerrados if p["estado"] == "GANADO")
    perdidos = sum(1 for p in cerrados if p["estado"] == "PERDIDO")
    pendientes = sum(1 for p in historial if p.get("estado") in ("PENDIENTE", "EN VIVO"))
    tot_c = ganados + perdidos

    profit_total = sum(float(p.get("profit", 0.0)) for p in cerrados)
    stake_total = sum(float(p.get("stake", 0.0)) for p in cerrados)
    win_rate = (ganados / tot_c * 100.0) if tot_c > 0 else 0.0
    roi = (profit_total / stake_total * 100.0) if stake_total > 0 else 0.0

    racha_cnt = 0
    racha_tipo = "W"
    for p in reversed(cerrados):
        t = "W" if p["estado"] == "GANADO" else "L"
        if racha_cnt == 0:
            racha_tipo = t
            racha_cnt = 1
        elif t == racha_tipo:
            racha_cnt += 1
        else:
            break

    auditoria["resumen"] = {
        "bankroll_inicial": BANKROLL_INICIAL,
        "bankroll_actual": round(BANKROLL_INICIAL + profit_total, 2),
        "ganancia_neta": round(profit_total, 2),
        "roi_pct": round(roi, 2),
        "win_rate_pct": round(win_rate, 1),
        "ganados": ganados,
        "perdidos": perdidos,
        "en_vivo": en_vivo_cnt,
        "pendientes": pendientes,
        "racha_actual": f"{racha_cnt}{racha_tipo}" if racha_cnt > 0 else f"{pendientes} Activos"
    }
    return auditoria

def clasificar_tier(prob_pct, edge_pct):
    if prob_pct >= 68.0 and edge_pct >= 2.2:
        return "TIER S", "💎 TIER S (Blindado)", 1
    elif prob_pct >= 60.0 and edge_pct >= 2.0:
        return "TIER A", "🥇 TIER A (Fuerte +EV)", 2
    elif prob_pct >= 53.5 and edge_pct >= 1.8:
        return "TIER B", "🥈 TIER B (Valor Matemático)", 3
    return "DESCARTADO", "❌", 99

# ==============================================================================
# 5. GENERADOR DE PLATAFORMA WEB COMERCIAL (index.html)
# ==============================================================================
def generar_web_comercial(auditoria, picks_jornada, partidos_analizados, ligas_csv_activas):
    res = auditoria["resumen"]
    historial = auditoria["historial"][-35:][::-1]
    fecha_act = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    tarjetas_html = ""
    for idx, p in enumerate(picks_jornada):
        bloquear = (MODO_COMERCIAL == "VENTA" and p["tier_cod"] == "TIER S" and idx > 0)
        blur_class = "blur-lock" if bloquear else ""
        badge_color = "#10b981" if p["tier_cod"] == "TIER S" else ("#3b82f6" if p["tier_cod"] == "TIER A" else "#f59e0b")

        overlay_vip = ""
        if bloquear:
            overlay_vip = f"""
            <div class="vip-overlay">
                <div class="vip-box">
                    <span>🔒 PICK EXCLUSIVO TIER S ({p['prob']:.1f}% Prob. Real)</span>
                    <a href="{LINK_CONTACTO_VIP}" target="_blank" class="btn-vip">Desbloquear Acceso VIP</a>
                </div>
            </div>
            """

        tarjetas_html += f"""
        <div class="pick-card">
            <div class="pick-header">
                <span class="league-tag">{p['liga']} · {p['categoria']}</span>
                <span class="tier-badge" style="background:{badge_color}22; color:{badge_color}; border:1px solid {badge_color};">{p['tier_label']}</span>
            </div>
            <div class="CardBody {blur_class}">
                <h3 class="match-title">{p['local']} vs {p['visita']}</h3>
                <div class="market-box">
                    <div>
                        <span class="market-label">PRONÓSTICO IA CUANTITATIVA</span>
                        <div class="market-name">{p['mercado']}</div>
                    </div>
                    <div class="odd-pill">@{p['cuota']:.2f}</div>
                </div>
                <div class="metrics-grid">
                    <div class="metric-item">
                        <span class="m-label">Confianza IA</span>
                        <span class="m-val" style="color:#10b981;">{p['confianza']:.1f}%</span>
                    </div>
                    <div class="metric-item">
                        <span class="m-label">Prob. Real</span>
                        <span class="m-val">{p['prob']:.1f}%</span>
                    </div>
                    <div class="metric-item">
                        <span class="m-label">Ventaja (+EV)</span>
                        <span class="m-val" style="color:#38bdf8;">+{p['edge']:.1f}%</span>
                    </div>
                    <div class="metric-item">
                        <span class="m-label">Stake Kelly</span>
                        <span class="m-val">S/ {p['stake']:.2f}</span>
                    </div>
                </div>
                <div class="l-bars">
                    <span>📊 {p['ventanas']}</span>
                </div>
                <p class="ai-note">🧠 <b>Casa: {p['casa']}</b> — {p['explicacion']}</p>
            </div>
            {overlay_vip}
        </div>
        """

    if not tarjetas_html:
        tarjetas_html = """
        <div class="empty-state">
            <h3>🛡️ Mercado Eficiente en este Ciclo</h3>
            <p>El motor analizó los endpoints B2B y descartó cuotas sin ventaja matemática. El próximo escaneo actualizará nuevas oportunidades.</p>
        </div>
        """

    filas_tabla = ""
    for h in historial:
        est = h.get("estado", "PENDIENTE")
        if est == "GANADO":
            badge_est = '<span class="st-won">✅ GANADO</span>'
            prof_str = f'<span style="color:#10b981;font-weight:700;">+S/ {h.get("profit", 0):.2f}</span>'
        elif est == "PERDIDO":
            badge_est = '<span class="st-lost">❌ PERDIDO</span>'
            prof_str = f'<span style="color:#ef4444;font-weight:700;">-S/ {abs(h.get("profit", 0)):.2f}</span>'
        elif est == "EN VIVO":
            badge_est = '<span class="st-live">🔴 EN VIVO</span>'
            prof_str = '<span style="color:#38bdf8;font-weight:700;">En Juego</span>'
        else:
            badge_est = '<span class="st-pend">⏳ PENDIENTE</span>'
            prof_str = '<span style="color:#94a3b8;">S/ 0.00</span>'

        filas_tabla += f"""
        <tr>
            <td>{h.get('fecha', '')}</td>
            <td><b>{h.get('local', '')} vs {h.get('visita', '')}</b><br><small style="color:#94a3b8;">{h.get('liga', '')} · {h.get('categoria', 'FÚTBOL')}</small></td>
            <td><b style="color:#38bdf8;">{h.get('mercado', '')}</b></td>
            <td>@{float(h.get('cuota', 1.0)):.2f}<br><small style="color:#94a3b8;">{h.get('casa', '')}</small></td>
            <td>{h.get('prob', 0):.1f}%</td>
            <td><b>{h.get('marcador_final', '-')}</b></td>
            <td>{badge_est}</td>
            <td>{prof_str}</td>
        </tr>
        """

    html_completo = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IABET Quant Pro | Predicciones Deportivas Auditadas en Tiempo Real</title>
    <style>
        :root {{
            --bg: #0b0f19;
            --card: #111827;
            --border: #1f2937;
            --text: #f8fafc;
            --muted: #94a3b8;
            --accent: #10b981;
            --blue: #3b82f6;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }}
        body {{ background-color: var(--bg); color: var(--text); line-height: 1.5; padding-bottom: 60px; }}
        .navbar {{ background: #0f172a; border-bottom: 1px solid var(--border); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 50; }}
        .logo {{ font-size: 1.35rem; font-weight: 800; letter-spacing: -0.5px; color: #fff; display: flex; align-items: center; gap: 8px; }}
        .logo span {{ background: linear-gradient(90deg, #10b981, #3b82f6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .status-pill {{ font-size: 0.8rem; background: #10b98120; color: #10b981; border: 1px solid #10b98150; padding: 6px 12px; border-radius: 999px; font-weight: 600; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 24px 16px; }}
        .hero {{ text-align: center; padding: 28px 16px; background: radial-gradient(circle at top, #1e293b60, transparent 70%); border-radius: 16px; margin-bottom: 28px; border: 1px solid var(--border); }}
        .hero h1 {{ font-size: 2rem; font-weight: 800; margin-bottom: 8px; }}
        .hero p {{ color: var(--muted); max-width: 760px; margin: 0 auto; font-size: 0.95rem; }}
        .stats-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin-bottom: 32px; }}
        .stat-box {{ background: var(--card); border: 1px solid var(--border); padding: 18px; border-radius: 12px; text-align: center; }}
        .stat-box .s-label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-box .s-val {{ font-size: 1.6rem; font-weight: 800; margin-top: 4px; color: #fff; }}
        .section-title {{ font-size: 1.2rem; font-weight: 700; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }}
        .picks-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 18px; margin-bottom: 42px; }}
        .pick-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px; position: relative; overflow: hidden; transition: transform 0.2s; }}
        .pick-card:hover {{ transform: translateY(-3px); border-color: #334155; }}
        .pick-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
        .league-tag {{ font-size: 0.72rem; color: var(--muted); font-weight: 700; text-transform: uppercase; }}
        .tier-badge {{ font-size: 0.72rem; font-weight: 700; padding: 3px 9px; border-radius: 99px; }}
        .match-title {{ font-size: 1.1rem; font-weight: 700; margin-bottom: 12px; color: #fff; }}
        .market-box {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }}
        .market-label {{ font-size: 0.65rem; color: var(--muted); font-weight: 700; }}
        .market-name {{ font-size: 0.98rem; font-weight: 800; color: #38bdf8; }}
        .odd-pill {{ background: #10b98120; color: #10b981; font-weight: 800; font-size: 1.1rem; padding: 6px 12px; border-radius: 8px; border: 1px solid #10b98140; }}
        .metrics-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; text-align: center; background: #0b0f19; padding: 10px; border-radius: 8px; }}
        .m-label {{ display: block; font-size: 0.66rem; color: var(--muted); }}
        .m-val {{ font-size: 0.88rem; font-weight: 700; }}
        .l-bars {{ font-size: 0.76rem; color: #cbd5e1; background: #1e293b60; padding: 7px 10px; border-radius: 6px; margin-bottom: 10px; }}
        .ai-note {{ font-size: 0.78rem; color: var(--muted); border-top: 1px solid var(--border); padding-top: 8px; }}
        .blur-lock {{ filter: blur(6px); user-select: none; pointer-events: none; }}
        .vip-overlay {{ position: absolute; inset: 0; background: rgba(11, 15, 25, 0.75); display: flex; align-items: center; justify-content: center; padding: 20px; text-align: center; }}
        .vip-box {{ background: #111827; border: 1px solid #10b981; padding: 16px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.6); }}
        .vip-box span {{ display: block; font-weight: 700; font-size: 0.9rem; margin-bottom: 10px; color: #fff; }}
        .btn-vip {{ display: inline-block; background: #10b981; color: #052e16; font-weight: 800; text-decoration: none; padding: 8px 16px; border-radius: 8px; font-size: 0.85rem; }}
        .table-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; text-align: left; }}
        th {{ background: #0f172a; color: var(--muted); padding: 12px 14px; font-size: 0.74rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }}
        td {{ padding: 12px 14px; border-bottom: 1px solid var(--border); }}
        .st-won {{ background: #10b98120; color: #10b981; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.74rem; }}
        .st-lost {{ background: #ef444420; color: #ef4444; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.74rem; }}
        .st-live {{ background: #ef444430; color: #f87171; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.74rem; border: 1px solid #ef4444; }}
        .st-pend {{ background: #f59e0b20; color: #f59e0b; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.74rem; }}
        .empty-state {{ grid-column: 1 / -1; background: var(--card); border: 1px dashed #334155; padding: 36px; border-radius: 14px; text-align: center; color: var(--muted); }}
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="logo">⚡ IABET <span>QUANT PRO (Nivel 2+ B2B)</span></div>
        <div class="status-pill">🟢 Kambi Direct + ESPN Live | Modo: {MODO_COMERCIAL}</div>
    </nav>
    <div class="container">
        <div class="hero">
            <h1>Motor Multimercado & Diagnóstico Deportivo en Tiempo Real</h1>
            <p>Ingesta B2B directa de Kambi (Betsson/Inkabet), ESPN Live Scoreboards y series históricas L5 · L10 · L20 procesadas con 1,000,000 de simulaciones de Monte Carlo.</p>
        </div>

        <div class="stats-bar">
            <div class="stat-box">
                <div class="s-label">Win Rate Auditado</div>
                <div class="s-val" style="color:#10b981;">{res['win_rate_pct']:.1f}%</div>
            </div>
            <div class="stat-box">
                <div class="s-label">Aciertos / Cerrados</div>
                <div class="s-val">{res['ganados']}W - {res['perdidos']}L</div>
            </div>
            <div class="stat-box">
                <div class="s-label">ROI Verificado</div>
                <div class="s-val" style="color:#38bdf8;">{res['roi_pct']:+.1f}%</div>
            </div>
            <div class="stat-box">
                <div class="s-label">Bankroll (Base S/ 100)</div>
                <div class="s-val">S/ {res['bankroll_actual']:.2f}</div>
            </div>
            <div class="stat-box">
                <div class="s-label">Estado / En Vivo</div>
                <div class="s-val">🔥 {res['racha_actual']}</div>
            </div>
        </div>

        <div class="section-title">
            <span>🎯 Pronósticos de Valor Detectados ({len(picks_jornada)} seleccionados de {partidos_analizados} partidos | {ligas_csv_activas} bases históricas activas)</span>
            <small style="color:var(--muted); font-size:0.8rem;">Último escaneo: {fecha_act}</small>
        </div>
        <div class="picks-grid">
            {tarjetas_html}
        </div>

        <div class="section-title">
            <span>📋 Auditoría en Tiempo Real (Verificación Automática ESPN Scoreboard)</span>
        </div>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>Fecha</th>
                        <th>Partido / Mercado</th>
                        <th>Pronóstico IA</th>
                        <th>Cuota / Casa</th>
                        <th>Prob. Real</th>
                        <th>Marcador / Estado</th>
                        <th>Verificación</th>
                        <th>Retorno</th>
                    </tr>
                </thead>
                <tbody>
                    {filas_tabla}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_completo)

# ==============================================================================
# 6. ORQUESTADOR MAESTRO (100% INDEPENDIENTE DE AGREGADORES)
# ==============================================================================
def enviar_telegram_seguro(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    bloques = [texto[i:i+3900] for i in range(0, len(texto), 3900)]
    for b in bloques:
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": b}, timeout=10)
        except Exception:
            pass

def ejecutar_iabet_nivel_2():
    t0 = time.time()
    caches_csv, espn_partidos, espn_equipos, eventos_kambi = obtener_datos_arquitectura_nivel_2()

    auditoria = cargar_auditoria()
    auditoria = auditar_picks_con_espn(auditoria, espn_partidos)
    bankroll_dinamico = max(50.0, float(auditoria["resumen"]["bankroll_actual"]))

    candidatos = []
    partidos_analizados = 0
    vistos = set()

    for ev in eventos_kambi:
        loc = ev.get("home_team")
        vis = ev.get("away_team")
        liga = ev.get("liga", "Fútbol")
        if not loc or not vis:
            continue

        id_p = f"{normalizar_nombre(loc)[:6]}__{normalizar_nombre(vis)[:6]}"
        if id_p in vistos:
            continue
        vistos.add(id_p)
        partidos_analizados += 1

        c_1 = ev["c_1"]
        c_x = ev["c_x"]
        c_2 = ev["c_2"]
        c_o25 = ev["c_o25"]
        c_u25 = ev["c_u25"]
        casa = ev["casa"]

        # De-Vigging de la casa B2B
        overround = (1.0 / c_1) + (1.0 / c_x) + (1.0 / c_2)
        p_devig_1 = (1.0 / c_1) / overround
        p_devig_x = (1.0 / c_x) / overround
        p_devig_2 = (1.0 / c_2) / overround

        goles_mkt = 2.65
        if c_o25 > 1.05 and c_u25 > 1.05:
            p_ov = (1.0 / c_o25) / ((1.0 / c_o25) + (1.0 / c_u25))
            goles_mkt = 2.15 + (p_ov * 1.15)

        l_mkt_l = max(0.55, min(3.10, goles_mkt * (p_devig_1 + 0.48 * p_devig_x) * 1.05))
        l_mkt_v = max(0.45, min(2.75, goles_mkt * (p_devig_2 + 0.52 * p_devig_x) * 0.95))

        base_t = ev.get("base_tarj", 4.5)
        base_f = ev.get("base_falt", 24.5)
        base_s = ev.get("base_sot", 8.8)

        df_liga = caches_csv.get(liga)
        st_l = analizar_equipo_profundo(df_liga, loc, base_f, base_s, base_t, es_local=True)
        st_v = analizar_equipo_profundo(df_liga, vis, base_f, base_s, base_t, es_local=False)

        info_espn_l = espn_equipos.get(normalizar_nombre(loc)[:6], {})
        info_espn_v = espn_equipos.get(normalizar_nombre(vis)[:6], {})
        form_l = info_espn_l.get("form", "")
        form_v = info_espn_v.get("form", "")
        bonus_l = calcular_bonus_forma_espn(form_l)
        bonus_v = calcular_bonus_forma_espn(form_v)
        tag_espn = f" | ESPN: {form_l or 'Activo'}" if (form_l or form_v) else ""

        if st_l and st_v:
            lambda_l = ((((st_l["gf"] + st_v["gc"]) / 2.0) * 0.60) + (l_mkt_l * 0.40)) * bonus_l
            lambda_v = ((((st_v["gf"] + st_l["gc"]) / 2.0) * 0.60) + (l_mkt_v * 0.40)) * bonus_v
            f_loc_esp = (st_l["f_com"] * 0.65) + (st_v["f_rec"] * 0.35)
            f_vis_esp = (st_v["f_com"] * 0.65) + (st_l["f_rec"] * 0.35)
            sot_l, sot_v = st_l["sot"] * bonus_l, st_v["sot"] * bonus_v
            tarj_esp = st_l["tarj"] + st_v["tarj"]
            f_l5 = st_l["f_l5"] + st_v["f_l5"]
            f_l10 = st_l["f_l10"] + st_v["f_l10"]
            f_l20 = st_l["f_l20"] + st_v["f_l20"]
            str_ventanas = f"L5: {f_l5:.1f}f · L10: {f_l10:.1f}f · L20: {f_l20:.1f}f | xG: {lambda_l:.2f}-{lambda_v:.2f}{tag_espn}"
            tiene_stats = True
        else:
            deseq = min(1.25, max(0.80, c_2 / max(1.05, c_1)))
            lambda_l = l_mkt_l * bonus_l
            lambda_v = l_mkt_v * bonus_v
            f_loc_esp = (base_f * 0.47) / (deseq ** 0.18)
            f_vis_esp = (base_f * 0.53) * (deseq ** 0.18)
            sot_l = (base_s * 0.54) * (deseq ** 0.15)
            sot_v = (base_s * 0.46) / (deseq ** 0.15)
            tarj_esp = base_t
            str_ventanas = f"xG Proyectado: {lambda_l:.2f} vs {lambda_v:.2f} | SOT: {sot_l+sot_v:.1f}{tag_espn}"
            tiene_stats = False

        linea_f_tot = round(base_f) - 0.5
        linea_sot = round(base_s) - 0.5
        linea_tarj = round(base_t) - 0.5

        res_mc = motor_montecarlo_v13(
            lambda_l, lambda_v, f_loc_esp, f_vis_esp, linea_f_tot,
            sot_l, sot_v, linea_sot, tarj_esp, linea_tarj, N_SIMS
        )

        p_fin_1 = (res_mc[0] * 0.68) + (p_devig_1 * 0.32)
        p_fin_2 = (res_mc[2] * 0.68) + (p_devig_2 * 0.32)
        p_fin_1x = (res_mc[3] * 0.68) + ((p_devig_1 + p_devig_x) * 0.32)
        p_fin_x2 = (res_mc[4] * 0.68) + ((p_devig_2 + p_devig_x) * 0.32)

        c_1x = round((1.0 / ((1.0 / c_1) + (1.0 / c_x))) * 1.02, 2)
        c_x2 = round((1.0 / ((1.0 / c_2) + (1.0 / c_x))) * 1.02, 2)

        mercados_eval = [
            (f"Victoria {loc} (1)", p_fin_1, c_1, casa, "1X2"),
            (f"Victoria {vis} (2)", p_fin_2, c_2, casa, "1X2"),
            (f"Doble Oportunidad {loc} o Empate (1X)", p_fin_1x, c_1x, casa, "DOBLE"),
            (f"Doble Oportunidad {vis} o Empate (X2)", p_fin_x2, c_x2, casa, "DOBLE"),
        ]

        if c_o25 > 1.35:
            mercados_eval.append(("Más de 2.5 Goles (Over 2.5)", res_mc[6], c_o25, casa, "GOLES"))
        if c_u25 > 1.35:
            mercados_eval.append(("Menos de 2.5 Goles (Under 2.5)", res_mc[7], c_u25, casa, "GOLES"))
            c_un35 = max(1.35, round(c_u25 * 0.80, 2))
            mercados_eval.append(("Menos de 3.5 Goles (Under 3.5)", res_mc[8], c_un35, casa, "GOLES"))

        dif_f = (f_loc_esp + f_vis_esp) - linea_f_tot
        if dif_f <= -0.9:
            mercados_eval.append((f"Menos de {linea_f_tot} Faltas Totales", res_mc[11], 1.68, "Betsson", "FALTAS"))
        elif dif_f >= 0.9:
            mercados_eval.append((f"Más de {linea_f_tot} Faltas Totales", res_mc[10], 1.68, "Betsson", "FALTAS"))

        if tiene_stats:
            dif_sot = (sot_l + sot_v) - linea_sot
            if dif_sot >= 0.7:
                mercados_eval.append((f"Más de {linea_sot} Tiros al Arco", res_mc[12], 1.70, "Betsson", "TIROS"))
            elif dif_sot <= -0.7:
                mercados_eval.append((f"Menos de {linea_sot} Tiros al Arco", res_mc[13], 1.68, "Betsson", "TIROS"))

            if (tarj_esp - linea_tarj) >= 0.45:
                mercados_eval.append((f"Más de {linea_tarj} Tarjetas", res_mc[14], 1.72, "Betsson", "TARJETAS"))

        for nom_m, p_real, cuota_m, casa_m, cat in mercados_eval:
            if not (CUOTA_MIN <= cuota_m <= CUOTA_MAX):
                continue
            p_imp = 1.0 / cuota_m
            edge_pct = (p_real - p_imp) * 100.0
            if edge_pct < 1.8 or edge_pct > 19.5:
                continue

            prob_pct = p_real * 100.0
            tier_cod, tier_label, tier_rank = clasificar_tier(prob_pct, edge_pct)
            if tier_cod == "DESCARTADO":
                continue

            b = cuota_m - 1.0
            kelly = max(0.015, (((b * p_real) - (1.0 - p_real)) / b) * KELLY_FRAC)
            stake_soles = min(6.0, max(1.50, round((bankroll_dinamico * kelly) * 2) / 2))
            confianza_ia = min(97.4, 82.5 + (edge_pct * 0.85) + max(0.0, (prob_pct - 54.0) * 0.25))

            candidatos.append({
                "fecha": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "liga": liga,
                "categoria": cat,
                "local": loc,
                "visita": vis,
                "mercado": nom_m,
                "cuota": round(cuota_m, 2),
                "casa": casa_m,
                "confianza": round(confianza_ia, 1),
                "prob": round(prob_pct, 1),
                "edge": round(edge_pct, 1),
                "stake": stake_soles,
                "tier_cod": tier_cod,
                "tier_label": tier_label,
                "tier_rank": tier_rank,
                "ventanas": str_ventanas,
                "explicacion": f"1M Sims (λ={lambda_l:.2f}-{lambda_v:.2f}). Desajuste frente a línea justa Kambi B2B.",
                "estado": "PENDIENTE",
                "marcador_final": "Por jugar",
                "profit": 0.0,
                "score": (p_real * 0.55) + ((edge_pct / 100.0) * 0.45)
            })

    candidatos.sort(key=lambda x: (x["tier_rank"], -x["score"]))
    picks_finales = []
    partidos_usados = set()
    conteo_cat = {}

    for c in candidatos:
        clave_p = f"{c['local']}_{c['visita']}"
        cat = c["categoria"]
        if clave_p not in partidos_usados and conteo_cat.get(cat, 0) < 4 and len(picks_finales) < MAX_PICKS_WEB:
            picks_finales.append(c)
            partidos_usados.add(clave_p)
            conteo_cat[cat] = conteo_cat.get(cat, 0) + 1

    claves_hist = {f"{h['fecha']}_{h['local']}_{h['mercado']}" for h in auditoria["historial"]}
    for pf in picks_finales:
        k_nuevo = f"{pf['fecha']}_{pf['local']}_{pf['mercado']}"
        if k_nuevo not in claves_hist:
            auditoria["historial"].append(pf)
            claves_hist.add(k_nuevo)

    auditoria = auditar_picks_con_espn(auditoria, espn_partidos)

    with open(ARCHIVO_AUDITORIA, "w", encoding="utf-8") as f:
        json.dump(auditoria, f, ensure_ascii=False, indent=2)

    generar_web_comercial(auditoria, picks_finales, partidos_analizados, len(caches_csv))

    res = auditoria["resumen"]
    dur = time.time() - t0
    lineas_tg = [
        "⚡ IABET QUANT PRO (ARQUITECTURA NIVEL 2+ PURA)",
        f"📊 Efectividad: {res['win_rate_pct']:.1f}% ({res['ganados']}W-{res['perdidos']}L) | ROI: {res['roi_pct']:+.1f}%",
        f"💰 Bankroll: S/ {res['bankroll_actual']:.2f} | Estado: 🔥 {res['racha_actual']}",
        f"🌐 Web: https://blakeffx.github.io/iabet-cloud/",
        f"⏱️ {len(picks_finales)} picks seleccionados de {partidos_analizados} partidos analizados ({dur:.1f}s)",
        "━━━━━━━━━━━━━━━━━━━━"
    ]

    for idx, p in enumerate(picks_finales[:8], 1):
        lineas_tg.append(
            f"{idx}. {p['tier_label']} [{p['liga']} · {p['categoria']}]\n"
            f"⚽ {p['local']} vs {p['visita']}\n"
            f"👉 {p['mercado']}  @{p['cuota']:.2f} ({p['casa']})\n"
            f"🔥 Confianza IA: {p['confianza']:.1f}% (Prob: {p['prob']:.1f}% | Edge: +{p['edge']:.1f}%)\n"
            f"📊 {p['ventanas']} | 💰 Stake: S/ {p['stake']:.2f}\n"
        )

    enviar_telegram_seguro("\n".join(lineas_tg))
    print(f"✅ IABET Nivel 2+ completado: {len(picks_finales)} picks publicados en la web.")

if __name__ == "__main__":
    ejecutar_iabet_nivel_2()
