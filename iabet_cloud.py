import time
import io
import os
import json
import math
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import requests
import numpy as np
import pandas as pd
from numba import njit, prange

# ==============================================================================
# 1. CONFIGURACIÓN ENTERPRISE SaaS (IABET QUANT PRO v18.0 - LIVE STATS CENTER)
# ==============================================================================
THE_ODDS_API_KEY = "a13d27d9c5cc3ea988f72ba4ff74c56f"
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

MODO_COMERCIAL = "PRUEBA"  # "PRUEBA" (Picks abiertos) o "VENTA" (Blur VIP en Tier S)
LINK_CONTACTO_VIP = "https://t.me/+TuEnlaceDeContacto"

BANKROLL_INICIAL = 100.0
KELLY_FRAC = 0.20          # Fifth-Kelly: máxima protección frente a la varianza
N_SIMS = 1_000_000         # 1,000,000 de simulaciones de Monte Carlo por partido
CUOTA_MIN = 1.36
CUOTA_MAX = 2.20           # Candado anti-fantasía
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

# Catálogo Maestro: Slug ESPN -> (Nombre, CSV Code, OddsAPI Key, Tarjetas, Faltas, SOT, Córners)
LIGAS_CONFIG = {
    "esp.1": ("LaLiga", "SP1", "soccer_spain_la_liga", 4.6, 24.8, 8.5, 9.4),
    "eng.1": ("Premier League", "E0", "soccer_epl", 3.8, 21.5, 9.2, 10.4),
    "ita.1": ("Serie A", "I1", "soccer_italy_serie_a", 4.2, 24.2, 8.6, 9.6),
    "ger.1": ("Bundesliga", "D1", "soccer_germany_bundesliga", 3.9, 22.0, 9.4, 9.8),
    "fra.1": ("Ligue 1", "F1", "soccer_france_ligue_one", 3.9, 23.5, 8.7, 9.3),
    "ned.1": ("Eredivisie", "N1", "soccer_netherlands_eredivisie", 3.2, 20.2, 9.5, 10.1),
    "por.1": ("Primeira Liga", "P1", "soccer_portugal_primeira_liga", 5.0, 26.2, 8.4, 9.7),
    "uefa.champions": ("Champions League", None, "soccer_uefa_champs_league", 4.0, 22.5, 9.3, 9.8),
    "uefa.europa": ("Europa League", None, "soccer_uefa_europa_league", 4.2, 23.5, 8.9, 9.5),
    "conmebol.libertadores": ("Copa Libertadores", None, "soccer_conmebol_copa_libertadores", 5.2, 27.2, 8.5, 9.2),
    "conmebol.sudamericana": ("Copa Sudamericana", None, "soccer_conmebol_copa_sudamericana", 4.9, 26.5, 8.3, 9.0),
    "per.1": ("Liga 1 Perú", None, None, 5.1, 27.5, 8.4, 9.1),
    "bra.1": ("Brasileirão", "BRA", "soccer_brazil_campeonato", 4.9, 27.0, 8.8, 10.3),
    "arg.1": ("Liga Argentina", "ARG", "soccer_argentina_primera_division", 5.1, 27.2, 8.2, 9.0),
    "mex.1": ("Liga MX", "MEX", "soccer_mexico_ligamx", 4.4, 24.2, 8.7, 9.6)
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
    "universitario": {"gf": 1.85, "gc": 0.65, "f_com": 13.5, "f_rec": 14.5, "sot": 5.4, "tarj": 2.2, "cor": 5.4},
    "alianza lima": {"gf": 1.72, "gc": 0.72, "f_com": 13.0, "f_rec": 14.8, "sot": 5.1, "tarj": 2.3, "cor": 5.2},
    "sporting cristal": {"gf": 2.05, "gc": 1.02, "f_com": 11.8, "f_rec": 14.5, "sot": 5.8, "tarj": 2.0, "cor": 5.8},
    "melgar": {"gf": 1.80, "gc": 0.85, "f_com": 13.2, "f_rec": 13.8, "sot": 5.4, "tarj": 2.4, "cor": 5.3},
    "cusco": {"gf": 1.65, "gc": 1.02, "f_com": 14.2, "f_rec": 13.1, "sot": 4.8, "tarj": 2.5, "cor": 4.7},
    "cienciano": {"gf": 1.52, "gc": 1.15, "f_com": 14.5, "f_rec": 13.0, "sot": 4.6, "tarj": 2.6, "cor": 4.6},
    "flamengo": {"gf": 1.92, "gc": 0.82, "f_com": 12.8, "f_rec": 14.4, "sot": 5.7, "tarj": 2.2, "cor": 5.9},
    "palmeiras": {"gf": 1.88, "gc": 0.78, "f_com": 13.1, "f_rec": 14.2, "sot": 5.6, "tarj": 2.3, "cor": 5.8},
    "botafogo": {"gf": 1.82, "gc": 0.80, "f_com": 12.9, "f_rec": 14.0, "sot": 5.4, "tarj": 2.2, "cor": 5.4},
    "river plate": {"gf": 1.85, "gc": 0.75, "f_com": 12.4, "f_rec": 14.8, "sot": 5.5, "tarj": 2.2, "cor": 5.7},
    "boca juniors": {"gf": 1.58, "gc": 0.88, "f_com": 13.6, "f_rec": 14.3, "sot": 4.8, "tarj": 2.6, "cor": 5.0}
}

# ==============================================================================
# 2. MOTOR MONTE CARLO JIT EN C (1,000,000 SIMULACIONES)
# ==============================================================================
@njit(parallel=True)
def motor_montecarlo_v18(
    l_loc, l_vis, f_loc, f_vis, linea_f_tot,
    sot_loc, sot_vis, linea_sot, tarj_esp, linea_tarj,
    cor_loc, cor_vis, n_sims
):
    vic_loc, empate, vic_vis = 0, 0, 0
    over_15, over_25, under_25, under_35, btts_si = 0, 0, 0, 0, 0
    f_over, f_under = 0, 0
    sot_over, sot_under = 0, 0
    tarj_over35, tarj_under45 = 0, 0
    cor_over85, cor_under105 = 0, 0
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

        friccion = 1.04 if abs(gl - gv) <= 1 else 0.96
        fl = max(4.0, np.random.normal(f_loc * friccion, 2.55))
        fv = max(4.0, np.random.normal(f_vis * friccion, 2.65))
        ftot = fl + fv
        if ftot > linea_f_tot: f_over += 1
        else: f_under += 1

        sot_tot = max(1.0, np.random.normal(sot_loc + sot_vis, 2.60))
        if sot_tot > linea_sot: sot_over += 1
        else: sot_under += 1

        ratio_f = ftot / max(19.0, (f_loc + f_vis))
        factor_tarj = 1.0 + ((ratio_f - 1.0) * 0.35)
        t_sim = max(0.0, np.random.normal(tarj_esp * factor_tarj, 1.45))
        if t_sim > linea_tarj: tarj_over35 += 1
        if t_sim < 4.5: tarj_under45 += 1

        c_tot = np.random.poisson(max(1.5, cor_loc)) + np.random.poisson(max(1.5, cor_vis))
        if c_tot > 8.5: cor_over85 += 1
        if c_tot < 10.5: cor_under105 += 1

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
        tarj_over35 / n_sims,
        tarj_under45 / n_sims,
        cor_over85 / n_sims,
        cor_under105 / n_sims
    )

def calcular_top3_marcadores_exactos(l_loc, l_vis):
    marcadores = []
    rho = -0.08
    for gl in range(5):
        for gv in range(5):
            p_l = (math.exp(-l_loc) * (l_loc ** gl)) / math.factorial(gl)
            p_v = (math.exp(-l_vis) * (l_vis ** gv)) / math.factorial(gv)
            tau = 1.0
            if gl == 0 and gv == 0:
                tau = 1.0 - (l_loc * l_vis * rho)
            elif gl == 0 and gv == 1:
                tau = 1.0 + (l_loc * rho)
            elif gl == 1 and gv == 0:
                tau = 1.0 + (l_vis * rho)
            elif gl == 1 and gv == 1:
                tau = 1.0 - rho
            prob = max(0.0, p_l * p_v * tau) * 100.0
            marcadores.append((f"{gl}-{gv}", prob))
    marcadores.sort(key=lambda x: x[1], reverse=True)
    return [f"{m[0]} ({m[1]:.1f}%)" for m in marcadores[:3]]

# ==============================================================================
# 3. INGESTA INTELIGENTE MULTI-CASA + ESTADÍSTICAS PROFUNDAS ESPN SUMMARY
# ==============================================================================
def normalizar_nombre(nombre):
    if not nombre:
        return ""
    limpio = str(nombre).lower().replace("-", " ")
    for p in ["fc ", "cf ", "cd ", "ca ", "sc ", "ac ", "as ", "ud ", "rcd ", "real ", "sporting ", "club ", "deportivo ", "atletico ", "united", "city", "calcio ", "1913", "1909"]:
        limpio = limpio.replace(p, "")
    return limpio.strip()

def consultar_the_odds_api_smart(ligas_con_partidos_hoy, auditoria):
    cache_global = auditoria.get("cache_odds_api", {})
    creditos_restantes = auditoria.get("resumen", {}).get("creditos_odds_api", "490")
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
            "markets": "h2h,totals",
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
                    best_o25, best_u25 = (0.0, ""), (0.0, "")
                    all_o25, all_u25 = [], []

                    for bm in ev.get("bookmakers", []):
                        b_key = bm.get("key", "")
                        b_title = bm.get("title", "Bookie")
                        for m in bm.get("markets", []):
                            mk = m.get("key")
                            if mk == "h2h":
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
                            elif mk == "totals":
                                for o in m.get("outcomes", []):
                                    pt = float(o.get("point", 0) or 0)
                                    pr = float(o.get("price", 0) or 0)
                                    if abs(pt - 2.5) < 0.1 and pr > 1.05:
                                        if o.get("name") == "Over":
                                            all_o25.append(pr)
                                            if pr > best_o25[0]: best_o25 = (pr, b_title)
                                        elif o.get("name") == "Under":
                                            all_u25.append(pr)
                                            if pr > best_u25[0]: best_u25 = (pr, b_title)

                    if best_1[0] > 1.01 and best_2[0] > 1.01:
                        clave = f"{normalizar_nombre(loc)[:5]}__{normalizar_nombre(vis)[:5]}"
                        mapa_cuotas[clave] = {
                            "best_1": best_1[0], "casa_1": best_1[1],
                            "best_x": best_x[0] if best_x[0] > 1.0 else 3.25, "casa_x": best_x[1] or best_1[1],
                            "best_2": best_2[0], "casa_2": best_2[1],
                            "pin_1": pin_1 if pin_1 > 1.0 else float(np.mean(all_1)),
                            "pin_x": pin_x if pin_x > 1.0 else (float(np.mean(all_x)) if all_x else 3.25),
                            "pin_2": pin_2 if pin_2 > 1.0 else float(np.mean(all_2)),
                            "best_o25": best_o25[0], "casa_o25": best_o25[1],
                            "best_u25": best_u25[0], "casa_u25": best_u25[1],
                            "mean_o25": float(np.mean(all_o25)) if all_o25 else 0.0,
                            "mean_u25": float(np.mean(all_u25)) if all_u25 else 0.0
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
        faltas_tot, sot_tot, tarj_tot, cor_tot = 0, 0, 0, 0
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
                elif nom_st in ("wonCorners", "cornerKicks"):
                    cor_tot += int(val)
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
            "cor_tot": cor_tot,
            "bajas_txt": " | ".join(bajas) if bajas else "Plantillas titulares disponibles"
        }
    except Exception:
        return None

def descargar_espn_liga_estable(item):
    slug, (nom_liga, _, odds_key, b_tarj, b_falt, b_sot, b_cor) = item
    ahora_peru = datetime.now(TZ_PERU)
    dia_ayer = (ahora_peru - timedelta(days=1)).strftime("%Y%m%d")
    dia_hoy = ahora_peru.strftime("%Y%m%d")
    dia_manana = (ahora_peru + timedelta(days=1)).strftime("%Y%m%d")
    dia_pasado = (ahora_peru + timedelta(days=2)).strftime("%Y%m%d")

    urls_a_probar = [
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard",
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={dia_ayer}",
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

                    # Permitir que partidos en juego ('in') de hoy y futuros ('pre') estén en el radar vivo
                    if estado == "post":
                        continue

                    odds_obj = (comp.get("odds") or [{}])[0]
                    c_1, c_x, c_2 = 0.0, 0.0, 0.0
                    c_o25, c_u25 = 0.0, 0.0
                    casa_nombre = "Consenso Internacional"

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
                        "estado_espn": estado,
                        "reloj_espn": detalle_tiempo,
                        "goles_h_vivo": sh,
                        "goles_a_vivo": sa,
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
                        "base_cor": b_cor,
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

def obtener_datos_pipeline_v18():
    caches_csv = {}
    espn_partidos = {}
    eventos_activos = []

    with ThreadPoolExecutor(max_workers=10) as ex:
        for nom, df in ex.map(descargar_csv_liga, CSV_LIGAS_MAPA.items()):
            if not df.empty:
                caches_csv[nom] = df
        for evs, p_dict in ex.map(descargar_espn_liga_estable, LIGAS_CONFIG.items()):
            eventos_activos.extend(evs)
            espn_partidos.update(p_dict)

    return caches_csv, espn_partidos, eventos_activos

def calcular_bonus_forma(form_str):
    if not form_str:
        return 1.0
    pesos = 0.0
    for ch in str(form_str)[-5:]:
        if ch.upper() == "W": pesos += 0.018
        elif ch.upper() == "L": pesos -= 0.018
    return max(0.91, min(1.09, 1.0 + pesos))

def analizar_equipo_profundo_v18(df, equipo, base_faltas, base_sot, base_tarj, base_cor, es_local=True):
    eq_low = normalizar_nombre(equipo)
    for k, v in DB_EQUIPOS_PRO.items():
        if k in eq_low:
            f_esp = v["f_com"] * (0.97 if es_local else 1.04)
            return {
                "gf": v["gf"] * (1.06 if es_local else 0.94), "gc": v["gc"],
                "f_com": f_esp, "f_rec": v["f_rec"],
                "sot": v["sot"], "tarj": v["tarj"], "cor": v["cor"],
                "f_l5": f_esp, "f_l10": f_esp * 0.98, "f_l20": f_esp * 0.96,
                "arr_f": np.full(10, f_esp),
                "arr_sot": np.full(10, v["sot"]),
                "arr_tarj": np.full(10, v["tarj"]),
                "arr_cor": np.full(10, v["cor"]),
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
    if len(partidos) < 6:
        return None

    gf, gc, f_com, f_rec, sot, tarj, cor = [], [], [], [], [], [], []
    ok_f = {'HF', 'AF'}.issubset(partidos.columns)
    ok_s = {'HST', 'AST'}.issubset(partidos.columns)
    ok_t = {'HY', 'AY'}.issubset(partidos.columns)
    ok_c = {'HC', 'AC'}.issubset(partidos.columns)

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
        if ok_c and pd.notna(row['HC']) and pd.notna(row['AC']):
            cor.append(float(row['HC'] if es_l else row['AC']))

    gf_pond = ((np.mean(gf[-5:]) * 0.45) + (np.mean(gf[-10:]) * 0.35) + (np.mean(gf) * 0.20)) * (1.06 if es_local else 0.94)
    gc_pond = (np.mean(gc[-5:]) * 0.45) + (np.mean(gc[-10:]) * 0.35) + (np.mean(gc) * 0.20)

    tiene_f = len(f_com) >= 6
    f_l5 = np.mean(f_com[-5:]) if tiene_f else base_faltas / 2.0
    f_l10 = np.mean(f_com[-10:]) if tiene_f else base_faltas / 2.0
    f_l20 = np.mean(f_com) if tiene_f else base_faltas / 2.0
    f_pond = (f_l5 * 0.45) + (f_l10 * 0.35) + (f_l20 * 0.20)
    f_rec_pond = np.mean(f_rec[-10:]) if tiene_f else base_faltas / 2.0

    tiene_s = len(sot) >= 6
    sot_pond = ((np.mean(sot[-5:]) * 0.45) + (np.mean(sot[-10:]) * 0.35) + (np.mean(sot) * 0.20)) if tiene_s else base_sot / 2.0

    tiene_t = len(tarj) >= 6
    tarj_pond = ((np.mean(tarj[-5:]) * 0.30) + (np.mean(tarj[-10:]) * 0.30) + (np.mean(tarj) * 0.40)) if tiene_t else base_tarj / 2.0

    tiene_c = len(cor) >= 6
    cor_pond = ((np.mean(cor[-5:]) * 0.45) + (np.mean(cor[-10:]) * 0.35) + (np.mean(cor) * 0.20)) if tiene_c else base_cor / 2.0

    return {
        "gf": gf_pond, "gc": gc_pond,
        "f_com": f_pond, "f_rec": f_rec_pond,
        "sot": sot_pond, "tarj": tarj_pond, "cor": cor_pond,
        "f_l5": f_l5, "f_l10": f_l10, "f_l20": f_l20,
        "arr_f": np.array(f_com[-10:]) if tiene_f else np.full(10, base_faltas / 2.0),
        "arr_sot": np.array(sot[-10:]) if tiene_s else np.full(10, base_sot / 2.0),
        "arr_tarj": np.array(tarj[-10:]) if tiene_t else np.full(10, base_tarj / 2.0),
        "arr_cor": np.array(cor[-10:]) if tiene_c else np.full(10, base_cor / 2.0),
        "verificado": True
    }

def calcular_hit_rate_combinado(arr_l, arr_v, linea, direccion="OVER"):
    n = min(len(arr_l), len(arr_v))
    if n < 5:
        return 65.0
    suma = arr_l[-n:] + arr_v[-n:]
    aciertos = np.sum(suma > linea) if direccion == "OVER" else np.sum(suma < linea)
    return float((aciertos / n) * 100.0)

# ==============================================================================
# 4. AUDITORÍA AUTOMÁTICA MULTIVARIABLE
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
            "racha_actual": "En espera",
            "creditos_odds_api": "490"
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
        if ev_id:
            pick["event_id"] = ev_id

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
            elif any(x in mercado for x in ["Faltas", "Tiros al Arco", "Tarjetas", "Córners"]):
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
                    elif "Córners" in mercado:
                        val_real = res_sum["cor_tot"]
                        stats_extra_txt = f" · {val_real} Córn"
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

def clasificar_tier_realista(prob_pct, edge_pct, cuota_m, hit_rate_l10):
    if prob_pct >= 66.5 and edge_pct >= 2.0 and cuota_m <= 1.76 and hit_rate_l10 >= 65.0:
        return "TIER S", "💎 TIER S (Blindado)", 1
    elif prob_pct >= 58.0 and edge_pct >= 2.0 and cuota_m <= 1.98:
        return "TIER A", "🥇 TIER A (Fuerte +EV)", 2
    elif prob_pct >= 53.0 and edge_pct >= 1.8:
        return "TIER B", "🥈 TIER B (Valor Moderado)", 3
    return "DESCARTADO", "❌", 99

# ==============================================================================
# 5. GENERADOR WEB SPA v18.0 CON TELEMETRÍA Y ESTADÍSTICAS EN TIEMPO REAL
# ==============================================================================
def generar_web_spa_v18(auditoria, picks_jornada, catalogo_partidos, ligas_csv_activas):
    res = auditoria["resumen"]
    historial = auditoria["historial"][-45:][::-1]
    ahora_peru = datetime.now(TZ_PERU).strftime("%Y-%m-%d %H:%M (Hora Perú)")
    cred_txt = res.get("creditos_odds_api", "490")

    todas_las_ligas = sorted(list({m["liga"] for m in catalogo_partidos}))
    opts_ligas = '<option value="ALL">🌍 Todas las Ligas (' + str(len(todas_las_ligas)) + ')</option>'
    for lg in todas_las_ligas:
        opts_ligas += f'<option value="{lg}">🏆 {lg}</option>'

    tarjetas_picks_html = ""
    for idx, p in enumerate(picks_jornada):
        bloquear = (MODO_COMERCIAL == "VENTA" and p["tier_cod"] == "TIER S" and idx > 0)
        blur_class = "blur-lock" if bloquear else ""
        badge_color = "#10b981" if p["tier_cod"] == "TIER S" else ("#3b82f6" if p["tier_cod"] == "TIER A" else "#f59e0b")
        dia_badge_bg = "#ef444425" if p["etiqueta_dia"] == "HOY" else "#38bdf820"
        dia_badge_col = "#f87171" if p["etiqueta_dia"] == "HOY" else "#38bdf8"
        texto_busqueda = f"{p['local']} {p['visita']} {p['liga']} {p['mercado']}".lower()
        linea_objetivo = extraer_linea_numerica(p["mercado"])

        overlay_vip = ""
        if bloquear:
            overlay_vip = f"""
            <div class="vip-overlay">
                <div class="vip-box">
                    <span>🔒 PICK EXCLUSIVO TIER S ({p['prob']:.1f}% Prob. Calibrada)</span>
                    <a href="{LINK_CONTACTO_VIP}" target="_blank" class="btn-vip">Desbloquear Acceso VIP</a>
                </div>
            </div>
            """

        tarjetas_picks_html += f"""
        <div class="pick-card" id="card-{p['event_id']}" data-event-id="{p['event_id']}" data-cat="{p['categoria']}" data-linea="{linea_objetivo}" data-mercado="{p['mercado']}" data-search="{texto_busqueda}" data-dia="{p['etiqueta_dia']}" data-liga="{p['liga']}" data-tier="{p['tier_cod']}">
            <div class="pick-header">
                <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
                    <span class="day-pill" id="pill-{p['event_id']}" style="background:{dia_badge_bg}; color:{dia_badge_col};">📅 {p['etiqueta_dia']} · {p['hora_peru']}</span>
                    <span class="league-tag">{p['liga']}</span>
                </div>
                <span class="tier-badge" style="background:{badge_color}22; color:{badge_color}; border:1px solid {badge_color};">{p['tier_label']}</span>
            </div>
            <div class="CardBody {blur_class}">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <h3 class="match-title" style="margin-bottom:0;">{p['local']} vs {p['visita']}</h3>
                    <span class="live-score-badge" id="score-{p['event_id']}">⏳ Pre-Partido</span>
                </div>

                <div class="market-box">
                    <div>
                        <span class="market-label">PRONÓSTICO IA CALIBRADO ({p['categoria']})</span>
                        <div class="market-name">{p['mercado']}</div>
                    </div>
                    <div class="odd-pill">@{p['cuota']:.2f}</div>
                </div>

                <!-- BARRA DE TELEMETRÍA Y PROGRESO DEL PICK EN TIEMPO REAL -->
                <div class="live-tracker-box" id="tracker-{p['event_id']}">
                    <div class="lt-top">
                        <span id="lt-status-{p['event_id']}">📡 Estadísticas en Tiempo Real: Esperando inicio ({p['hora_peru']})</span>
                        <b id="lt-val-{p['event_id']}" style="color:#38bdf8;">Meta: {p['mercado']}</b>
                    </div>
                    <div class="lt-bar-bg">
                        <div class="lt-bar-fill" id="lt-bar-{p['event_id']}" style="width:0%;"></div>
                    </div>
                </div>

                <!-- CENTRO DE ESTADÍSTICAS EN VIVO ESTILO CASA DE APUESTAS -->
                <details class="live-stats-drawer" id="drawer-live-{p['event_id']}" ontoggle="if(this.open) actualizarPartidoEnVivo('{p['event_id']}')">
                    <summary>📊 Estadísticas del Partido en Tiempo Real (Posesión, Tiros, Córners, Faltas y Tarjetas)</summary>
                    <div class="live-stats-body" id="live-body-{p['event_id']}">
                        <div style="text-align:center; color:#94a3b8; padding:8px;">🔄 Conectando en tiempo real con el estadio (ESPN Live Telemetry)...</div>
                    </div>
                </details>

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
                        <span class="m-label">Stake Seguro</span>
                        <span class="m-val stake-dinamico" data-pct="{p['stake_pct']:.4f}">S/ {p['stake']:.2f}</span>
                    </div>
                </div>
                <div class="l-bars">
                    <span>📊 {p['ventanas']}</span>
                </div>

                <!-- CALCULADORA UNIVERSAL MULTI-CASA -->
                <div class="betano-checker">
                    <span>🧮 ¿Qué cuota paga tu casa de apuestas?</span>
                    <input type="number" step="0.01" value="{p['cuota']:.2f}" data-prob="{p['prob']/100.0:.4f}" oninput="verificarCuotaCasa(this)">
                    <span class="betano-res" style="color:#10b981;">✅ Rentable (+{p['edge']:.1f}% EV)</span>
                </div>

                <div style="display:flex; gap:8px; margin-bottom:8px;">
                    <button class="btn-lab" onclick="abrirLaboratorioPartido('{p['match_key']}')">🔬 Abrir Radar Pre-Partido (12 Mercados + Exactos)</button>
                </div>

                <details class="tactical-drawer">
                    <summary>📐 Ver Cuota Mínima Justa, Córners, Tarjetas y Bajas</summary>
                    <div class="drawer-content">
                        <div class="drawer-row"><span>🎯 Cuota Mínima Rentable (Sin Vig):</span> <b>@{p['cuota_justa']:.2f} vs @{p['cuota']:.2f} ({p['casa']})</b></div>
                        <div class="drawer-row"><span>✅ Cumplimiento Histórico (L10/L20):</span> <b>{p['hit_rate']:.0f}% de frecuencia real</b></div>
                        <div class="drawer-row"><span>📐 Goles Esperados (xG):</span> <b>Local {p['xg_l']:.2f} — Visita {p['xg_v']:.2f}</b></div>
                        <div class="drawer-row"><span>🚩 Córners / 🟨 Tarjetas (L20):</span> <b>{p['cor_proy']:.1f} Córners | {p['tarj_l']+p['tarj_v']:.1f} Tarjetas</b></div>
                        <div class="drawer-row"><span>🛑 Faltas / 🎯 Tiros al Arco:</span> <b>{p['faltas_proy']:.1f} Faltas | {p['sot_proy']:.1f} SOT</b></div>
                        <div class="drawer-row"><span>🎲 Top 3 Marcadores Exactos:</span> <b>{p['top_scores']}</b></div>
                        <p class="drawer-note">🧠 {p['explicacion']}</p>
                    </div>
                </details>
            </div>
            {overlay_vip}
        </div>
        """

    if not tarjetas_picks_html:
        tarjetas_picks_html = """
        <div class="empty-state">
            <h3>🛡️ Filtro Anti-Varianza Activo</h3>
            <p>Usa la pestaña "📊 Buscador FutbolDataPro" arriba para consultar las probabilidades y estadísticas en vivo de todos los partidos de la jornada.</p>
        </div>
        """

    tarjetas_explorador_html = ""
    for m in catalogo_partidos:
        t_search = f"{m['local']} {m['visita']} {m['liga']}".lower()
        dia_bg = "#ef444425" if m["etiqueta_dia"] == "HOY" else "#38bdf820"
        dia_col = "#f87171" if m["etiqueta_dia"] == "HOY" else "#38bdf8"

        tarjetas_explorador_html += f"""
        <div class="explorer-card" data-event-id="{m['event_id']}" data-search="{t_search}" data-dia="{m['etiqueta_dia']}" data-liga="{m['liga']}">
            <div class="pick-header">
                <span class="day-pill" style="background:{dia_bg}; color:{dia_col};">📅 {m['etiqueta_dia']} · {m['hora_peru']}</span>
                <span class="league-tag">{m['liga']}</span>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                <h3 class="match-title" style="margin-bottom:0;">{m['local']} vs {m['visita']}</h3>
                <span class="live-score-badge" id="exp-score-{m['event_id']}">⏳ {m['hora_peru']}</span>
            </div>
            <div class="exp-prob-bar">
                <div class="p-seg p-loc" style="width:{m['p_1']:.0f}%" title="Local">{m['p_1']:.0f}% (1)</div>
                <div class="p-seg p-emp" style="width:{m['p_x']:.0f}%" title="Empate">{m['p_x']:.0f}% (X)</div>
                <div class="p-seg p-vis" style="width:{m['p_2']:.0f}%" title="Visita">{m['p_2']:.0f}% (2)</div>
            </div>
            <div class="metrics-grid" style="margin-top:10px;">
                <div class="metric-item"><span class="m-label">xG Proy.</span><span class="m-val">{m['xg_l']:.2f}-{m['xg_v']:.2f}</span></div>
                <div class="metric-item"><span class="m-label">Over 2.5</span><span class="m-val" style="color:#10b981;">{m['p_o25']:.0f}%</span></div>
                <div class="metric-item"><span class="m-label">Córners</span><span class="m-val" style="color:#38bdf8;">{m['cor_tot']:.1f}</span></div>
                <div class="metric-item"><span class="m-label">Faltas/Tarj</span><span class="m-val">{m['falt_tot']:.1f} / {m['tarj_tot']:.1f}</span></div>
            </div>
            <div class="l-bars" style="display:flex; justify-content:space-between;">
                <span>🎲 Marcador Top: <b>{m['top_scores'].split(',')[0]}</b></span>
                <span>🔥 BTTS: <b>{m['p_btts']:.0f}%</b></span>
            </div>
            <button class="btn-lab" onclick="abrirLaboratorioPartido('{m['match_key']}')">📊 Ver Estadísticas en Vivo + 12 Mercados</button>
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

    json_catalogo = json.dumps({m["match_key"]: m for m in catalogo_partidos}, ensure_ascii=False)

    html_completo = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IABET Quant Pro v18.0 | Live Match Center & Buscador FutbolDataPro</title>
    <style>
        :root {{
            --bg: #060911;
            --card: #0f172a;
            --border: #1e293b;
            --text: #f8fafc;
            --muted: #94a3b8;
            --accent: #10b981;
            --blue: #38bdf8;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }}
        body {{ background-color: var(--bg); color: var(--text); line-height: 1.5; padding-bottom: 80px; }}
        .navbar {{ background: rgba(15, 23, 42, 0.95); backdrop-filter: blur(12px); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; flex-wrap: wrap; gap: 12px; }}
        .logo {{ font-size: 1.35rem; font-weight: 900; color: #fff; display: flex; align-items: center; gap: 8px; }}
        .logo span {{ background: linear-gradient(90deg, #10b981, #38bdf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .nav-tabs {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .nav-tab {{ background: #1e293b; color: #cbd5e1; border: 1px solid #334155; padding: 8px 16px; border-radius: 99px; font-size: 0.83rem; font-weight: 700; cursor: pointer; transition: all 0.2s; }}
        .nav-tab.active, .nav-tab:hover {{ background: linear-gradient(90deg, #10b981, #059669); color: #022c22; border-color: #10b981; box-shadow: 0 0 15px rgba(16,185,129,0.3); }}
        .nav-right {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
        .btn-refresh-live {{ background: #ef444425; color: #f87171; border: 1px solid #ef444460; padding: 6px 12px; border-radius: 999px; font-size: 0.76rem; font-weight: 800; cursor: pointer; transition: all 0.2s; }}
        .btn-refresh-live:hover {{ background: #ef4444; color: #fff; }}
        .quota-pill {{ font-size: 0.75rem; background: #38bdf818; color: #38bdf8; border: 1px solid #38bdf850; padding: 5px 11px; border-radius: 999px; font-weight: 700; }}

        .container {{ max-width: 1260px; margin: 0 auto; padding: 22px 16px; }}
        .stats-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: 12px; margin-bottom: 22px; }}
        .stat-box {{ background: var(--card); border: 1px solid var(--border); padding: 15px; border-radius: 12px; text-align: center; }}
        .stat-box .s-label {{ font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-box .s-val {{ font-size: 1.5rem; font-weight: 800; margin-top: 4px; color: #fff; }}

        .controls-panel {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 16px; margin-bottom: 24px; display: flex; flex-direction: column; gap: 14px; }}
        .search-row {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; justify-content: space-between; }}
        .search-input-wrap {{ flex: 1; min-width: 260px; }}
        .search-input {{ width: 100%; background: #060911; border: 1px solid #334155; color: #fff; padding: 11px 16px; border-radius: 10px; font-size: 0.9rem; font-weight: 600; outline: none; }}
        .search-input:focus {{ border-color: var(--blue); }}
        .filters-row {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; }}
        .day-tabs {{ display: flex; gap: 6px; flex-wrap: wrap; }}
        .day-tab {{ background: #1e293b; color: var(--muted); border: 1px solid #334155; padding: 7px 13px; border-radius: 8px; font-size: 0.8rem; font-weight: 700; cursor: pointer; }}
        .day-tab.active {{ background: var(--blue); color: #082f49; border-color: var(--blue); }}
        .filters-group {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
        .select-pro {{ background: #060911; color: #fff; border: 1px solid #334155; padding: 8px 12px; border-radius: 8px; font-size: 0.8rem; font-weight: 600; cursor: pointer; outline: none; }}
        .bankroll-calc {{ display: flex; align-items: center; gap: 6px; background: #060911; border: 1px solid #10b98160; padding: 6px 12px; border-radius: 8px; font-size: 0.8rem; }}
        .bankroll-calc input {{ width: 70px; background: transparent; border: none; color: #10b981; font-weight: 800; font-size: 0.9rem; text-align: right; outline: none; }}

        .section-title {{ font-size: 1.15rem; font-weight: 800; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }}
        .picks-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(365px, 1fr)); gap: 18px; margin-bottom: 42px; }}
        .pick-card, .explorer-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px; position: relative; overflow: hidden; transition: transform 0.2s, border-color 0.2s; }}
        .pick-card:hover, .explorer-card:hover {{ transform: translateY(-3px); border-color: #38bdf860; }}
        .pick-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; gap: 6px; flex-wrap: wrap; }}
        .day-pill {{ font-size: 0.7rem; font-weight: 800; padding: 3px 8px; border-radius: 6px; }}
        .league-tag {{ font-size: 0.72rem; color: var(--muted); font-weight: 700; text-transform: uppercase; }}
        .tier-badge {{ font-size: 0.7rem; font-weight: 700; padding: 3px 9px; border-radius: 99px; }}
        .match-title {{ font-size: 1.08rem; font-weight: 800; color: #fff; }}
        .live-score-badge {{ font-size: 0.75rem; font-weight: 800; padding: 4px 9px; border-radius: 8px; background: #1e293b; color: #cbd5e1; white-space: nowrap; }}
        .live-active-badge {{ background: #ef444430 !important; color: #f87171 !important; border: 1px solid #ef4444; animation: pulseLive 2s infinite; }}
        @keyframes pulseLive {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.65; }} 100% {{ opacity: 1; }} }}

        .market-box {{ background: #060911; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
        .market-label {{ font-size: 0.64rem; color: var(--muted); font-weight: 700; }}
        .market-name {{ font-size: 0.95rem; font-weight: 800; color: #38bdf8; }}
        .odd-pill {{ background: #10b98120; color: #10b981; font-weight: 800; font-size: 1.1rem; padding: 6px 12px; border-radius: 8px; border: 1px solid #10b98140; }}

        /* BARRA DE PROGRESO EN VIVO DEL PICK */
        .live-tracker-box {{ background: #060911; border: 1px solid #1e293b; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; font-size: 0.74rem; }}
        .lt-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; gap: 6px; flex-wrap: wrap; }}
        .lt-bar-bg {{ width: 100%; height: 7px; background: #1e293b; border-radius: 99px; overflow: hidden; }}
        .lt-bar-fill {{ height: 100%; background: linear-gradient(90deg, #38bdf8, #10b981); transition: width 0.5s ease; }}

        /* DESPLEGABLE DE ESTADÍSTICAS EN VIVO (ESTILO CASA DE APUESTAS) */
        .live-stats-drawer {{ background: #080d1a; border: 1px solid #38bdf840; border-radius: 8px; padding: 8px 12px; margin-bottom: 12px; font-size: 0.76rem; }}
        .live-stats-drawer summary {{ cursor: pointer; font-weight: 800; color: #38bdf8; outline: none; list-style: none; display: flex; justify-content: space-between; align-items: center; }}
        .live-stats-drawer summary::after {{ content: "📡 VIVO ▼"; font-size: 0.65rem; background: #38bdf820; padding: 2px 6px; border-radius: 4px; }}
        .live-stats-body {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid #1e293b; }}
        .stat-bar-item {{ margin-bottom: 8px; }}
        .sb-head {{ display: flex; justify-content: space-between; font-size: 0.73rem; font-weight: 700; margin-bottom: 3px; color: #e2e8f0; }}
        .sb-track {{ display: flex; height: 7px; border-radius: 99px; overflow: hidden; background: #1e293b; }}
        .events-feed {{ margin-top: 8px; padding-top: 8px; border-top: 1px dashed #334155; font-size: 0.72rem; color: #cbd5e1; max-height: 110px; overflow-y: auto; }}

        .metrics-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; text-align: center; background: #060911; padding: 10px; border-radius: 8px; }}
        .m-label {{ display: block; font-size: 0.64rem; color: var(--muted); }}
        .m-val {{ font-size: 0.86rem; font-weight: 700; }}
        .l-bars {{ font-size: 0.75rem; color: #cbd5e1; background: #1e293b60; padding: 7px 10px; border-radius: 6px; margin-bottom: 10px; }}

        .betano-checker {{ background: #18181b; border: 1px solid #38bdf840; border-radius: 8px; padding: 7px 10px; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 0.74rem; flex-wrap: wrap; }}
        .betano-checker input {{ width: 58px; background: #09090b; border: 1px solid #38bdf8; color: #38bdf8; font-weight: 800; border-radius: 5px; padding: 3px 6px; text-align: center; font-size: 0.8rem; outline: none; }}
        .betano-res {{ font-weight: 800; font-size: 0.74rem; }}

        .btn-lab {{ width: 100%; background: linear-gradient(90deg, #1e293b, #0f172a); color: #38bdf8; border: 1px solid #38bdf850; padding: 8px 12px; border-radius: 8px; font-size: 0.78rem; font-weight: 700; cursor: pointer; transition: all 0.2s; }}
        .btn-lab:hover {{ background: #38bdf8; color: #060911; }}

        .exp-prob-bar {{ display: flex; height: 22px; border-radius: 6px; overflow: hidden; font-size: 0.7rem; font-weight: 800; text-align: center; line-height: 22px; }}
        .p-loc {{ background: #10b981; color: #022c22; }}
        .p-emp {{ background: #475569; color: #f8fafc; }}
        .p-vis {{ background: #3b82f6; color: #eff6ff; }}

        .tactical-drawer {{ background: #060911; border: 1px solid #1e293b; border-radius: 8px; padding: 8px 12px; font-size: 0.76rem; }}
        .tactical-drawer summary {{ cursor: pointer; font-weight: 700; color: var(--muted); outline: none; list-style: none; display: flex; justify-content: space-between; align-items: center; }}
        .tactical-drawer summary::after {{ content: "▼"; font-size: 0.65rem; }}
        .tactical-drawer[open] summary::after {{ transform: rotate(180deg); }}
        .drawer-content {{ margin-top: 10px; padding-top: 10px; border-top: 1px dashed #334155; display: flex; flex-direction: column; gap: 6px; color: #cbd5e1; }}
        .drawer-row {{ display: flex; justify-content: space-between; gap: 8px; }}
        .drawer-note {{ margin-top: 6px; color: var(--muted); font-style: italic; }}

        .modal-backdrop {{ position: fixed; inset: 0; background: rgba(6, 9, 17, 0.85); backdrop-filter: blur(6px); display: none; align-items: center; justify-content: center; z-index: 200; padding: 16px; }}
        .modal-card {{ background: #0f172a; border: 1px solid #38bdf860; border-radius: 16px; max-width: 800px; width: 100%; max-height: 90vh; overflow-y: auto; padding: 22px; box-shadow: 0 20px 50px rgba(0,0,0,0.8); }}
        .modal-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 16px; }}
        .btn-close {{ background: #ef444425; color: #f87171; border: 1px solid #ef444450; padding: 6px 12px; border-radius: 8px; cursor: pointer; font-weight: 800; }}
        .h2h-row {{ margin-bottom: 12px; }}
        .h2h-labels {{ display: flex; justify-content: space-between; font-size: 0.78rem; font-weight: 700; margin-bottom: 4px; }}
        .h2h-track {{ display: flex; height: 10px; border-radius: 99px; overflow: hidden; background: #1e293b; }}
        .prob-matrix {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-top: 16px; }}
        .pm-box {{ background: #060911; border: 1px solid var(--border); padding: 10px; border-radius: 8px; text-align: center; }}
        .pm-box span {{ display: block; font-size: 0.7rem; color: var(--muted); }}
        .pm-box b {{ font-size: 1rem; color: #10b981; }}

        .blur-lock {{ filter: blur(6px); user-select: none; pointer-events: none; }}
        .vip-overlay {{ position: absolute; inset: 0; background: rgba(11, 15, 25, 0.75); display: flex; align-items: center; justify-content: center; padding: 20px; text-align: center; }}
        .vip-box {{ background: #111827; border: 1px solid #10b981; padding: 16px; border-radius: 12px; }}
        .btn-vip {{ display: inline-block; background: #10b981; color: #052e16; font-weight: 800; text-decoration: none; padding: 8px 16px; border-radius: 8px; font-size: 0.85rem; margin-top: 8px; }}
        .table-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.83rem; text-align: left; }}
        th {{ background: #060911; color: var(--muted); padding: 12px 14px; font-size: 0.73rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }}
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
        <div class="logo">⚡ IABET <span>QUANT PRO v18.0</span></div>
        <div class="nav-tabs">
            <button class="nav-tab active" onclick="cambiarVista('PICKS', this)">🎯 Picks IA + Live Stats ({len(picks_jornada)})</button>
            <button class="nav-tab" onclick="cambiarVista('EXPLORER', this)">📊 Buscador FutbolDataPro ({len(catalogo_partidos)} Partidos)</button>
            <button class="nav-tab" onclick="cambiarVista('AUDITORIA', this)">📋 Historial Auditado</button>
        </div>
        <div class="nav-right">
            <button class="btn-refresh-live" onclick="sincronizarTodosLosPartidosEnVivo()" id="btnSyncLive">📡 Actualizar Stats en Vivo (Auto 30s)</button>
            <div class="quota-pill">🎫 OddsAPI: {cred_txt} disp.</div>
        </div>
    </nav>

    <div class="container">
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
                <div class="s-label">Estado Live / Racha</div>
                <div class="s-val" id="contadorEnVivoGlobal">🔥 {res['racha_actual']}</div>
            </div>
        </div>

        <div class="controls-panel" id="panelControles">
            <div class="search-row">
                <div class="search-input-wrap">
                    <input type="text" id="inputBuscador" class="search-input" placeholder="🔍 Buscar equipo, partido o liga en vivo (ej. Real Madrid, Serie A, Universitario, Córners)..." oninput="aplicarFiltros()">
                </div>
                <div class="bankroll-calc">
                    <span>💰 Mi Capital: S/</span>
                    <input type="number" id="inputBankroll" value="{res['bankroll_actual']:.0f}" min="10" step="10" oninput="recalcularStakes()">
                </div>
            </div>
            <div class="filters-row">
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
                        <option value="CORNERS">🚩 Córners</option>
                        <option value="FALTAS">🛑 Faltas Totales</option>
                        <option value="TIROS">🎯 Tiros al Arco</option>
                        <option value="TARJETAS">🟨 Tarjetas</option>
                        <option value="GOLES">⚽ Goles / BTTS</option>
                        <option value="DOBLE">🛡️ Doble Oportunidad</option>
                        <option value="1X2">🏆 Ganador (1X2)</option>
                    </select>
                    <select id="filtroTier" class="select-pro" onchange="aplicarFiltros()">
                        <option value="ALL">💎 Todos los Tiers</option>
                        <option value="TIER S">💎 Solo TIER S (&le; @1.76)</option>
                        <option value="TIER A">🥇 Solo TIER A (+EV)</option>
                        <option value="TIER B">🥈 Solo TIER B (Valor)</option>
                    </select>
                </div>
            </div>
        </div>

        <!-- VISTA 1: PICKS IA CON TELEMETRÍA EN VIVO -->
        <div id="vistaPicks">
            <div class="section-title">
                <span id="contadorPicks">🎯 Pronósticos Calibrados + Telemetría en Tiempo Real ({len(picks_jornada)} seleccionados)</span>
                <small id="relojSync" style="color:#10b981; font-size:0.8rem; font-weight:700;">📡 Telemetría en vivo conectada · Escaneo base: {ahora_peru}</small>
            </div>
            <div class="picks-grid" id="gridPicks">
                {tarjetas_picks_html}
            </div>
        </div>

        <!-- VISTA 2: EXPLORADOR FUTBOLDATAPRO -->
        <div id="vistaExplorer" style="display:none;">
            <div class="section-title">
                <span id="contadorExplorer">📊 Laboratorio Estadístico FutbolDataPro ({len(catalogo_partidos)} partidos analizados)</span>
                <small style="color:#38bdf8; font-size:0.8rem;">Haz clic en cualquier partido para ver sus estadísticas en vivo y sus 12 mercados</small>
            </div>
            <div class="picks-grid" id="gridExplorer">
                {tarjetas_explorador_html}
            </div>
        </div>

        <!-- VISTA 3: AUDITORÍA -->
        <div id="vistaAuditoria" style="display:none;">
            <div class="section-title">
                <span>📋 Auditoría Automática en Tiempo Real (Goles, Córners, Faltas, Tiros y Tarjetas)</span>
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
    </div>

    <div class="modal-backdrop" id="modalLab" onclick="if(event.target===this) cerrarModal()">
        <div class="modal-card" id="modalContent"></div>
    </div>

    <script>
        const CATALOGO_PARTIDOS = {json_catalogo};
        const CACHE_LIVE_STATS = {{}};
        let diaSeleccionado = 'ALL';

        function extraerStatEquipo(teamBox, statNames) {{
            if (!teamBox || !teamBox.statistics) return 0;
            for (const st of teamBox.statistics) {{
                if (statNames.includes(st.name)) {{
                    return parseFloat(st.displayValue) || 0;
                }}
            }}
            return 0;
        }}

        async function actualizarPartidoEnVivo(eventId) {{
            if (!eventId) return null;
            try {{
                const r = await fetch(`https://site.api.espn.com/apis/site/v2/sports/soccer/all/summary?event=${{eventId}}`);
                if (!r.ok) return null;
                const data = await r.json();

                const comp = (data.header && data.header.competitions && data.header.competitions[0]) || {{}};
                const stObj = comp.status || {{}};
                const state = (stObj.type && stObj.type.state) || 'pre';
                const clock = (stObj.type && stObj.type.shortDetail) || '';
                const comps = comp.competitors || [];
                const tHome = comps.find(c => c.homeAway === 'home') || comps[0] || {{}};
                const tAway = comps.find(c => c.homeAway === 'away') || comps[1] || {{}};
                const gh = parseInt(tHome.score || 0);
                const ga = parseInt(tAway.score || 0);

                const boxTeams = (data.boxscore && data.boxscore.teams) || [];
                const bHome = boxTeams[0] || {{}};
                const bAway = boxTeams[1] || {{}};

                const statsVivo = {{
                    state: state,
                    clock: clock,
                    gh: gh,
                    ga: ga,
                    posL: extraerStatEquipo(bHome, ['possessionPct']) || 50,
                    posV: extraerStatEquipo(bAway, ['possessionPct']) || 50,
                    sotL: extraerStatEquipo(bHome, ['shotsOnTarget', 'shotsOnGoal']),
                    sotV: extraerStatEquipo(bAway, ['shotsOnTarget', 'shotsOnGoal']),
                    tirL: extraerStatEquipo(bHome, ['totalShots']),
                    tirV: extraerStatEquipo(bAway, ['totalShots']),
                    corL: extraerStatEquipo(bHome, ['wonCorners', 'cornerKicks']),
                    corV: extraerStatEquipo(bAway, ['wonCorners', 'cornerKicks']),
                    faltL: extraerStatEquipo(bHome, ['foulsCommitted']),
                    faltV: extraerStatEquipo(bAway, ['foulsCommitted']),
                    amarL: extraerStatEquipo(bHome, ['yellowCards']),
                    amarV: extraerStatEquipo(bAway, ['yellowCards']),
                    rojaL: extraerStatEquipo(bHome, ['redCards']),
                    rojaV: extraerStatEquipo(bAway, ['redCards']),
                    eventos: []
                }};

                const details = comp.details || data.keyEvents || [];
                details.slice(-6).forEach(d => {{
                    const min = (d.clock && d.clock.displayValue) || '';
                    const tipo = (d.type && d.type.text) || '';
                    const jug = (d.participants && d.participants[0] && d.participants[0].athlete && d.participants[0].athlete.displayName) || '';
                    if (min && (tipo || jug)) {{
                        statsVivo.eventos.push(`⏱️ ${{min}} — ${{tipo}} ${{jug}}`);
                    }}
                }});

                CACHE_LIVE_STATS[eventId] = statsVivo;
                renderizarTelemetriaEnTarjeta(eventId, statsVivo);
                return statsVivo;
            }} catch (e) {{
                return null;
            }}
        }}

        function renderizarBarraDual(titulo, valL, valV, colorL='#10b981', colorV='#3b82f6', sufijo='') {{
            const tot = Math.max(1, valL + valV);
            const pctL = (valL / tot) * 100;
            return `
                <div class="stat-bar-item">
                    <div class="sb-head">
                        <span>${{valL}}${{sufijo}}</span>
                        <span>${{titulo}} (Total: ${{valL + valV}}${{sufijo}})</span>
                        <span>${{valV}}${{sufijo}}</span>
                    </div>
                    <div class="sb-track">
                        <div style="width:${{pctL}}%; background:${{colorL}};"></div>
                        <div style="width:${{100 - pctL}}%; background:${{colorV}};"></div>
                    </div>
                </div>
            `;
        }}

        function renderizarTelemetriaEnTarjeta(eventId, st) {{
            const badgeScore = document.getElementById(`score-${{eventId}}`);
            const expScore = document.getElementById(`exp-score-${{eventId}}`);
            const card = document.getElementById(`card-${{eventId}}`);

            let txtMarcador = '⏳ Pre-Partido';
            if (st.state === 'in') {{
                txtMarcador = `🔴 ${{st.gh}} - ${{st.ga}} (${{st.clock}})`;
                if (badgeScore) badgeScore.classList.add('live-active-badge');
                if (expScore) expScore.classList.add('live-active-badge');
            }} else if (st.state === 'post') {{
                txtMarcador = `🏁 Final: ${{st.gh}} - ${{st.ga}}`;
                if (badgeScore) badgeScore.classList.remove('live-active-badge');
            }}

            if (badgeScore) badgeScore.innerText = txtMarcador;
            if (expScore) expScore.innerText = txtMarcador;

            if (card) {{
                const cat = card.getAttribute('data-cat') || '';
                const linea = parseFloat(card.getAttribute('data-linea')) || 2.5;
                const mercado = card.getAttribute('data-mercado') || '';
                const ltStatus = document.getElementById(`lt-status-${{eventId}}`);
                const ltVal = document.getElementById(`lt-val-${{eventId}}`);
                const ltBar = document.getElementById(`lt-bar-${{eventId}}`);

                let actual = 0;
                let unidad = '';
                if (cat === 'FALTAS') {{ actual = st.faltL + st.faltV; unidad = 'Faltas'; }}
                else if (cat === 'TIROS') {{ actual = st.sotL + st.sotV; unidad = 'Tiros al Arco'; }}
                else if (cat === 'CORNERS') {{ actual = st.corL + st.corV; unidad = 'Córners'; }}
                else if (cat === 'TARJETAS') {{ actual = st.amarL + st.amarV + st.rojaL + st.rojaV; unidad = 'Tarjetas'; }}
                else {{ actual = st.gh + st.ga; unidad = 'Goles'; }}

                if (st.state === 'in' || st.state === 'post') {{
                    const meta = linea > 0 ? Math.ceil(linea) : 1;
                    const pct = Math.min(100, Math.round((actual / Math.max(1, meta)) * 100));
                    if (ltStatus) ltStatus.innerHTML = `<b style="color:#10b981;">📡 EN VIVO (${{st.clock}}):</b> Actual <b>${{actual}} ${{unidad}}</b>`;
                    if (ltVal) ltVal.innerText = `Línea: ${{mercado}} (${{pct}}%)`;
                    if (ltBar) ltBar.style.width = `${{pct}}%`;
                }}
            }}

            const bodyLive = document.getElementById(`live-body-${{eventId}}`);
            if (bodyLive) {{
                if (st.state === 'pre' && (st.tirL + st.tirV + st.faltL + st.faltV === 0)) {{
                    bodyLive.innerHTML = `<div style="text-align:center; color:#94a3b8; padding:6px;">⏳ El partido aún no ha iniciado. Apenas ruede el balón, aquí verás Posesión, Tiros al Arco, Córners, Faltas y Tarjetas en tiempo real.</div>`;
                }} else {{
                    const evHtml = st.eventos.length > 0
                        ? `<div class="events-feed"><b>⚡ Últimas Incidencias en Vivo:</b><br>${{st.eventos.join('<br>')}}</div>`
                        : '';
                    bodyLive.innerHTML = `
                        ${{renderizarBarraDual('⚽ Posesión (%)', st.posL, st.posV, '#10b981', '#3b82f6', '%')}}
                        ${{renderizarBarraDual('🎯 Tiros al Arco (SOT)', st.sotL, st.sotV, '#10b981', '#38bdf8')}}
                        ${{renderizarBarraDual('🚀 Remates Totales', st.tirL, st.tirV, '#38bdf8', '#6366f1')}}
                        ${{renderizarBarraDual('🚩 Córners', st.corL, st.corV, '#10b981', '#3b82f6')}}
                        ${{renderizarBarraDual('🛑 Faltas Cometidas', st.faltL, st.faltV, '#f59e0b', '#ef4444')}}
                        ${{renderizarBarraDual('🟨 Tarjetas (Amarillas+Rojas)', st.amarL + st.rojaL, st.amarV + st.rojaV, '#eab308', '#ef4444')}}
                        ${{evHtml}}
                    `;
                }}
            }}
        }}

        async function sincronizarTodosLosPartidosEnVivo() {{
            const btn = document.getElementById('btnSyncLive');
            if (btn) btn.innerText = '🔄 Sincronizando...';
            const ids = new Set();
            document.querySelectorAll('[data-event-id]').forEach(el => {{
                const id = el.getAttribute('data-event-id');
                if (id) ids.add(id);
            }});

            let enVivoCount = 0;
            for (const id of ids) {{
                const res = await actualizarPartidoEnVivo(id);
                if (res && res.state === 'in') enVivoCount++;
            }}

            const ahoraStr = new Date().toLocaleTimeString('es-PE', {{ hour: '2-digit', minute: '2-digit', second: '2-digit' }});
            const rel = document.getElementById('relojSync');
            if (rel) rel.innerText = `📡 Telemetría sincronizada a las ${{ahoraStr}} (${{enVivoCount}} partidos en juego ahora mismo)`;
            if (btn) btn.innerText = '📡 Actualizar Stats en Vivo (Auto 30s)';
        }}

        function cambiarVista(vista, btn) {{
            document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('vistaPicks').style.display = (vista === 'PICKS') ? 'block' : 'none';
            document.getElementById('vistaExplorer').style.display = (vista === 'EXPLORER') ? 'block' : 'none';
            document.getElementById('vistaAuditoria').style.display = (vista === 'AUDITORIA') ? 'block' : 'none';
            document.getElementById('panelControles').style.display = (vista === 'AUDITORIA') ? 'none' : 'flex';
        }}

        function filtrarDia(dia, btn) {{
            diaSeleccionado = dia;
            document.querySelectorAll('.day-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            aplicarFiltros();
        }}

        function aplicarFiltros() {{
            const q = (document.getElementById('inputBuscador').value || '').toLowerCase().trim();
            const liga = document.getElementById('filtroLiga').value;
            const cat = document.getElementById('filtroMercado').value;
            const tier = document.getElementById('filtroTier').value;

            let visPicks = 0;
            const cardsP = document.querySelectorAll('.pick-card');
            cardsP.forEach(card => {{
                const okQ = !q || card.getAttribute('data-search').includes(q);
                const okDia = (diaSeleccionado === 'ALL') || (card.getAttribute('data-dia') === diaSeleccionado);
                const okLiga = (liga === 'ALL') || (card.getAttribute('data-liga') === liga);
                const okCat = (cat === 'ALL') || (card.getAttribute('data-cat') === cat);
                const okTier = (tier === 'ALL') || (card.getAttribute('data-tier') === tier);
                if (okQ && okDia && okLiga && okCat && okTier) {{
                    card.style.display = 'block';
                    visPicks++;
                }} else {{
                    card.style.display = 'none';
                }}
            }});
            document.getElementById('contadorPicks').innerText = `🎯 Pronósticos Activos Mostrados: ${{visPicks}} de ${{cardsP.length}}`;

            let visExp = 0;
            const cardsE = document.querySelectorAll('.explorer-card');
            cardsE.forEach(card => {{
                const okQ = !q || card.getAttribute('data-search').includes(q);
                const okDia = (diaSeleccionado === 'ALL') || (card.getAttribute('data-dia') === diaSeleccionado);
                const okLiga = (liga === 'ALL') || (card.getAttribute('data-liga') === liga);
                if (okQ && okDia && okLiga) {{
                    card.style.display = 'block';
                    visExp++;
                }} else {{
                    card.style.display = 'none';
                }}
            }});
            document.getElementById('contadorExplorer').innerText = `📊 Partidos en Laboratorio: ${{visExp}} de ${{cardsE.length}}`;
        }}

        async function abrirLaboratorioPartido(key) {{
            const m = CATALOGO_PARTIDOS[key];
            if (!m) return;
            const pctXgL = (m.xg_l / Math.max(0.1, m.xg_l + m.xg_v)) * 100;
            const pctCorL = (m.cor_l / Math.max(0.1, m.cor_tot)) * 100;
            const pctSotL = (m.sot_l / Math.max(0.1, m.sot_tot)) * 100;
            const pctFaltL = (m.falt_l / Math.max(0.1, m.falt_tot)) * 100;

            let bloqueVivoModal = `<div id="modal-live-${{m.event_id}}" style="background:#060911; border:1px solid #38bdf840; padding:12px; border-radius:10px; margin-bottom:14px; text-align:center; color:#94a3b8;">🔄 Consultando estadísticas en tiempo real del partido...</div>`;

            const html = `
                <div class="modal-header">
                    <div>
                        <span class="league-tag">${{m.liga}} · 📅 ${{m.etiqueta_dia}} ${{m.hora_peru}}</span>
                        <h2 style="font-size:1.35rem; color:#fff; margin-top:4px;">⚽ ${{m.local}} vs ${{m.visita}}</h2>
                    </div>
                    <button class="btn-close" onclick="cerrarModal()">✕ Cerrar</button>
                </div>

                ${{bloqueVivoModal}}

                <h4 style="margin-bottom:10px; font-size:0.88rem; color:#10b981;">📐 Proyecciones Pre-Partido (Modelo L5 · L10 · L20 + Monte Carlo 1M):</h4>
                <div class="h2h-row">
                    <div class="h2h-labels"><span>${{m.local}}: ${{m.xg_l.toFixed(2)}} xG</span><span>Goles Esperados (xG)</span><span>${{m.visita}}: ${{m.xg_v.toFixed(2)}} xG</span></div>
                    <div class="h2h-track"><div style="width:${{pctXgL}}%; background:#10b981;"></div><div style="width:${{100-pctXgL}}%; background:#3b82f6;"></div></div>
                </div>
                <div class="h2h-row">
                    <div class="h2h-labels"><span>${{m.cor_l.toFixed(1)}}</span><span>🚩 Proyección Córners (Total: ${{m.cor_tot.toFixed(1)}})</span><span>${{m.cor_v.toFixed(1)}}</span></div>
                    <div class="h2h-track"><div style="width:${{pctCorL}}%; background:#10b981;"></div><div style="width:${{100-pctCorL}}%; background:#3b82f6;"></div></div>
                </div>
                <div class="h2h-row">
                    <div class="h2h-labels"><span>${{m.sot_l.toFixed(1)}}</span><span>🎯 Tiros al Arco (Total: ${{m.sot_tot.toFixed(1)}})</span><span>${{m.sot_v.toFixed(1)}}</span></div>
                    <div class="h2h-track"><div style="width:${{pctSotL}}%; background:#10b981;"></div><div style="width:${{100-pctSotL}}%; background:#3b82f6;"></div></div>
                </div>
                <div class="h2h-row">
                    <div class="h2h-labels"><span>${{m.falt_l.toFixed(1)}}f / ${{m.tarj_l.toFixed(1)}}🟨</span><span>🛑 Faltas (${{m.falt_tot.toFixed(1)}}) & Tarjetas (${{m.tarj_tot.toFixed(1)}})</span><span>${{m.falt_v.toFixed(1)}}f / ${{m.tarj_v.toFixed(1)}}🟨</span></div>
                    <div class="h2h-track"><div style="width:${{pctFaltL}}%; background:#f59e0b;"></div><div style="width:${{100-pctFaltL}}%; background:#ef4444;"></div></div>
                </div>

                <div class="l-bars" style="margin-top:14px;">
                    🎲 <b>Top 3 Marcadores Exactos (Dixon-Coles):</b> ${{m.top_scores}} | 📈 <b>Forma ESPN:</b> L: ${{m.form_l}} vs V: ${{m.form_v}}
                </div>

                <h4 style="margin-top:16px; font-size:0.9rem; color:#38bdf8;">📊 Matriz de 12 Probabilidades Exactas y Cuota Mínima Justa (Sin Margen):</h4>
                <div class="prob-matrix">
                    <div class="pm-box"><span>Victoria Local (1)</span><b>${{m.p_1.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_1)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>Empate (X)</span><b>${{m.p_x.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_x)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>Victoria Visita (2)</span><b>${{m.p_2.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_2)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>Doble Oport. 1X</span><b>${{m.p_1x.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_1x)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>Doble Oport. X2</span><b>${{m.p_x2.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_x2)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>Más de 1.5 Goles</span><b>${{m.p_o15.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_o15)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>Más de 2.5 Goles</span><b>${{m.p_o25.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_o25)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>Menos de 3.5 Goles</span><b>${{m.p_u35.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_u35)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>Ambos Anotan (Sí)</span><b>${{m.p_btts.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_btts)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>🚩 Más 8.5 Córners</span><b>${{m.p_cor_o85.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_cor_o85)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>🚩 Menos 10.5 Córners</span><b>${{m.p_cor_u105.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_cor_u105)).toFixed(2)}}</small></div>
                    <div class="pm-box"><span>🟨 Menos 4.5 Tarjetas</span><b>${{m.p_tarj_u45.toFixed(1)}}%</b><small style="display:block;color:#94a3b8;">Justa: @${{(100/Math.max(5,m.p_tarj_u45)).toFixed(2)}}</small></div>
                </div>
            `;
            document.getElementById('modalContent').innerHTML = html;
            document.getElementById('modalLab').style.display = 'flex';

            const st = await actualizarPartidoEnVivo(m.event_id);
            const boxModal = document.getElementById(`modal-live-${{m.event_id}}`);
            if (boxModal && st) {{
                if (st.state === 'pre' && (st.tirL + st.tirV === 0)) {{
                    boxModal.innerHTML = `⏳ <b>Partido por iniciar (${{m.hora_peru}} Hora Perú)</b> — Las barras en tiempo real se activarán automáticamente al comenzar el encuentro.`;
                }} else {{
                    boxModal.innerHTML = `
                        <div style="font-weight:800; color:#f87171; margin-bottom:8px;">📡 ESTADÍSTICAS EN TIEMPO REAL (${{st.clock}} | Marcador: ${{st.gh}} - ${{st.ga}})</div>
                        ${{renderizarBarraDual('⚽ Posesión (%)', st.posL, st.posV, '#10b981', '#3b82f6', '%')}}
                        ${{renderizarBarraDual('🎯 Tiros al Arco (SOT)', st.sotL, st.sotV, '#10b981', '#38bdf8')}}
                        ${{renderizarBarraDual('🚩 Córners', st.corL, st.corV, '#10b981', '#3b82f6')}}
                        ${{renderizarBarraDual('🛑 Faltas Cometidas', st.faltL, st.faltV, '#f59e0b', '#ef4444')}}
                        ${{renderizarBarraDual('🟨 Tarjetas', st.amarL + st.rojaL, st.amarV + st.rojaV, '#eab308', '#ef4444')}}
                    `;
                }}
            }}
        }}

        function cerrarModal() {{
            document.getElementById('modalLab').style.display = 'none';
        }}

        function recalcularStakes() {{
            const bank = parseFloat(document.getElementById('inputBankroll').value) || 100;
            document.querySelectorAll('.stake-dinamico').forEach(el => {{
                const pct = parseFloat(el.getAttribute('data-pct')) || 0.025;
                const monto = Math.max(1.0, Math.min(bank * 0.05, Math.round((bank * pct) * 2) / 2));
                el.innerText = 'S/ ' + monto.toFixed(2);
            }});
        }}

        function verificarCuotaCasa(inputEl) {{
            const cuotaUsuario = parseFloat(inputEl.value) || 1.0;
            const probReal = parseFloat(inputEl.getAttribute('data-prob')) || 0.5;
            const resSpan = inputEl.parentElement.querySelector('.betano-res');
            const pImp = 1.0 / Math.max(1.01, cuotaUsuario);
            const edge = (probReal - pImp) * 100.0;

            if (cuotaUsuario >= 2.05 && probReal > 0.65) {{
                resSpan.style.color = '#f59e0b';
                resSpan.innerText = `⚠️ Cuota alta: Reducir Stake a mitad`;
            }} else if (edge >= 1.5) {{
                resSpan.style.color = '#10b981';
                resSpan.innerText = `✅ Rentable (+${{edge.toFixed(1)}}% EV)`;
            }} else if (edge > 0) {{
                resSpan.style.color = '#f59e0b';
                resSpan.innerText = `⚖️ Margen justo (+${{edge.toFixed(1)}}% EV)`;
            }} else {{
                resSpan.style.color = '#ef4444';
                resSpan.innerText = `❌ NO APOSTAR (${{edge.toFixed(1)}}% EV)`;
            }}
        }}

        function filtrarTablaAuditoria() {{
            const est = document.getElementById('filtroAuditoria').value;
            document.querySelectorAll('.audit-row').forEach(row => {{
                const rEst = row.getAttribute('data-est');
                row.style.display = (est === 'ALL' || rEst === est) ? '' : 'none';
            }});
        }}

        // Iniciar telemetría en tiempo real al abrir la página y actualizar cada 30 segundos
        window.addEventListener('DOMContentLoaded', () => {{
            sincronizarTodosLosPartidosEnVivo();
            setInterval(sincronizarTodosLosPartidosEnVivo, 30000);
        }});
    </script>
</body>
</html>"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_completo)

# ==============================================================================
# 6. ORQUESTADOR MAESTRO v18.0
# ==============================================================================
def enviar_telegram_seguro(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    bloques = [texto[i:i+3900] for i in range(0, len(texto), 3900)]
    for b in bloques:
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": b}, timeout=10)
        except Exception:
            pass

def ejecutar_iabet_v18():
    t0 = time.time()
    caches_csv, espn_partidos, eventos_activos = obtener_datos_pipeline_v18()

    auditoria = cargar_auditoria()

    ligas_activas_hoy = []
    for ev in eventos_activos:
        if ev.get("odds_key") and ev["odds_key"] not in ligas_activas_hoy:
            ligas_activas_hoy.append(ev["odds_key"])

    mapa_odds_api, creditos_restantes = consultar_the_odds_api_smart(ligas_activas_hoy, auditoria)

    auditoria = auditar_picks_con_espn(auditoria, espn_partidos, creditos_restantes)
    bankroll_dinamico = max(50.0, float(auditoria["resumen"]["bankroll_actual"]))

    candidatos = []
    catalogo_partidos = []
    partidos_analizados = 0
    vistos = set()

    for ev in eventos_activos:
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

        base_t = ev.get("base_tarj", 4.4)
        base_f = ev.get("base_falt", 24.2)
        base_s = ev.get("base_sot", 8.6)
        base_c = ev.get("base_cor", 9.6)

        df_liga = caches_csv.get(liga)
        st_l = analizar_equipo_profundo_v18(df_liga, loc, base_f, base_s, base_t, base_c, es_local=True)
        st_v = analizar_equipo_profundo_v18(df_liga, vis, base_f, base_s, base_t, base_c, es_local=False)

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
            casa_1 = f"{datos_odds['casa_1']}"
            casa_2 = f"{datos_odds['casa_2']}"
            c_o25 = datos_odds["best_o25"] if datos_odds.get("best_o25", 0) > 1.35 else 1.88
            c_u25 = datos_odds["best_u25"] if datos_odds.get("best_u25", 0) > 1.35 else 1.92
            mean_o25 = datos_odds["mean_o25"] if datos_odds.get("mean_o25", 0) > 1.35 else c_o25
            mean_u25 = datos_odds["mean_u25"] if datos_odds.get("mean_u25", 0) > 1.35 else c_u25
        else:
            c_1 = ev["c_1"]
            c_x = ev["c_x"]
            c_2 = ev["c_2"]
            c_o25 = ev["c_o25"]
            c_u25 = ev["c_u25"]
            casa_1 = ev["casa"]
            casa_2 = ev["casa"]

            if c_1 <= 1.05 or c_2 <= 1.05:
                fuerza_rel = (bonus_l * 1.10) / max(0.78, bonus_v)
                p_est_1 = min(0.65, max(0.25, 0.44 * fuerza_rel))
                p_est_x = 0.27
                p_est_2 = max(0.16, 1.0 - p_est_1 - p_est_x)
                c_1 = round((1.0 / p_est_1) * 0.94, 2)
                c_x = round((1.0 / p_est_x) * 0.94, 2)
                c_2 = round((1.0 / p_est_2) * 0.94, 2)
                c_o25, c_u25 = 1.86, 1.90
                casa_1 = "Consenso Multi-Casa"
                casa_2 = "Consenso Multi-Casa"

            pin_1, pin_x, pin_2 = c_1, c_x, c_2
            mean_o25, mean_u25 = c_o25, c_u25

        overround = (1.0 / pin_1) + (1.0 / pin_x) + (1.0 / pin_2)
        p_devig_1 = (1.0 / pin_1) / overround
        p_devig_x = (1.0 / pin_x) / overround
        p_devig_2 = (1.0 / pin_2) / overround

        p_devig_o25, p_devig_u25 = 0.50, 0.50
        goles_mkt = 2.60
        if mean_o25 > 1.05 and mean_u25 > 1.05:
            ov_tot = (1.0 / mean_o25) + (1.0 / mean_u25)
            p_devig_o25 = (1.0 / mean_o25) / ov_tot
            p_devig_u25 = (1.0 / mean_u25) / ov_tot
            goles_mkt = 2.12 + (p_devig_o25 * 1.12)

        l_mkt_l = max(0.55, min(2.95, goles_mkt * (p_devig_1 + 0.48 * p_devig_x) * 1.04))
        l_mkt_v = max(0.45, min(2.65, goles_mkt * (p_devig_2 + 0.52 * p_devig_x) * 0.96))

        linea_f_tot = round(base_f) - 0.5
        linea_sot = round(base_s) - 0.5
        linea_tarj_over = 3.5

        if st_l and st_v:
            lambda_l = ((((st_l["gf"] + st_v["gc"]) / 2.0) * 0.50) + (l_mkt_l * 0.50)) * bonus_l
            lambda_v = ((((st_v["gf"] + st_l["gc"]) / 2.0) * 0.50) + (l_mkt_v * 0.50)) * bonus_v
            f_loc_esp = (st_l["f_com"] * 0.60) + (st_v["f_rec"] * 0.40)
            f_vis_esp = (st_v["f_com"] * 0.60) + (st_l["f_rec"] * 0.40)
            sot_l, sot_v = st_l["sot"] * bonus_l, st_v["sot"] * bonus_v
            tarj_l_real, tarj_v_real = st_l["tarj"], st_v["tarj"]
            tarj_esp = tarj_l_real + tarj_v_real
            cor_l_real, cor_v_real = st_l["cor"] * bonus_l, st_v["cor"] * bonus_v
            cor_esp = cor_l_real + cor_v_real
            f_l5 = st_l["f_l5"] + st_v["f_l5"]
            f_l20 = st_l["f_l20"] + st_v["f_l20"]
            str_ventanas = f"Faltas L5:{f_l5:.1f} · L20:{f_l20:.1f} | Córners L20:{cor_esp:.1f} | Tarj:{tarj_esp:.1f}"
            tiene_stats = True
        else:
            deseq = min(1.22, max(0.82, pin_2 / max(1.05, pin_1)))
            lambda_l = l_mkt_l * bonus_l
            lambda_v = l_mkt_v * bonus_v
            f_loc_esp = (base_f * 0.47) / (deseq ** 0.16)
            f_vis_esp = (base_f * 0.53) * (deseq ** 0.16)
            sot_l = (base_s * 0.54) * (deseq ** 0.14)
            sot_v = (base_s * 0.46) / (deseq ** 0.14)
            tarj_l_real, tarj_v_real = base_t * 0.48, base_t * 0.52
            tarj_esp = base_t
            cor_l_real, cor_v_real = (base_c * 0.53) * (deseq ** 0.12), (base_c * 0.47) / (deseq ** 0.12)
            cor_esp = cor_l_real + cor_v_real
            str_ventanas = f"xG Proy: {lambda_l:.2f}-{lambda_v:.2f} | Córners: {cor_esp:.1f} | SOT: {sot_l+sot_v:.1f}"
            tiene_stats = False

        res_mc = motor_montecarlo_v18(
            lambda_l, lambda_v, f_loc_esp, f_vis_esp, linea_f_tot,
            sot_l, sot_v, linea_sot, tarj_esp, linea_tarj_over,
            cor_l_real, cor_v_real, N_SIMS
        )

        p_fin_1 = (res_mc[0] * 0.45) + (p_devig_1 * 0.55)
        p_fin_x = (res_mc[1] * 0.45) + (p_devig_x * 0.55)
        p_fin_2 = (res_mc[2] * 0.45) + (p_devig_2 * 0.55)
        p_fin_1x = (res_mc[3] * 0.45) + ((p_devig_1 + p_devig_x) * 0.55)
        p_fin_x2 = (res_mc[4] * 0.45) + ((p_devig_2 + p_devig_x) * 0.55)
        p_fin_o25 = (res_mc[6] * 0.45) + (p_devig_o25 * 0.55)
        p_fin_u25 = (res_mc[7] * 0.45) + (p_devig_u25 * 0.55)
        top_scores_str = ", ".join(calcular_top3_marcadores_exactos(lambda_l, lambda_v))

        catalogo_partidos.append({
            "match_key": id_p,
            "event_id": ev_id,
            "liga": liga,
            "local": loc,
            "visita": vis,
            "etiqueta_dia": ev["etiqueta_dia"],
            "hora_peru": ev["hora_peru"],
            "xg_l": round(lambda_l, 2),
            "xg_v": round(lambda_v, 2),
            "cor_l": round(cor_l_real, 1),
            "cor_v": round(cor_v_real, 1),
            "cor_tot": round(cor_esp, 1),
            "sot_l": round(sot_l, 1),
            "sot_v": round(sot_v, 1),
            "sot_tot": round(sot_l + sot_v, 1),
            "falt_l": round(f_loc_esp, 1),
            "falt_v": round(f_vis_esp, 1),
            "falt_tot": round(f_loc_esp + f_vis_esp, 1),
            "tarj_l": round(tarj_l_real, 1),
            "tarj_v": round(tarj_v_real, 1),
            "tarj_tot": round(tarj_esp, 1),
            "form_l": form_l,
            "form_v": form_v,
            "top_scores": top_scores_str,
            "p_1": round(p_fin_1 * 100.0, 1),
            "p_x": round(p_fin_x * 100.0, 1),
            "p_2": round(p_fin_2 * 100.0, 1),
            "p_1x": round(p_fin_1x * 100.0, 1),
            "p_x2": round(p_fin_x2 * 100.0, 1),
            "p_o15": round(res_mc[5] * 100.0, 1),
            "p_o25": round(p_fin_o25 * 100.0, 1),
            "p_u35": round(res_mc[8] * 100.0, 1),
            "p_btts": round(res_mc[9] * 100.0, 1),
            "p_cor_o85": round(res_mc[16] * 100.0, 1),
            "p_cor_u105": round(res_mc[17] * 100.0, 1),
            "p_tarj_u45": round(res_mc[15] * 100.0, 1)
        })

        c_1x = round((1.0 / ((1.0 / c_1) + (1.0 / c_x))) * 1.02, 2)
        c_x2 = round((1.0 / ((1.0 / c_2) + (1.0 / c_x))) * 1.02, 2)

        mercados_eval = [
            (f"Victoria {loc} (1)", p_fin_1, c_1, casa_1, "1X2", p_fin_1 * 100.0),
            (f"Victoria {vis} (2)", p_fin_2, c_2, casa_2, "1X2", p_fin_2 * 100.0),
            (f"Doble Oportunidad {loc} o Empate (1X)", p_fin_1x, c_1x, casa_1, "DOBLE", p_fin_1x * 100.0),
            (f"Doble Oportunidad {vis} o Empate (X2)", p_fin_x2, c_x2, casa_2, "DOBLE", p_fin_x2 * 100.0),
        ]

        if c_o25 > 1.38:
            mercados_eval.append(("Más de 2.5 Goles (Over 2.5)", p_fin_o25, c_o25, casa_1, "GOLES", p_fin_o25 * 100.0))
        if c_u25 > 1.38:
            mercados_eval.append(("Menos de 2.5 Goles (Under 2.5)", p_fin_u25, c_u25, casa_1, "GOLES", p_fin_u25 * 100.0))
            c_un35 = max(1.36, round(c_u25 * 0.78, 2))
            p_fin_u35 = min(0.76, (res_mc[8] * 0.50) + (min(0.82, p_devig_u25 * 1.30) * 0.50))
            mercados_eval.append(("Menos de 3.5 Goles (Under 3.5)", p_fin_u35, c_un35, casa_1, "GOLES", p_fin_u35 * 100.0))

        if tiene_stats:
            if cor_esp >= 10.1:
                hr_co = calcular_hit_rate_combinado(st_l["arr_cor"], st_v["arr_cor"], 8.5, "OVER")
                p_cal_co = min(0.72, (res_mc[16] * 0.50) + ((hr_co / 100.0) * 0.50))
                if hr_co >= 65.0:
                    mercados_eval.append(("Más de 8.5 Córners", p_cal_co, 1.62, "Consenso Multi-Casa", "CORNERS", hr_co))
            elif cor_esp <= 9.1:
                hr_cu = calcular_hit_rate_combinado(st_l["arr_cor"], st_v["arr_cor"], 10.5, "UNDER")
                p_cal_cu = min(0.73, (res_mc[17] * 0.50) + ((hr_cu / 100.0) * 0.50))
                if hr_cu >= 65.0:
                    mercados_eval.append(("Menos de 10.5 Córners", p_cal_cu, 1.58, "Consenso Multi-Casa", "CORNERS", hr_cu))

            dif_f = (f_loc_esp + f_vis_esp) - linea_f_tot
            if dif_f <= -1.1:
                hr_fu = calcular_hit_rate_combinado(st_l["arr_f"], st_v["arr_f"], linea_f_tot, "UNDER")
                p_cal_fu = min(0.71, (res_mc[11] * 0.55) + ((hr_fu / 100.0) * 0.45))
                if hr_fu >= 65.0:
                    mercados_eval.append((f"Menos de {linea_f_tot} Faltas Totales", p_cal_fu, 1.66, "Consenso Multi-Casa", "FALTAS", hr_fu))
            elif dif_f >= 1.1:
                hr_fo = calcular_hit_rate_combinado(st_l["arr_f"], st_v["arr_f"], linea_f_tot, "OVER")
                p_cal_fo = min(0.71, (res_mc[10] * 0.55) + ((hr_fo / 100.0) * 0.45))
                if hr_fo >= 65.0:
                    mercados_eval.append((f"Más de {linea_f_tot} Faltas Totales", p_cal_fo, 1.66, "Consenso Multi-Casa", "FALTAS", hr_fo))

            dif_sot = (sot_l + sot_v) - linea_sot
            if dif_sot >= 0.85:
                hr_so = calcular_hit_rate_combinado(st_l["arr_sot"], st_v["arr_sot"], linea_sot, "OVER")
                p_cal_so = min(0.70, (res_mc[12] * 0.55) + ((hr_so / 100.0) * 0.45))
                if hr_so >= 65.0:
                    mercados_eval.append((f"Más de {linea_sot} Tiros al Arco", p_cal_so, 1.66, "Consenso Multi-Casa", "TIROS", hr_so))
            elif dif_sot <= -0.85:
                hr_su = calcular_hit_rate_combinado(st_l["arr_sot"], st_v["arr_sot"], linea_sot, "UNDER")
                p_cal_su = min(0.70, (res_mc[13] * 0.55) + ((hr_su / 100.0) * 0.45))
                if hr_su >= 65.0:
                    mercados_eval.append((f"Menos de {linea_sot} Tiros al Arco", p_cal_su, 1.65, "Consenso Multi-Casa", "TIROS", hr_su))

            if tarj_esp >= 4.35 and tarj_l_real >= 1.75 and tarj_v_real >= 1.75:
                hr_to = calcular_hit_rate_combinado(st_l["arr_tarj"], st_v["arr_tarj"], 3.5, "OVER")
                p_cal_to = min(0.68, (res_mc[14] * 0.50) + ((hr_to / 100.0) * 0.50))
                if hr_to >= 65.0:
                    mercados_eval.append(("Más de 3.5 Tarjetas", p_cal_to, 1.66, "Consenso Multi-Casa", "TARJETAS", hr_to))
            elif tarj_esp <= 3.85 and (tarj_l_real <= 1.65 or tarj_v_real <= 1.65):
                hr_tu = calcular_hit_rate_combinado(st_l["arr_tarj"], st_v["arr_tarj"], 4.5, "UNDER")
                p_cal_tu = min(0.72, (res_mc[15] * 0.45) + ((hr_tu / 100.0) * 0.55))
                if hr_tu >= 70.0:
                    mercados_eval.append(("Menos de 4.5 Tarjetas", p_cal_tu, 1.55, "Consenso Multi-Casa", "TARJETAS", hr_tu))

        for nom_m, p_real, cuota_m, casa_m, cat, hr_val in mercados_eval:
            if not (CUOTA_MIN <= cuota_m <= CUOTA_MAX):
                continue
            p_imp = 1.0 / cuota_m
            edge_pct = (p_real - p_imp) * 100.0
            if edge_pct < 1.8 or edge_pct > 12.0:
                continue

            prob_pct = p_real * 100.0
            tier_cod, tier_label, tier_rank = clasificar_tier_realista(prob_pct, edge_pct, cuota_m, hr_val)
            if tier_cod == "DESCARTADO":
                continue

            b = cuota_m - 1.0
            kelly = max(0.015, min(0.045, (((b * p_real) - (1.0 - p_real)) / b) * KELLY_FRAC))
            tope_soles = 4.50 if tier_cod == "TIER S" else 3.00
            stake_soles = min(tope_soles, max(1.50, round((bankroll_dinamico * kelly) * 2) / 2))
            confianza_ia = min(94.8, 80.0 + (edge_pct * 0.90) + max(0.0, (prob_pct - 55.0) * 0.30))
            cuota_justa = round(1.0 / max(0.05, p_real), 2)

            candidatos.append({
                "match_key": id_p,
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
                "hit_rate": round(hr_val, 0),
                "stake": stake_soles,
                "stake_pct": kelly,
                "tier_cod": tier_cod,
                "tier_label": tier_label,
                "tier_rank": tier_rank,
                "xg_l": round(lambda_l, 2),
                "xg_v": round(lambda_v, 2),
                "cor_proy": round(cor_esp, 1),
                "tarj_l": round(tarj_l_real, 2),
                "tarj_v": round(tarj_v_real, 2),
                "faltas_proy": round(f_loc_esp + f_vis_esp, 1),
                "sot_proy": round(sot_l + sot_v, 1),
                "top_scores": top_scores_str,
                "form_l": form_l,
                "form_v": form_v,
                "rec_l": rec_l,
                "rec_v": rec_v,
                "bajas": "Plantillas titulares disponibles",
                "ventanas": str_ventanas,
                "explicacion": f"Cuota mínima rentable (Sin Vig): @{cuota_justa:.2f}. En cualquier casa que pague más de @{cuota_justa:.2f} tienes ventaja matemática (+EV).",
                "estado": "PENDIENTE",
                "marcador_final": f"Juega {ev['etiqueta_dia']} {ev['hora_peru']}",
                "profit": 0.0,
                "score": (p_real * 0.60) + ((edge_pct / 100.0) * 0.40)
            })

    candidatos.sort(key=lambda x: (x["tier_rank"], -x["score"]))
    picks_finales = []
    partidos_usados = set()
    conteo_cat = {}

    for c in candidatos:
        clave_p = f"{c['local']}_{c['visita']}"
        cat = c["categoria"]
        if clave_p not in partidos_usados and conteo_cat.get(cat, 0) < 3 and len(picks_finales) < MAX_PICKS_WEB:
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

    generar_web_spa_v18(auditoria, picks_finales, catalogo_partidos, len(caches_csv))

    res = auditoria["resumen"]
    dur = time.time() - t0
    lineas_tg = [
        "📡 IABET QUANT PRO v18.0 (LIVE MATCH STATS CENTER)",
        f"📊 Efectividad: {res['win_rate_pct']:.1f}% ({res['ganados']}W-{res['perdidos']}L) | ROI: {res['roi_pct']:+.1f}%",
        f"💰 Bankroll: S/ {res['bankroll_actual']:.2f} | 🎫 Créditos OddsAPI: {creditos_restantes}",
        f"🌐 Live Tracker Web: https://blakeffx.github.io/iabet-cloud/",
        f"⏱️ {len(picks_finales)} picks con telemetría en vivo ({dur:.1f}s)",
        "━━━━━━━━━━━━━━━━━━━━"
    ]

    for idx, p in enumerate(picks_finales[:8], 1):
        lineas_tg.append(
            f"{idx}. {p['tier_label']} [📅 {p['etiqueta_dia']} {p['hora_peru']} · {p['liga']}]\n"
            f"⚽ {p['local']} vs {p['visita']}\n"
            f"👉 {p['mercado']}  @{p['cuota']:.2f} (Mín. Rentable: @{p['cuota_justa']:.2f})\n"
            f"✅ Prob. Calibrada: {p['prob']:.1f}% | Hit-Rate: {p['hit_rate']:.0f}% | Edge: +{p['edge']:.1f}%\n"
            f"📊 {p['ventanas']} | 💰 Stake: S/ {p['stake']:.2f}\n"
        )

    enviar_telegram_seguro("\n".join(lineas_tg))
    print(f"✅ IABET v18.0 completado: {len(picks_finales)} picks con Live Match Center publicados.")

if __name__ == "__main__":
    ejecutar_iabet_v18()
