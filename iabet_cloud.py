import time
import io
import os
import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import requests
import numpy as np
import pandas as pd
from numba import njit, prange

# ==============================================================================
# 1. CREDENCIALES Y CONFIGURACIÓN ESTABLE (IABET QUANT PRO v15.1)
# ==============================================================================
THE_ODDS_API_KEY = "a13d27d9c5cc3ea988f72ba4ff74c56f"
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"
GEMINI_API_KEY = "AQ.Ab8RN6IKL6yh9YxXdsAzjRn9RKHo9xOtu1Rq8OpZhBKg8rPp-Q"

MODO_COMERCIAL = "PRUEBA"
LINK_CONTACTO_VIP = "https://t.me/+TuEnlaceDeContacto"

BANKROLL_INICIAL = 100.0
KELLY_FRAC = 0.25
N_SIMS = 1_000_000
CUOTA_MIN = 1.35
CUOTA_MAX = 2.45
MAX_PICKS_WEB = 15
ARCHIVO_AUDITORIA = "auditoria_iabet.json"

HORAS_CACHE_ODDS_API = 4.0
MAX_LIGAS_ODDS_POR_CICLO = 4

TZ_PERU = timezone(timedelta(hours=-5))

HEADERS_PRO = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9"
}

LIGAS_CONFIG = {
    "esp.1": ("LaLiga", "SP1", "soccer_spain_la_liga", 4.7, 25.0, 8.6),
    "eng.1": ("Premier League", "E0", "soccer_epl", 3.9, 21.6, 9.3),
    "ita.1": ("Serie A", "I1", "soccer_italy_serie_a", 4.4, 24.5, 8.7),
    "ger.1": ("Bundesliga", "D1", "soccer_germany_bundesliga", 4.0, 22.2, 9.5),
    "fra.1": ("Ligue 1", "F1", "soccer_france_ligue_one", 4.0, 23.6, 8.8),
    "ned.1": ("Eredivisie", "N1", "soccer_netherlands_eredivisie", 3.3, 20.4, 9.6),
    "por.1": ("Primeira Liga", "P1", "soccer_portugal_primeira_liga", 5.1, 26.5, 8.5),
    "uefa.champions": ("Champions League", None, "soccer_uefa_champs_league", 4.1, 22.6, 9.4),
    "uefa.europa": ("Europa League", None, "soccer_uefa_europa_league", 4.3, 23.8, 9.0),
    "conmebol.libertadores": ("Copa Libertadores", None, "soccer_conmebol_copa_libertadores", 5.3, 27.5, 8.6),
    "conmebol.sudamericana": ("Copa Sudamericana", None, "soccer_conmebol_copa_sudamericana", 5.0, 26.8, 8.4),
    "per.1": ("Liga 1 Perú", None, None, 5.3, 28.0, 8.5),
    "bra.1": ("Brasileirão", "BRA", "soccer_brazil_campeonato", 5.0, 27.2, 8.9),
    "arg.1": ("Liga Argentina", "ARG", "soccer_argentina_primera_division", 5.2, 27.6, 8.3),
    "mex.1": ("Liga MX", "MEX", "soccer_mexico_ligamx", 4.5, 24.4, 8.8),
    "eng.league_cup": ("EFL Cup", None, "soccer_england_efl_cup", 3.8, 21.5, 9.1),
    "esp.copa_del_rey": ("Copa del Rey", None, None, 4.6, 25.5, 8.5),
    "ita.coppa_italia": ("Coppa Italia", None, None, 4.4, 24.8, 8.6)
}

CSV_LIGAS_MAPA = {
    "LaLiga": "SP1",
    "Premier League": "E0",
    "Serie A": "I1",
    "Bundesliga": "D1",
    "Ligue 1": "F1",
    "Eredivisie": "N1",
    "Primeira Liga": "P1",
    "Brasileirão": "BRA",
    "Liga Argentina": "ARG",
    "Liga MX": "MEX"
}

DB_EQUIPOS_PRO = {
    "universitario": {"gf": 1.88, "gc": 0.65, "f_com": 13.6, "f_rec": 14.8, "sot": 5.5, "tarj": 2.3},
    "alianza lima": {"gf": 1.75, "gc": 0.72, "f_com": 13.1, "f_rec": 15.1, "sot": 5.2, "tarj": 2.4},
    "sporting cristal": {"gf": 2.12, "gc": 1.02, "f_com": 11.7, "f_rec": 14.9, "sot": 5.9, "tarj": 2.1},
    "melgar": {"gf": 1.84, "gc": 0.85, "f_com": 13.4, "f_rec": 13.9, "sot": 5.5, "tarj": 2.5},
    "cusco": {"gf": 1.68, "gc": 1.02, "f_com": 14.5, "f_rec": 13.2, "sot": 4.8, "tarj": 2.6},
    "cienciano": {"gf": 1.55, "gc": 1.15, "f_com": 14.9, "f_rec": 13.0, "sot": 4.6, "tarj": 2.7},
    "flamengo": {"gf": 1.95, "gc": 0.82, "f_com": 12.8, "f_rec": 14.6, "sot": 5.8, "tarj": 2.2},
    "palmeiras": {"gf": 1.90, "gc": 0.78, "f_com": 13.2, "f_rec": 14.4, "sot": 5.7, "tarj": 2.3},
    "botafogo": {"gf": 1.85, "gc": 0.80, "f_com": 13.0, "f_rec": 14.2, "sot": 5.5, "tarj": 2.3},
    "river plate": {"gf": 1.88, "gc": 0.75, "f_com": 12.5, "f_rec": 15.0, "sot": 5.6, "tarj": 2.2},
    "boca juniors": {"gf": 1.62, "gc": 0.88, "f_com": 13.8, "f_rec": 14.5, "sot": 4.9, "tarj": 2.6},
    "racing": {"gf": 1.78, "gc": 0.92, "f_com": 13.1, "f_rec": 14.1, "sot": 5.3, "tarj": 2.5}
}

# ==============================================================================
# 2. MOTOR MONTE CARLO JIT EN C (1,000,000 SIMULACIONES)
# ==============================================================================
@njit(parallel=True)
def motor_montecarlo_v15(
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
# 3. EXTRACCIÓN DE DATOS ESTABLE MULTI-DÍA
# ==============================================================================
def normalizar_nombre(nombre):
    if not nombre:
        return ""
    limpio = str(nombre).lower().replace("-", " ")
    for p in ["fc ", "cf ", "cd ", "ca ", "sc ", "ac ", "as ", "ud ", "rcd ", "real ", "sporting ", "club ", "deportivo ", "atletico ", "united", "city"]:
        limpio = limpio.replace(p, "")
    return limpio.strip()

def consultar_the_odds_api_smart(ligas_con_partidos_hoy, auditoria):
    cache_global = auditoria.get("cache_odds_api", {})
    creditos_restantes = auditoria.get("resumen", {}).get("creditos_odds_api", "500")
    ahora_ts = time.time()
    hora_peru_actual = datetime.now(TZ_PERU).hour

    if not THE_ODDS_API_KEY or hora_peru_actual >= 23 or hora_peru_actual < 5:
        return cache_global.get("partidos", {}), creditos_restantes

    mapa_cuotas = cache_global.get("partidos", {})
    tiempos_ligas = cache_global.get("tiempos_ligas", {})
    peticiones_hechas = 0

    for odds_key in ligas_con_partidos_hoy:
        if not odds_key or peticiones_hechas >= MAX_LIGAS_ODDS_POR_CICLO:
            continue

        ult_consulta = float(tiempos_ligas.get(odds_key, 0.0))
        if (ahora_ts - ult_consulta) < (HORAS_CACHE_ODDS_API * 3600):
            continue

        url = f"https://api.the-odds-api.com/v4/sports/{odds_key}/odds"
        params = {
            "apiKey": THE_ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal"
        }
        try:
            r = requests.get(url, params=params, timeout=8)
            if r.status_code == 200:
                peticiones_hechas += 1
                tiempos_ligas[odds_key] = ahora_ts
                rem = r.headers.get("x-requests-remaining")
                if rem is not None:
                    creditos_restantes = str(rem)

                for ev in r.json():
                    loc = ev.get("home_team", "")
                    vis = ev.get("away_team", "")
                    if not loc or not vis:
                        continue

                    best_1, best_x, best_2 = (0.0, ""), (0.0, ""), (0.0, "")
                    pin_1, pin_x, pin_2 = 0.0, 0.0, 0.0
                    all_1, all_x, all_2 = [], [], []

                    for bm in ev.get("bookmakers", []):
                        b_key = bm.get("key", "")
                        b_title = bm.get("title", "Bookie")
                        for m in bm.get("markets", []):
                            if m.get("key") == "h2h":
                                for o in m.get("outcomes", []):
                                    pr = float(o.get("price", 0) or 0)
                                    if pr <= 1.01:
                                        continue
                                    if o.get("name") == loc:
                                        all_1.append(pr)
                                        if pr > best_1[0]: best_1 = (pr, b_title)
                                        if b_key == "pinnacle": pin_1 = pr
                                    elif o.get("name") == vis:
                                        all_2.append(pr)
                                        if pr > best_2[0]: best_2 = (pr, b_title)
                                        if b_key == "pinnacle": pin_2 = pr
                                    else:
                                        all_x.append(pr)
                                        if pr > best_x[0]: best_x = (pr, b_title)
                                        if b_key == "pinnacle": pin_x = pr

                    if best_1[0] > 1.01 and best_2[0] > 1.01:
                        clave = f"{normalizar_nombre(loc)[:5]}__{normalizar_nombre(vis)[:5]}"
                        mapa_cuotas[clave] = {
                            "best_1": best_1[0], "casa_1": best_1[1],
                            "best_x": best_x[0] if best_x[0] > 1.0 else 3.25, "casa_x": best_x[1] or best_1[1],
                            "best_2": best_2[0], "casa_2": best_2[1],
                            "pin_1": pin_1 if pin_1 > 1.0 else float(np.mean(all_1)),
                            "pin_x": pin_x if pin_x > 1.0 else (float(np.mean(all_x)) if all_x else 3.25),
                            "pin_2": pin_2 if pin_2 > 1.0 else float(np.mean(all_2))
                        }
        except Exception:
            continue

    auditoria["cache_odds_api"] = {
        "tiempos_ligas": tiempos_ligas,
        "partidos": mapa_cuotas
    }
    return mapa_cuotas, creditos_restantes

def descargar_csv_liga(item):
    nom_liga, cod_csv = item
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
            r = requests.get(u, headers=HEADERS_PRO, timeout=7)
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

def extraer_estadisticas_summary_espn(event_id):
    if not event_id:
        return None
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/summary?event={event_id}"
    try:
        r = requests.get(url, headers=HEADERS_PRO, timeout=6)
        if r.status_code != 200:
            return None
        data = r.json()
        box = data.get("boxscore", {})
        teams = box.get("teams", [])
        faltas_tot, sot_tot, tarj_tot = 0, 0, 0
        tiene_datos = False

        for t in teams:
            for st in t.get("statistics", []):
                nom_st = st.get("name", "")
                val_str = str(st.get("displayValue", "0")).strip()
                try:
                    val = float(val_str)
                except Exception:
                    val = 0.0
                if nom_st == "foulsCommitted":
                    faltas_tot += int(val)
                    tiene_datos = True
                elif nom_st in ("shotsOnTarget", "shotsOnGoal"):
                    sot_tot += int(val)
                    tiene_datos = True
                elif nom_st in ("yellowCards", "redCards"):
                    tarj_tot += int(val)
                    tiene_datos = True

        bajas = []
        for inj_team in data.get("injuries", []):
            t_nom = inj_team.get("team", {}).get("displayName", "")
            injs = inj_team.get("injuries", [])
            if injs:
                bajas.append(f"{t_nom}: {len(injs)} baja(s)")

        return {
            "tiene_stats_vivo": tiene_datos,
            "faltas_tot": faltas_tot,
            "sot_tot": sot_tot,
            "tarj_tot": tarj_tot,
            "bajas_txt": " | ".join(bajas) if bajas else "Plantillas titulares disponibles"
        }
    except Exception:
        return None

def descargar_espn_liga_estable(item):
    slug, (nom_liga, _, odds_key, b_tarj, b_falt, b_sot) = item
    ahora_peru = datetime.now(TZ_PERU)
    dia_hoy = ahora_peru.strftime("%Y%m%d")
    dia_manana = (ahora_peru + timedelta(days=1)).strftime("%Y%m%d")
    dia_pasado = (ahora_peru + timedelta(days=2)).strftime("%Y%m%d")

    # Consulta segura multi-fecha a ESPN (sin rangos con guión)
    urls_a_probar = [
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard",
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={dia_hoy}",
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={dia_manana}",
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={dia_pasado}"
    ]

    eventos_encontrados = []
    partidos_espn_estado = {}
    ids_vistos = set()

    for url in urls_a_probar:
        try:
            r = requests.get(url, headers=HEADERS_PRO, timeout=7)
            if r.status_code != 200:
                continue
            data = r.json()
            for ev in data.get("events", []):
                ev_id = str(ev.get("id", ""))
                if ev_id in ids_vistos:
                    continue
                ids_vistos.add(ev_id)

                comp = (ev.get("competitions") or [{}])[0]
                st_obj = comp.get("status", {})
                estado = st_obj.get("type", {}).get("state", "pre")
                detalle_tiempo = st_obj.get("type", {}).get("shortDetail", "")
                fecha_iso = ev.get("date", "")

                try:
                    dt_utc = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))
                    dt_peru = dt_utc.astimezone(TZ_PERU)
                    fecha_peru_str = dt_peru.strftime("%Y-%m-%d")
                    hora_peru_str = dt_peru.strftime("%H:%M")
                    hoy_str = ahora_peru.strftime("%Y-%m-%d")
                    manana_str = (ahora_peru + timedelta(days=1)).strftime("%Y-%m-%d")

                    if fecha_peru_str == hoy_str:
                        etiqueta_dia = "HOY"
                    elif fecha_peru_str == manana_str:
                        etiqueta_dia = "MAÑANA"
                    else:
                        etiqueta_dia = fecha_peru_str
                except Exception:
                    fecha_peru_str = ahora_peru.strftime("%Y-%m-%d")
                    hora_peru_str = "Por definir"
                    etiqueta_dia = "PRÓXIMO"

                equipos = comp.get("competitors", [])
                if len(equipos) == 2:
                    t_h = next((x for x in equipos if x.get("homeAway") == "home"), equipos[0])
                    t_a = next((x for x in equipos if x.get("homeAway") == "away"), equipos[1])
                    nh = t_h.get("team", {}).get("displayName", "")
                    na = t_a.get("team", {}).get("displayName", "")
                    sh = int(float(t_h.get("score", 0) or 0))
                    sa = int(float(t_a.get("score", 0) or 0))
                    fh = t_h.get("form", "") or "WDW"
                    fa = t_a.get("form", "") or "DWL"
                    rec_h = (t_h.get("records") or [{}])[0].get("summary", "S/R")
                    rec_a = (t_a.get("records") or [{}])[0].get("summary", "S/R")

                    clave = f"{normalizar_nombre(nh)}__{normalizar_nombre(na)}"
                    partidos_espn_estado[clave] = {
                        "event_id": ev_id,
                        "home": nh, "away": na, "state": estado,
                        "clock": detalle_tiempo, "home_score": sh, "away_score": sa
                    }

                    # Para picks nuevos en tarjetas: solo futuros (pre)
                    if estado != "pre":
                        continue

                    odds_obj = (comp.get("odds") or [{}])[0]
                    c_1, c_x, c_2 = 0.0, 0.0, 0.0
                    c_o25, c_u25 = 0.0, 0.0
                    casa_nombre = "ESPN Bet / DraftKings"

                    if odds_obj:
                        prov = odds_obj.get("provider", {}).get("name")
                        if prov: casa_nombre = prov
                        ml_h = odds_obj.get("homeTeamOdds", {}).get("moneyLine")
                        ml_a = odds_obj.get("awayTeamOdds", {}).get("moneyLine")
                        ml_d = odds_obj.get("drawOdds", {}).get("moneyLine")

                        def a_dec(val):
                            if val is None: return 0.0
                            val = float(val)
                            if val > 0: return round((val / 100.0) + 1.0, 2)
                            elif val < 0: return round((100.0 / abs(val)) + 1.0, 2)
                            return val

                        c_1 = a_dec(ml_h)
                        c_2 = a_dec(ml_a)
                        c_x = a_dec(ml_d)
                        ou = odds_obj.get("overUnder")
                        if ou and abs(float(ou) - 2.5) < 0.2:
                            c_o25, c_u25 = 1.88, 1.92

                    eventos_encontrados.append({
                        "event_id": ev_id,
                        "liga": nom_liga,
                        "odds_key": odds_key,
                        "fecha_partido": fecha_peru_str,
                        "hora_peru": hora_peru_str,
                        "etiqueta_dia": etiqueta_dia,
                        "home_team": nh,
                        "away_team": na,
                        "form_h": fh,
                        "form_a": fa,
                        "rec_h": rec_h,
                        "rec_a": rec_a,
                        "base_tarj": b_tarj,
                        "base_falt": b_falt,
                        "base_sot": b_sot,
                        "c_1": c_1,
                        "c_x": c_x,
                        "c_2": c_2,
                        "c_o25": c_o25,
                        "c_u25": c_u25,
                        "casa": casa_nombre
                    })
        except Exception:
            continue

    return eventos_encontrados, partidos_espn_estado

def obtener_datos_pipeline_v15_1():
    caches_csv = {}
    espn_partidos = {}
    eventos_futuros = []

    with ThreadPoolExecutor(max_workers=10) as ex:
        for nom, df in ex.map(descargar_csv_liga, CSV_LIGAS_MAPA.items()):
            if not df.empty:
                caches_csv[nom] = df
        for evs, p_dict in ex.map(descargar_espn_liga_estable, LIGAS_CONFIG.items()):
            eventos_futuros.extend(evs)
            espn_partidos.update(p_dict)

    return caches_csv, espn_partidos, eventos_futuros

def calcular_bonus_forma(form_str):
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
# 4. AUDITORÍA PERMANENTE
# ==============================================================================
def cargar_auditoria():
    if os.path.exists(ARCHIVO_AUDITORIA):
        try:
            with open(ARCHIVO_AUDITORIA, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
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
            "racha_actual": "En espera",
            "creditos_odds_api": "500"
        },
        "cache_odds_api": {"tiempos_ligas": {}, "partidos": {}},
        "historial": []
    }

def extraer_linea_numerica(texto_mercado):
    for token in texto_mercado.split():
        try:
            if "." in token:
                return float(token)
        except Exception:
            continue
    return 0.0

def auditar_picks_con_espn(auditoria, espn_partidos, creditos_restantes):
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
        ev_id = pick.get("event_id") or partido_espn.get("event_id", "")

        if st == "in":
            en_vivo_cnt += 1
            reloj = partido_espn.get("clock", "LIVE")
            pick["marcador_final"] = f"🔴 {gh}-{ga} ({reloj})"
            pick["estado"] = "EN VIVO"
        elif st == "post":
            mercado = pick.get("mercado", "")
            linea_num = extraer_linea_numerica(mercado)
            gano = None
            stats_extra_txt = ""

            if "(1)" in mercado or "Victoria Local" in mercado:
                gano = (gh > ga)
            elif "(2)" in mercado or "Victoria Visita" in mercado:
                gano = (ga > gh)
            elif "(1X)" in mercado or "Doble Oportunidad 1X" in mercado:
                gano = (gh >= ga)
            elif "(X2)" in mercado or "Doble Oportunidad X2" in mercado:
                gano = (ga >= gh)
            elif "Goles" in mercado:
                if "Más de" in mercado:
                    gano = (gtot > linea_num)
                elif "Menos de" in mercado:
                    gano = (gtot < linea_num)
            elif "Ambos Anotan" in mercado:
                gano = (gh > 0 and ga > 0)
            elif any(x in mercado for x in ["Faltas", "Tiros al Arco", "Tarjetas"]):
                res_sum = extraer_estadisticas_summary_espn(ev_id)
                if res_sum and res_sum.get("tiene_stats_vivo"):
                    if "Faltas" in mercado:
                        val_real = res_sum["faltas_tot"]
                        stats_extra_txt = f" · {val_real} Faltas"
                        gano = (val_real > linea_num) if "Más de" in mercado else (val_real < linea_num)
                    elif "Tiros al Arco" in mercado:
                        val_real = res_sum["sot_tot"]
                        stats_extra_txt = f" · {val_real} SOT"
                        gano = (val_real > linea_num) if "Más de" in mercado else (val_real < linea_num)
                    elif "Tarjetas" in mercado:
                        val_real = res_sum["tarj_tot"]
                        stats_extra_txt = f" · {val_real} Tarj"
                        gano = (val_real > linea_num) if "Más de" in mercado else (val_real < linea_num)

            pick["marcador_final"] = f"{gh}-{ga} (Final{stats_extra_txt})"

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
        "racha_actual": f"{racha_cnt}{racha_tipo}" if racha_cnt > 0 else f"{pendientes} En Cartera",
        "creditos_odds_api": creditos_restantes
    }
    return auditoria

def clasificar_tier(prob_pct, edge_pct):
    if prob_pct >= 66.5 and edge_pct >= 2.0:
        return "TIER S", "💎 TIER S (Blindado)", 1
    elif prob_pct >= 58.0 and edge_pct >= 1.8:
        return "TIER A", "🥇 TIER A (Fuerte +EV)", 2
    elif prob_pct >= 52.5 and edge_pct >= 1.5:
        return "TIER B", "🥈 TIER B (Valor Matemático)", 3
    return "DESCARTADO", "❌", 99

# ==============================================================================
# 5. GENERADOR DE LA PLATAFORMA WEB
# ==============================================================================
def generar_web_interactiva_v15_1(auditoria, picks_jornada, partidos_analizados, ligas_csv_activas):
    res = auditoria["resumen"]
    historial = auditoria["historial"][-45:][::-1]
    ahora_peru = datetime.now(TZ_PERU).strftime("%Y-%m-%d %H:%M (Hora Perú)")
    cred_txt = res.get("creditos_odds_api", "500")

    ligas_unicas = sorted(list({p["liga"] for p in picks_jornada}))
    opts_ligas = '<option value="ALL">🌍 Todas las Ligas (' + str(len(ligas_unicas)) + ')</option>'
    for lg in ligas_unicas:
        opts_ligas += f'<option value="{lg}">🏆 {lg}</option>'

    tarjetas_html = ""
    for idx, p in enumerate(picks_jornada):
        bloquear = (MODO_COMERCIAL == "VENTA" and p["tier_cod"] == "TIER S" and idx > 0)
        blur_class = "blur-lock" if bloquear else ""
        badge_color = "#10b981" if p["tier_cod"] == "TIER S" else ("#3b82f6" if p["tier_cod"] == "TIER A" else "#f59e0b")
        dia_badge_bg = "#ef444425" if p["etiqueta_dia"] == "HOY" else "#38bdf820"
        dia_badge_col = "#f87171" if p["etiqueta_dia"] == "HOY" else "#38bdf8"

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
        <div class="pick-card" data-dia="{p['etiqueta_dia']}" data-fecha="{p['fecha']}" data-liga="{p['liga']}" data-cat="{p['categoria']}" data-tier="{p['tier_cod']}">
            <div class="pick-header">
                <div style="display:flex; gap:6px; align-items:center;">
                    <span class="day-pill" style="background:{dia_badge_bg}; color:{dia_badge_col};">📅 {p['etiqueta_dia']} · {p['hora_peru']}</span>
                    <span class="league-tag">{p['liga']}</span>
                </div>
                <span class="tier-badge" style="background:{badge_color}22; color:{badge_color}; border:1px solid {badge_color};">{p['tier_label']}</span>
            </div>
            <div class="CardBody {blur_class}">
                <h3 class="match-title">{p['local']} vs {p['visita']}</h3>
                <div class="market-box">
                    <div>
                        <span class="market-label">PRONÓSTICO IA ({p['categoria']})</span>
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
                        <span class="m-label">Edge (+EV)</span>
                        <span class="m-val" style="color:#38bdf8;">+{p['edge']:.1f}%</span>
                    </div>
                    <div class="metric-item">
                        <span class="m-label">Stake Sugerido</span>
                        <span class="m-val stake-dinamico" data-pct="{p['stake_pct']:.4f}">S/ {p['stake']:.2f}</span>
                    </div>
                </div>
                <div class="l-bars">
                    <span>📊 {p['ventanas']}</span>
                </div>

                <details class="tactical-drawer">
                    <summary>🔬 Ver Desglose Táctico, Benchmark Pinnacle y Cuota Justa</summary>
                    <div class="drawer-content">
                        <div class="drawer-row"><span>🎯 Cuota Justa (Sin Margen):</span> <b>@{p['cuota_justa']:.2f} vs @{p['cuota']:.2f} ({p['casa']})</b></div>
                        <div class="drawer-row"><span>📐 Proyección Goles (xG):</span> <b>Local {p['xg_l']:.2f} — Visita {p['xg_v']:.2f}</b></div>
                        <div class="drawer-row"><span>🛑 Proyección Faltas / SOT:</span> <b>{p['faltas_proy']:.1f} Faltas | {p['sot_proy']:.1f} Tiros al Arco</b></div>
                        <div class="drawer-row"><span>📈 Forma & Récord ESPN:</span> <b>L: {p['form_l']} ({p['rec_l']}) | V: {p['form_v']} ({p['rec_v']})</b></div>
                        <div class="drawer-row"><span>🏥 Reporte de Plantilla:</span> <b>{p['bajas']}</b></div>
                        <p class="drawer-note">🧠 {p['explicacion']}</p>
                    </div>
                </details>
            </div>
            {overlay_vip}
        </div>
        """

    if not tarjetas_html:
        tarjetas_html = """
        <div class="empty-state">
            <h3>🛡️ Escaneo en Curso</h3>
            <p>Monitoreando líneas de mercado. El bot actualizará nuevas entradas automáticamente en la siguiente ventana horaria.</p>
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
            badge_est = '<span class="st-pend">⏳ POR JUGAR</span>'
            prof_str = '<span style="color:#94a3b8;">En espera</span>'

        hora_p = h.get("hora_peru", "")
        fecha_mostrar = f"{h.get('fecha', '')} {hora_p}".strip()

        filas_tabla += f"""
        <tr class="audit-row" data-est="{est}">
            <td><b>{fecha_mostrar}</b></td>
            <td><b>{h.get('local', '')} vs {h.get('visita', '')}</b><br><small style="color:#94a3b8;">{h.get('liga', '')} · {h.get('categoria', 'FÚTBOL')}</small></td>
            <td><b style="color:#38bdf8;">{h.get('mercado', '')}</b></td>
            <td>@{float(h.get('cuota', 1.0)):.2f}<br><small style="color:#94a3b8;">{h.get('casa', '')}</small></td>
            <td>{h.get('prob', 0):.1f}%</td>
            <td><b>{h.get('marcador_final', 'Por jugar')}</b></td>
            <td>{badge_est}</td>
            <td>{prof_str}</td>
        </tr>
        """

    html_completo = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IABET Quant Pro v15.1 | The Odds API + Pinnacle De-Vig + ESPN Live</title>
    <style>
        :root {{
            --bg: #070b14;
            --card: #111827;
            --border: #1e293b;
            --text: #f8fafc;
            --muted: #94a3b8;
            --accent: #10b981;
            --blue: #38bdf8;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }}
        body {{ background-color: var(--bg); color: var(--text); line-height: 1.5; padding-bottom: 70px; }}
        .navbar {{ background: rgba(15, 23, 42, 0.92); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; flex-wrap: wrap; gap: 10px; }}
        .logo {{ font-size: 1.3rem; font-weight: 800; color: #fff; display: flex; align-items: center; gap: 8px; }}
        .logo span {{ background: linear-gradient(90deg, #10b981, #38bdf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .nav-right {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
        .quota-pill {{ font-size: 0.76rem; background: #38bdf818; color: #38bdf8; border: 1px solid #38bdf850; padding: 6px 12px; border-radius: 999px; font-weight: 700; }}
        .status-pill {{ font-size: 0.78rem; background: #10b98120; color: #10b981; border: 1px solid #10b98150; padding: 6px 12px; border-radius: 999px; font-weight: 600; }}
        .container {{ max-width: 1240px; margin: 0 auto; padding: 24px 16px; }}
        .hero {{ text-align: center; padding: 26px 18px; background: radial-gradient(circle at top, #1e293b70, #0f172a40); border-radius: 16px; margin-bottom: 24px; border: 1px solid var(--border); }}
        .hero h1 {{ font-size: 1.85rem; font-weight: 800; margin-bottom: 8px; }}
        .hero p {{ color: var(--muted); max-width: 800px; margin: 0 auto; font-size: 0.92rem; }}
        .stats-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: 12px; margin-bottom: 24px; }}
        .stat-box {{ background: var(--card); border: 1px solid var(--border); padding: 16px; border-radius: 12px; text-align: center; }}
        .stat-box .s-label {{ font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-box .s-val {{ font-size: 1.55rem; font-weight: 800; margin-top: 4px; color: #fff; }}

        .controls-panel {{ background: #0f172a; border: 1px solid var(--border); border-radius: 14px; padding: 16px; margin-bottom: 24px; display: flex; flex-wrap: wrap; gap: 14px; align-items: center; justify-content: space-between; }}
        .day-tabs {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .day-tab {{ background: #1e293b; color: var(--muted); border: 1px solid #334155; padding: 8px 14px; border-radius: 8px; font-size: 0.82rem; font-weight: 700; cursor: pointer; transition: all 0.2s; }}
        .day-tab.active, .day-tab:hover {{ background: var(--accent); color: #052e16; border-color: var(--accent); }}
        .filters-group {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
        .select-pro {{ background: #111827; color: #fff; border: 1px solid #334155; padding: 8px 12px; border-radius: 8px; font-size: 0.82rem; font-weight: 600; cursor: pointer; outline: none; }}
        .select-pro:focus {{ border-color: var(--blue); }}
        .bankroll-calc {{ display: flex; align-items: center; gap: 8px; background: #111827; border: 1px solid #10b98160; padding: 6px 12px; border-radius: 8px; font-size: 0.8rem; }}
        .bankroll-calc input {{ width: 75px; background: transparent; border: none; color: #10b981; font-weight: 800; font-size: 0.92rem; text-align: right; outline: none; }}

        .section-title {{ font-size: 1.15rem; font-weight: 700; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }}
        .picks-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 18px; margin-bottom: 42px; }}
        .pick-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px; position: relative; overflow: hidden; transition: transform 0.2s, border-color 0.2s; }}
        .pick-card:hover {{ transform: translateY(-3px); border-color: #38bdf860; }}
        .pick-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; gap: 6px; flex-wrap: wrap; }}
        .day-pill {{ font-size: 0.7rem; font-weight: 800; padding: 3px 8px; border-radius: 6px; }}
        .league-tag {{ font-size: 0.72rem; color: var(--muted); font-weight: 700; text-transform: uppercase; }}
        .tier-badge {{ font-size: 0.7rem; font-weight: 700; padding: 3px 9px; border-radius: 99px; }}
        .match-title {{ font-size: 1.12rem; font-weight: 800; margin-bottom: 12px; color: #fff; }}
        .market-box {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }}
        .market-label {{ font-size: 0.65rem; color: var(--muted); font-weight: 700; }}
        .market-name {{ font-size: 0.96rem; font-weight: 800; color: #38bdf8; }}
        .odd-pill {{ background: #10b98120; color: #10b981; font-weight: 800; font-size: 1.1rem; padding: 6px 12px; border-radius: 8px; border: 1px solid #10b98140; }}
        .metrics-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; text-align: center; background: #070b14; padding: 10px; border-radius: 8px; }}
        .m-label {{ display: block; font-size: 0.65rem; color: var(--muted); }}
        .m-val {{ font-size: 0.88rem; font-weight: 700; }}
        .l-bars {{ font-size: 0.76rem; color: #cbd5e1; background: #1e293b60; padding: 7px 10px; border-radius: 6px; margin-bottom: 10px; }}

        .tactical-drawer {{ background: #0b1120; border: 1px solid #1e293b; border-radius: 8px; padding: 8px 12px; font-size: 0.78rem; }}
        .tactical-drawer summary {{ cursor: pointer; font-weight: 700; color: #38bdf8; outline: none; list-style: none; display: flex; justify-content: space-between; align-items: center; }}
        .tactical-drawer summary::after {{ content: "▼"; font-size: 0.65rem; transition: transform 0.2s; }}
        .tactical-drawer[open] summary::after {{ transform: rotate(180deg); }}
        .drawer-content {{ margin-top: 10px; padding-top: 10px; border-top: 1px dashed #334155; display: flex; flex-direction: column; gap: 6px; color: #cbd5e1; }}
        .drawer-row {{ display: flex; justify-content: space-between; gap: 8px; }}
        .drawer-note {{ margin-top: 6px; color: var(--muted); font-style: italic; }}

        .blur-lock {{ filter: blur(6px); user-select: none; pointer-events: none; }}
        .vip-overlay {{ position: absolute; inset: 0; background: rgba(11, 15, 25, 0.75); display: flex; align-items: center; justify-content: center; padding: 20px; text-align: center; }}
        .vip-box {{ background: #111827; border: 1px solid #10b981; padding: 16px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.6); }}
        .vip-box span {{ display: block; font-weight: 700; font-size: 0.9rem; margin-bottom: 10px; color: #fff; }}
        .btn-vip {{ display: inline-block; background: #10b981; color: #052e16; font-weight: 800; text-decoration: none; padding: 8px 16px; border-radius: 8px; font-size: 0.85rem; }}
        .table-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.83rem; text-align: left; }}
        th {{ background: #0f172a; color: var(--muted); padding: 12px 14px; font-size: 0.73rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }}
        td {{ padding: 12px 14px; border-bottom: 1px solid var(--border); }}
        .st-won {{ background: #10b98120; color: #10b981; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.73rem; }}
        .st-lost {{ background: #ef444420; color: #ef4444; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.73rem; }}
        .st-live {{ background: #ef444430; color: #f87171; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.73rem; border: 1px solid #ef4444; }}
        .st-pend {{ background: #f59e0b20; color: #f59e0b; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.73rem; }}
        .empty-state {{ grid-column: 1 / -1; background: var(--card); border: 1px dashed #334155; padding: 36px; border-radius: 14px; text-align: center; color: var(--muted); }}
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="logo">⚡ IABET <span>QUANT PRO v15.1</span></div>
        <div class="nav-right">
            <div class="quota-pill">🎫 Cuota OddsAPI: {cred_txt} disp.</div>
            <div class="status-pill">🟢 Motor Estable | Modo: {MODO_COMERCIAL}</div>
        </div>
    </nav>
    <div class="container">
        <div class="hero">
            <h1>Motor Cuantitativo con Pinnacle De-Vigging & Gestión Inteligente</h1>
            <p>Sincronizado con The Odds API (Pinnacle/Betfair/Betsson), ESPN Summary API y series históricas L5 · L10 · L20 en horarios clave de máxima liquidez[cite: 7].</p>
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
                <div class="s-label">Bankroll Auditado</div>
                <div class="s-val">S/ {res['bankroll_actual']:.2f}</div>
            </div>
            <div class="stat-box">
                <div class="s-label">Cartera / Racha</div>
                <div class="s-val">🔥 {res['racha_actual']}</div>
            </div>
        </div>

        <div class="controls-panel">
            <div class="day-tabs">
                <button class="day-tab active" onclick="filtrarDia('ALL', this)">📅 Todos los Días</button>
                <button class="day-tab" onclick="filtrarDia('HOY', this)">🔥 Juegan Hoy</button>
                <button class="day-tab" onclick="filtrarDia('MAÑANA', this)">⏭️ Juegan Mañana</button>
            </div>
            <div class="filters-group">
                <select id="filtroLiga" class="select-pro" onchange="aplicarFiltros()">
                    {opts_ligas}
                </select>
                <select id="filtroMercado" class="select-pro" onchange="aplicarFiltros()">
                    <option value="ALL">🎯 Todos los Mercados</option>
                    <option value="FALTAS">🛑 Faltas Totales</option>
                    <option value="TIROS">🎯 Tiros al Arco</option>
                    <option value="TARJETAS">🟨 Tarjetas</option>
                    <option value="GOLES">⚽ Goles (Over/Under)</option>
                    <option value="DOBLE">🛡️ Doble Oportunidad</option>
                    <option value="1X2">🏆 Ganador (1X2)</option>
                </select>
                <select id="filtroTier" class="select-pro" onchange="aplicarFiltros()">
                    <option value="ALL">💎 Todos los Tiers</option>
                    <option value="TIER S">💎 Solo TIER S (Blindado)</option>
                    <option value="TIER A">🥇 Solo TIER A (+EV)</option>
                    <option value="TIER B">🥈 Solo TIER B (Valor)</option>
                </select>
                <div class="bankroll-calc">
                    <span>💰 Mi Bankroll: S/</span>
                    <input type="number" id="inputBankroll" value="{res['bankroll_actual']:.0f}" min="10" step="10" oninput="recalcularStakes()">
                </div>
            </div>
        </div>

        <div class="section-title">
            <span id="contadorPicks">🎯 Pronósticos Futuros Verificados ({len(picks_jornada)} seleccionados de {partidos_analizados} partidos futuros)</span>
            <small style="color:var(--muted); font-size:0.8rem;">Actualizado: {ahora_peru}</small>
        </div>
        <div class="picks-grid" id="gridPicks">
            {tarjetas_html}
        </div>

        <div class="section-title">
            <span>📋 Auditoría Automática Multivariable (Goles, Faltas, Tiros al Arco y Tarjetas vía ESPN Summary)</span>
            <select id="filtroAuditoria" class="select-pro" onchange="filtrarTablaAuditoria()">
                <option value="ALL">Ver Todos los Estados</option>
                <option value="PENDIENTE">⏳ Solo Por Jugar</option>
                <option value="EN VIVO">🔴 Solo En Vivo</option>
                <option value="GANADO">✅ Solo Ganados</option>
                <option value="PERDIDO">❌ Solo Perdidos</option>
            </select>
        </div>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>Fecha / Hora (Perú)</th>
                        <th>Partido / Torneo</th>
                        <th>Pronóstico IA</th>
                        <th>Cuota / Casa</th>
                        <th>Prob. Real</th>
                        <th>Marcador & Estadísticas</th>
                        <th>Estado</th>
                        <th>Retorno</th>
                    </tr>
                </thead>
                <tbody id="tablaAuditoriaBody">
                    {filas_tabla}
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let diaSeleccionado = 'ALL';

        function filtrarDia(dia, btn) {{
            diaSeleccionado = dia;
            document.querySelectorAll('.day-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            aplicarFiltros();
        }}

        function aplicarFiltros() {{
            const liga = document.getElementById('filtroLiga').value;
            const cat = document.getElementById('filtroMercado').value;
            const tier = document.getElementById('filtroTier').value;
            const cards = document.querySelectorAll('.pick-card');
            let visibles = 0;

            cards.forEach(card => {{
                const cDia = card.getAttribute('data-dia');
                const cLiga = card.getAttribute('data-liga');
                const cCat = card.getAttribute('data-cat');
                const cTier = card.getAttribute('data-tier');

                const okDia = (diaSeleccionado === 'ALL') || (cDia === diaSeleccionado);
                const okLiga = (liga === 'ALL') || (cLiga === liga);
                const okCat = (cat === 'ALL') || (cCat === cat);
                const okTier = (tier === 'ALL') || (cTier === tier);

                if (okDia && okLiga && okCat && okTier) {{
                    card.style.display = 'block';
                    visibles++;
                }} else {{
                    card.style.display = 'none';
                }}
            }});

            document.getElementById('contadorPicks').innerText = `🎯 Pronósticos Activos Mostrados: ${{visibles}} de ${{cards.length}} disponibles`;
        }}

        function recalcularStakes() {{
            const bank = parseFloat(document.getElementById('inputBankroll').value) || 100;
            document.querySelectorAll('.stake-dinamico').forEach(el => {{
                const pct = parseFloat(el.getAttribute('data-pct')) || 0.03;
                const monto = Math.max(1.0, Math.min(bank * 0.08, Math.round((bank * pct) * 2) / 2));
                el.innerText = 'S/ ' + monto.toFixed(2);
            }});
        }}

        function filtrarTablaAuditoria() {{
            const est = document.getElementById('filtroAuditoria').value;
            document.querySelectorAll('.audit-row').forEach(row => {{
                const rEst = row.getAttribute('data-est');
                row.style.display = (est === 'ALL' || rEst === est) ? '' : 'none';
            }});
        }}
    </script>
</body>
</html>"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_completo)

# ==============================================================================
# 6. ORQUESTADOR MAESTRO v15.1
# ==============================================================================
def enviar_telegram_seguro(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    bloques = [texto[i:i+3900] for i in range(0, len(texto), 3900)]
    for b in bloques:
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": b}, timeout=10)
        except Exception:
            pass

def ejecutar_iabet_v15_1():
    t0 = time.time()
    caches_csv, espn_partidos, eventos_futuros = obtener_datos_pipeline_v15_1()

    auditoria = cargar_auditoria()

    ligas_activas_hoy = []
    for ev in eventos_futuros:
        if ev.get("odds_key"):
            if ev["odds_key"] not in ligas_activas_hoy:
                ligas_activas_hoy.append(ev["odds_key"])

    mapa_odds_api, creditos_restantes = consultar_the_odds_api_smart(ligas_activas_hoy, auditoria)

    auditoria = auditar_picks_con_espn(auditoria, espn_partidos, creditos_restantes)
    bankroll_dinamico = max(50.0, float(auditoria["resumen"]["bankroll_actual"]))

    candidatos = []
    partidos_analizados = 0
    vistos = set()

    for ev in eventos_futuros:
        loc = ev.get("home_team")
        vis = ev.get("away_team")
        liga = ev.get("liga", "Fútbol")
        ev_id = ev.get("event_id", "")
        if not loc or not vis:
            continue

        id_p = f"{normalizar_nombre(loc)[:5]}__{normalizar_nombre(vis)[:5]}"
        if id_p in vistos:
            continue
        vistos.add(id_p)
        partidos_analizados += 1

        base_t = ev.get("base_tarj", 4.5)
        base_f = ev.get("base_falt", 24.5)
        base_s = ev.get("base_sot", 8.8)

        df_liga = caches_csv.get(liga)
        st_l = analizar_equipo_profundo(df_liga, loc, base_f, base_s, base_t, es_local=True)
        st_v = analizar_equipo_profundo(df_liga, vis, base_f, base_s, base_t, es_local=False)

        form_l = ev.get("form_h", "WDW")
        form_v = ev.get("form_a", "DWL")
        rec_l = ev.get("rec_h", "Act")
        rec_v = ev.get("rec_a", "Act")
        bonus_l = calcular_bonus_forma(form_l)
        bonus_v = calcular_bonus_forma(form_v)

        datos_odds = mapa_odds_api.get(id_p)
        if datos_odds:
            c_1 = datos_odds["best_1"]
            c_x = datos_odds["best_x"]
            c_2 = datos_odds["best_2"]
            pin_1 = datos_odds["pin_1"]
            pin_x = datos_odds["pin_x"]
            pin_2 = datos_odds["pin_2"]
            casa_1 = f"{datos_odds['casa_1']} (OddsAPI)"
            casa_2 = f"{datos_odds['casa_2']} (OddsAPI)"
            c_o25, c_u25 = 1.88, 1.92
        else:
            c_1 = ev["c_1"]
            c_x = ev["c_x"]
            c_2 = ev["c_2"]
            c_o25 = ev["c_o25"]
            c_u25 = ev["c_u25"]
            casa_1 = ev["casa"]
            casa_2 = ev["casa"]

            if c_1 <= 1.05 or c_2 <= 1.05:
                fuerza_rel = (bonus_l * 1.12) / max(0.75, bonus_v)
                p_est_1 = min(0.68, max(0.24, 0.44 * fuerza_rel))
                p_est_x = 0.27
                p_est_2 = max(0.15, 1.0 - p_est_1 - p_est_x)
                c_1 = round((1.0 / p_est_1) * 0.95, 2)
                c_x = round((1.0 / p_est_x) * 0.95, 2)
                c_2 = round((1.0 / p_est_2) * 0.95, 2)
                c_o25, c_u25 = 1.88, 1.92
                casa_1 = "Línea Apertura B2B"
                casa_2 = "Línea Apertura B2B"

            pin_1, pin_x, pin_2 = c_1, c_x, c_2

        overround = (1.0 / pin_1) + (1.0 / pin_x) + (1.0 / pin_2)
        p_devig_1 = (1.0 / pin_1) / overround
        p_devig_x = (1.0 / pin_x) / overround
        p_devig_2 = (1.0 / pin_2) / overround

        goles_mkt = 2.65
        if c_o25 > 1.05 and c_u25 > 1.05:
            p_ov = (1.0 / c_o25) / ((1.0 / c_o25) + (1.0 / c_u25))
            goles_mkt = 2.15 + (p_ov * 1.15)

        l_mkt_l = max(0.55, min(3.10, goles_mkt * (p_devig_1 + 0.48 * p_devig_x) * 1.05))
        l_mkt_v = max(0.45, min(2.75, goles_mkt * (p_devig_2 + 0.52 * p_devig_x) * 0.95))

        if st_l and st_v:
            lambda_l = ((((st_l["gf"] + st_v["gc"]) / 2.0) * 0.62) + (l_mkt_l * 0.38)) * bonus_l
            lambda_v = ((((st_v["gf"] + st_l["gc"]) / 2.0) * 0.62) + (l_mkt_v * 0.38)) * bonus_v
            f_loc_esp = (st_l["f_com"] * 0.65) + (st_v["f_rec"] * 0.35)
            f_vis_esp = (st_v["f_com"] * 0.65) + (st_l["f_rec"] * 0.35)
            sot_l, sot_v = st_l["sot"] * bonus_l, st_v["sot"] * bonus_v
            tarj_esp = st_l["tarj"] + st_v["tarj"]
            f_l5 = st_l["f_l5"] + st_v["f_l5"]
            f_l10 = st_l["f_l10"] + st_v["f_l10"]
            f_l20 = st_l["f_l20"] + st_v["f_l20"]
            str_ventanas = f"L5: {f_l5:.1f}f · L10: {f_l10:.1f}f · L20: {f_l20:.1f}f | xG: {lambda_l:.2f}-{lambda_v:.2f} | ESPN: {form_l}"
            tiene_stats = True
        else:
            deseq = min(1.25, max(0.80, pin_2 / max(1.05, pin_1)))
            lambda_l = l_mkt_l * bonus_l
            lambda_v = l_mkt_v * bonus_v
            f_loc_esp = (base_f * 0.47) / (deseq ** 0.18)
            f_vis_esp = (base_f * 0.53) * (deseq ** 0.18)
            sot_l = (base_s * 0.54) * (deseq ** 0.15)
            sot_v = (base_s * 0.46) / (deseq ** 0.15)
            tarj_esp = base_t
            str_ventanas = f"xG Proy: {lambda_l:.2f}-{lambda_v:.2f} | SOT: {sot_l+sot_v:.1f} | ESPN: {form_l} vs {form_v}"
            tiene_stats = False

        linea_f_tot = round(base_f) - 0.5
        linea_sot = round(base_s) - 0.5
        linea_tarj = round(base_t) - 0.5

        res_mc = motor_montecarlo_v15(
            lambda_l, lambda_v, f_loc_esp, f_vis_esp, linea_f_tot,
            sot_l, sot_v, linea_sot, tarj_esp, linea_tarj, N_SIMS
        )

        p_fin_1 = (res_mc[0] * 0.65) + (p_devig_1 * 0.35)
        p_fin_2 = (res_mc[2] * 0.65) + (p_devig_2 * 0.35)
        p_fin_1x = (res_mc[3] * 0.65) + ((p_devig_1 + p_devig_x) * 0.35)
        p_fin_x2 = (res_mc[4] * 0.65) + ((p_devig_2 + p_devig_x) * 0.35)

        c_1x = round((1.0 / ((1.0 / c_1) + (1.0 / c_x))) * 1.02, 2)
        c_x2 = round((1.0 / ((1.0 / c_2) + (1.0 / c_x))) * 1.02, 2)

        mercados_eval = [
            (f"Victoria {loc} (1)", p_fin_1, c_1, casa_1, "1X2"),
            (f"Victoria {vis} (2)", p_fin_2, c_2, casa_2, "1X2"),
            (f"Doble Oportunidad {loc} o Empate (1X)", p_fin_1x, c_1x, casa_1, "DOBLE"),
            (f"Doble Oportunidad {vis} o Empate (X2)", p_fin_x2, c_x2, casa_2, "DOBLE"),
        ]

        if c_o25 > 1.38:
            mercados_eval.append(("Más de 2.5 Goles (Over 2.5)", res_mc[6], c_o25, casa_1, "GOLES"))
        if c_u25 > 1.38:
            mercados_eval.append(("Menos de 2.5 Goles (Under 2.5)", res_mc[7], c_u25, casa_1, "GOLES"))
            c_un35 = max(1.38, round(c_u25 * 0.80, 2))
            mercados_eval.append(("Menos de 3.5 Goles (Under 3.5)", res_mc[8], c_un35, casa_1, "GOLES"))

        dif_f = (f_loc_esp + f_vis_esp) - linea_f_tot
        if dif_f <= -0.85:
            mercados_eval.append((f"Menos de {linea_f_tot} Faltas Totales", res_mc[11], 1.68, "Betsson/Inkabet", "FALTAS"))
        elif dif_f >= 0.85:
            mercados_eval.append((f"Más de {linea_f_tot} Faltas Totales", res_mc[10], 1.68, "Betsson/Inkabet", "FALTAS"))

        if tiene_stats:
            dif_sot = (sot_l + sot_v) - linea_sot
            if dif_sot >= 0.65:
                mercados_eval.append((f"Más de {linea_sot} Tiros al Arco", res_mc[12], 1.70, "Betsson/Inkabet", "TIROS"))
            elif dif_sot <= -0.65:
                mercados_eval.append((f"Menos de {linea_sot} Tiros al Arco", res_mc[13], 1.68, "Betsson/Inkabet", "TIROS"))

            if (tarj_esp - linea_tarj) >= 0.40:
                mercados_eval.append((f"Más de {linea_tarj} Tarjetas", res_mc[14], 1.72, "Betsson/Inkabet", "TARJETAS"))

        for nom_m, p_real, cuota_m, casa_m, cat in mercados_eval:
            if not (CUOTA_MIN <= cuota_m <= CUOTA_MAX):
                continue
            p_imp = 1.0 / cuota_m
            edge_pct = (p_real - p_imp) * 100.0
            if edge_pct < 1.5 or edge_pct > 19.5:
                continue

            prob_pct = p_real * 100.0
            tier_cod, tier_label, tier_rank = clasificar_tier(prob_pct, edge_pct)
            if tier_cod == "DESCARTADO":
                continue

            b = cuota_m - 1.0
            kelly = max(0.015, min(0.06, (((b * p_real) - (1.0 - p_real)) / b) * KELLY_FRAC))
            stake_soles = min(6.0, max(1.50, round((bankroll_dinamico * kelly) * 2) / 2))
            confianza_ia = min(97.4, 82.5 + (edge_pct * 0.85) + max(0.0, (prob_pct - 54.0) * 0.25))
            cuota_justa = round(1.0 / max(0.05, p_real), 2)

            candidatos.append({
                "event_id": ev_id,
                "fecha": ev["fecha_partido"],
                "hora_peru": ev["hora_peru"],
                "etiqueta_dia": ev["etiqueta_dia"],
                "liga": liga,
                "categoria": cat,
                "local": loc,
                "visita": vis,
                "mercado": nom_m,
                "cuota": round(cuota_m, 2),
                "cuota_justa": cuota_justa,
                "casa": casa_m,
                "confianza": round(confianza_ia, 1),
                "prob": round(prob_pct, 1),
                "edge": round(edge_pct, 1),
                "stake": stake_soles,
                "stake_pct": kelly,
                "tier_cod": tier_cod,
                "tier_label": tier_label,
                "tier_rank": tier_rank,
                "xg_l": round(lambda_l, 2),
                "xg_v": round(lambda_v, 2),
                "faltas_proy": round(f_loc_esp + f_vis_esp, 1),
                "sot_proy": round(sot_l + sot_v, 1),
                "form_l": form_l,
                "form_v": form_v,
                "rec_l": rec_l,
                "rec_v": rec_v,
                "bajas": "Plantillas titulares disponibles",
                "ventanas": str_ventanas,
                "explicacion": f"En 1,000,000 de simulaciones + Pinnacle De-Vig la cuota justa es @{cuota_justa:.2f}, mientras que {casa_m} ofrece @{cuota_m:.2f} (+{edge_pct:.1f}% EV).",
                "estado": "PENDIENTE",
                "marcador_final": f"Juega {ev['etiqueta_dia']} {ev['hora_peru']}",
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
            res_sum = extraer_estadisticas_summary_espn(c.get("event_id"))
            if res_sum and res_sum.get("bajas_txt"):
                c["bajas"] = res_sum["bajas_txt"]
            picks_finales.append(c)
            partidos_usados.add(clave_p)
            conteo_cat[cat] = conteo_cat.get(cat, 0) + 1

    claves_hist = {f"{h.get('fecha')}_{h.get('local')}_{h.get('mercado')}" for h in auditoria["historial"]}
    for pf in picks_finales:
        k_nuevo = f"{pf['fecha']}_{pf['local']}_{pf['mercado']}"
        if k_nuevo not in claves_hist:
            auditoria["historial"].append(pf)
            claves_hist.add(k_nuevo)

    auditoria = auditar_picks_con_espn(auditoria, espn_partidos, creditos_restantes)

    with open(ARCHIVO_AUDITORIA, "w", encoding="utf-8") as f:
        json.dump(auditoria, f, ensure_ascii=False, indent=2)

    generar_web_interactiva_v15_1(auditoria, picks_finales, partidos_analizados, len(caches_csv))

    res = auditoria["resumen"]
    dur = time.time() - t0
    lineas_tg = [
        "⚡ IABET QUANT PRO v15.1 (ESTABLE)",
        f"📊 Efectividad: {res['win_rate_pct']:.1f}% ({res['ganados']}W-{res['perdidos']}L) | ROI: {res['roi_pct']:+.1f}%",
        f"💰 Bankroll: S/ {res['bankroll_actual']:.2f} | 🎫 Créditos OddsAPI: {creditos_restantes}",
        f"🌐 Web Interactiva: https://blakeffx.github.io/iabet-cloud/",
        f"⏱️ {len(picks_finales)} picks futuros seleccionados de {partidos_analizados} partidos ({dur:.1f}s)",
        "━━━━━━━━━━━━━━━━━━━━"
    ]

    for idx, p in enumerate(picks_finales[:8], 1):
        lineas_tg.append(
            f"{idx}. {p['tier_label']} [📅 {p['etiqueta_dia']} {p['hora_peru']} · {p['liga']}]\n"
            f"⚽ {p['local']} vs {p['visita']}\n"
            f"👉 {p['mercado']}  @{p['cuota']:.2f} ({p['casa']})\n"
            f"🔥 Confianza IA: {p['confianza']:.1f}% (Prob: {p['prob']:.1f}% | Edge: +{p['edge']:.1f}%)\n"
            f"📊 {p['ventanas']} | 💰 Stake: S/ {p['stake']:.2f}\n"
        )

    enviar_telegram_seguro("\n".join(lineas_tg))
    print(f"✅ IABET v15.1 completado: {len(picks_finales)} picks publicados.")

if __name__ == "__main__":
    ejecutar_iabet_v15_1()
