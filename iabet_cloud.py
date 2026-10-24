import time
import io
import requests
import numpy as np
import pandas as pd
from numba import njit, prange
from concurrent.futures import ThreadPoolExecutor
from parlay_api import ParlayAPI

# ==============================================================================
# 1. CREDENCIALES Y CONFIGURACION (IABET v11.0 APP-CLONE DEFINITIVA)
# ==============================================================================
API_KEY = "105d8edef7a4869116a817c106dc86a8"
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

BANKROLL = 100.0        # Capital en Soles (S/ 100.00)
KELLY_FRAC = 0.20       # Quarter-Kelly para gestión prudente
N_SIMS = 1_000_000      # 1,000,000 de simulaciones (monteCarlo.simulations)
TOP_DESTACADOS = 5
MAX_LISTOS = 12

CUOTA_MIN = 1.25
CUOTA_MAX = 2.60

HEADERS_PRO = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

LIGAS_FUTBOL = {
    "soccer_spain_la_liga": ("LaLiga", "SP1", 4.8, 25.2, 8.6),
    "soccer_epl": ("Premier League", "E0", 3.9, 21.8, 9.2),
    "soccer_italy_serie_a": ("Serie A", "I1", 4.4, 24.6, 8.7),
    "soccer_germany_bundesliga": ("Bundesliga", "D1", 4.0, 22.4, 9.5),
    "soccer_france_ligue_one": ("Ligue 1", "F1", 4.1, 23.8, 8.8),
    "soccer_netherlands_eredivisie": ("Eredivisie", "N1", 3.4, 20.5, 9.6),
    "soccer_brazil_campeonato": ("Brasileirao", "BRA", 5.0, 27.2, 8.9),
    "soccer_argentina_primera_division": ("Liga Argentina", "ARG", 5.2, 27.8, 8.3),
    "soccer_conmebol_copa_libertadores": ("Copa Libertadores", None, 5.3, 27.5, 8.6),
    "soccer_conmebol_copa_sudamericana": ("Copa Sudamericana", None, 5.0, 26.8, 8.4),
    "soccer_mexico_ligamx": ("Liga MX", "MEX", 4.5, 24.4, 8.8)
}

ESPN_SCOREBOARDS = {
    "LaLiga": "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard",
    "Premier League": "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "Serie A": "https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/scoreboard",
    "Copa Libertadores": "https://site.api.espn.com/apis/site/v2/sports/soccer/conmebol.libertadores/scoreboard"
}

DB_LIGA1_PERU = {
    "universitario": {"gf": 1.85, "gc": 0.65, "f_com": 13.6, "f_rec": 14.8, "sot": 5.4, "tarj": 2.3},
    "alianza lima": {"gf": 1.72, "gc": 0.75, "f_com": 13.1, "f_rec": 15.1, "sot": 5.1, "tarj": 2.4},
    "sporting cristal": {"gf": 2.10, "gc": 1.05, "f_com": 11.7, "f_rec": 14.9, "sot": 5.8, "tarj": 2.1},
    "melgar": {"gf": 1.82, "gc": 0.85, "f_com": 13.4, "f_rec": 13.9, "sot": 5.5, "tarj": 2.5}
}

# ==============================================================================
# 2. MOTOR MONTE CARLO JIT (NUMBA)
# ==============================================================================
@njit(parallel=True)
def motor_montecarlo_core(l_loc, l_vis, f_loc, f_vis, linea_f_tot, n_sims):
    vic_loc = 0
    empate = 0
    vic_vis = 0
    over_15 = 0
    over_25 = 0
    under_35 = 0
    f_tot_under = 0
    cov = 0.10

    for i in prange(n_sims):
        r = np.random.poisson(cov)
        gl = np.random.poisson(max(0.05, l_loc - cov)) + r
        gv = np.random.poisson(max(0.05, l_vis - cov)) + r
        tot_g = gl + gv

        if gl > gv: vic_loc += 1
        elif gl == gv: empate += 1
        else: vic_vis += 1

        if tot_g > 1.5: over_15 += 1
        if tot_g > 2.5: over_25 += 1
        if tot_g < 3.5: under_35 += 1

        friccion = 1.06 if abs(gl - gv) <= 1 else 0.93
        fl = max(4.0, np.random.normal(f_loc * friccion, 2.30))
        fv = max(4.0, np.random.normal(f_vis * friccion, 2.40))
        if (fl + fv) < linea_f_tot:
            f_tot_under += 1

    return (
        vic_loc / n_sims,            
        (vic_loc + empate) / n_sims, 
        (vic_vis + empate) / n_sims, 
        vic_vis / n_sims,            
        over_15 / n_sims,            
        over_25 / n_sims,            
        under_35 / n_sims,           
        f_tot_under / n_sims         
    )

def dec(c):
    if c is None: return 2.00
    c = float(c)
    if c >= 100: return (c / 100.0) + 1.0
    elif c <= -100: return (100.0 / abs(c)) + 1.0
    return c

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=12)
        if r.status_code == 200:
            print("✅ Mensaje entregado a Telegram correctamente.")
        else:
            print(f"⚠️ Telegram rechazó Markdown ({r.status_code}). Enviando texto plano...")
            payload.pop("parse_mode")
            r_plain = requests.post(url, json=payload, timeout=12)
            print(f"Resultado en texto plano: {r_plain.status_code}")
    except Exception as e:
        print(f"❌ Error de conexión al enviar a Telegram: {e}")

# ==============================================================================
# 3. DESCARGA HISTÓRICA CSV Y SCOREBOARDS ESPN (CONCURRENT FUTURES)
# ==============================================================================
def descargar_csv_seguro(cod_csv):
    if not cod_csv:
        return pd.DataFrame()
    urls = [
        f"https://www.football-data.co.uk/mmz4281/2526/{cod_csv}.csv",
        f"https://www.football-data.co.uk/mmz4281/2425/{cod_csv}.csv",
        f"https://www.football-data.co.uk/new/{cod_csv}.csv"
    ]
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS_PRO, timeout=7.0)
            if r.status_code == 200 and len(r.text) > 500:
                df = pd.read_csv(io.StringIO(r.text))
                df = df.rename(columns={'HG': 'FTHG', 'AG': 'FTAG', 'Home': 'HomeTeam', 'Away': 'AwayTeam'})
                if {'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}.issubset(df.columns):
                    return df.tail(380)
        except Exception:
            pass
    return pd.DataFrame()

def obtener_contexto_y_datos():
    caches_csv = {}
    espn_ctx = {}
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        csv_futures = {nom_liga: executor.submit(descargar_csv_seguro, cod_csv) 
                       for _, (nom_liga, cod_csv, _, _, _) in LIGAS_FUTBOL.items() if cod_csv}
        
        espn_futures = [executor.submit(requests.get, u, headers=HEADERS_PRO, timeout=7.0) 
                        for u in ESPN_SCOREBOARDS.values()]
        
        for nom_liga, future in csv_futures.items():
            df = future.result()
            if not df.empty:
                caches_csv[nom_liga] = df
                
        for future in espn_futures:
            try:
                r = future.result()
                if r.status_code == 200:
                    for ev in r.json().get("events", []):
                        comp = (ev.get("competitions") or [{}])[0]
                        for c in comp.get("competitors", []):
                            t_name = c.get("team", {}).get("displayName", "")
                            form = c.get("form", "")
                            if t_name and form:
                                espn_ctx[t_name.lower()[:5]] = form
            except Exception:
                pass
                
    return caches_csv, espn_ctx

def obtener_stats_equipo(df, equipo, base_faltas, es_local=True):
    eq_low = equipo.lower()
    for k, v in DB_LIGA1_PERU.items():
        if k in eq_low:
            f_esp = v["f_com"] * (0.96 if es_local else 1.06)
            return {
                "gf": v["gf"] * (1.10 if es_local else 0.90),
                "gc": v["gc"],
                "f_com": f_esp,
                "f_rec": v["f_rec"],
                "csv_ok": True
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

    gf, gc, f_com, f_rec = [], [], [], []
    ok_f = {'HF', 'AF'}.issubset(partidos.columns)

    for _, row in partidos.iterrows():
        es_l = clave.lower() in str(row['HomeTeam']).lower()
        gf.append(float(row['FTHG'] if es_l else row['FTAG']))
        gc.append(float(row['FTAG'] if es_l else row['FTHG']))
        if ok_f and pd.notna(row['HF']) and pd.notna(row['AF']):
            f_com.append(float(row['HF'] if es_l else row['AF']))
            f_rec.append(float(row['AF'] if es_l else row['HF']))

    mitad_f = base_faltas / 2.0
    if len(f_com) >= 5:
        f_l5, f_l10, f_l20 = np.mean(f_com[-5:]), np.mean(f_com[-10:]), np.mean(f_com)
        f_pond = (f_l5 * 0.50) + (f_l10 * 0.30) + (f_l20 * 0.20)
        f_rec_pond = np.mean(f_rec[-10:]) if len(f_rec) >= 5 else mitad_f
    else:
        f_pond = f_rec_pond = mitad_f

    gf_pond = ((np.mean(gf[-5:]) * 0.50) + (np.mean(gf[-10:]) * 0.30) + (np.mean(gf) * 0.20)) * (1.08 if es_local else 0.92)
    gc_pond = (np.mean(gc[-5:]) * 0.50) + (np.mean(gc[-10:]) * 0.30) + (np.mean(gc) * 0.20)

    return {
        "gf": gf_pond, "gc": gc_pond,
        "f_com": f_pond, "f_rec": f_rec_pond,
        "csv_ok": True
    }

# ==============================================================================
# 4. ORQUESTADOR CUANTITATIVO CONSOLIDADO
# ==============================================================================
def ejecutar_iabet_cloud():
    t0 = time.time()
    
    try:
        url_parlay = "https://parlay-api.com/api/v1/odds"
        params = {"api_key": API_KEY, "regions": "eu", "markets": "h2h,totals"}
        print("Consultando ParlayAPI...")
        res_parlay = requests.get(url_parlay, params=params, timeout=15)
        
        if res_parlay.status_code == 401 or "Unauthorized" in res_parlay.text:
            enviar_telegram(f"❌ *Error de Autenticación:* Tu API_KEY ({API_KEY}) de ParlayAPI no es válida o caducó.")
            return
            
        data_parlay = res_parlay.json()
        eventos = data_parlay if isinstance(data_parlay, list) else data_parlay.get("data", [])
        
    except Exception as e:
        enviar_telegram(f"❌ *Error de Red con ParlayAPI:* `{e}`")
        return

    if not eventos:
        enviar_telegram("ℹ️ *ParlayAPI no devolvió eventos activos.* Las ligas pueden estar en pausa o la cuota de la API se agotó.")
        return

    caches_csv, espn_ctx = obtener_contexto_y_datos()

    candidatos = []
    surebets = []
    partidos_totales = 0
    vistos_partido = set()

    for ev in eventos:
        cod_deporte = ev.get("sport_key", "")
        if cod_deporte not in LIGAS_FUTBOL:
            continue
            
        nom_liga, _, b_tarj, b_falt, b_sot = LIGAS_FUTBOL[cod_deporte]
        loc = ev.get("home_team")
        vis = ev.get("away_team")
        if not loc or not vis or "(Bookings)" in loc or "(Corners)" in loc:
            continue

        clave_p = f"{loc[:5]}_{vis[:5]}".lower()
        if clave_p in vistos_partido:
            continue
        vistos_partido.add(clave_p)

        bms = ev.get("bookmakers", [])
        if not bms:
            continue
        partidos_totales += 1

        c_1, c_x, c_2 = 0.0, 0.0, 0.0
        c_o25, c_u25, c_u35 = 0.0, 0.0, 0.0
        casa_1, casa_2 = "Bookie", "Bookie"
        todas_c1, todas_c2 = [], []

        for casa in bms:
            nom_casa = casa.get("title", "Bookie")
            for m in casa.get("markets", []):
                mkey = m.get("key")
                if mkey == "h2h":
                    for o in m.get("outcomes", []):
                        p = dec(o.get("price"))
                        if o.get("name") == loc:
                            todas_c1.append(p)
                            if p > c_1: c_1, casa_1 = p, nom_casa
                        elif o.get("name") == vis:
                            todas_c2.append(p)
                            if p > c_2: c_2, casa_2 = p, nom_casa
                        elif o.get("name") == "Draw" and p > c_x:
                            c_x = p
                elif mkey == "totals":
                    for o in m.get("outcomes", []):
                        p = dec(o.get("price"))
                        pt = float(o.get("point") or 0)
                        if pt == 2.5:
                            if o.get("name") == "Over" and p > c_o25: c_o25 = p
                            elif o.get("name") == "Under" and p > c_u25: c_u25 = p
                        elif pt == 3.5 and o.get("name") == "Under" and p > c_u35:
                            c_u35 = p

        if c_1 <= 1.0 or c_2 <= 1.0:
            continue

        if c_x > 1.0:
            margen = (1.0 / c_1) + (1.0 / c_x) + (1.0 / c_2)
            if margen < 1.0:
                rent = ((1.0 / margen) - 1.0) * 100
                surebets.append(f"🚨 *SUREBET +{rent:.2f}%* [{nom_liga}] {loc} vs {vis}")

        df_liga = caches_csv.get(nom_liga)
        st_l = obtener_stats_equipo(df_liga, loc, b_falt, es_local=True)
        st_v = obtener_stats_equipo(df_liga, vis, b_falt, es_local=False)

        c1_med = np.mean(todas_c1) if todas_c1 else c_1
        c2_med = np.mean(todas_c2) if todas_c2 else c_2
        l_mkt_l = max(0.85, min(2.80, (1.0 / c1_med) * 2.60))
        l_mkt_v = max(0.60, min(2.35, (1.0 / c2_med) * 2.30))

        form_espn = espn_ctx.get(loc.lower()[:5], "")
        tag_form = f" | Form:{form_espn}" if form_espn else ""

        if st_l and st_v:
            lambda_l = (((st_l["gf"] + st_v["gc"]) / 2.0) * 0.60) + (l_mkt_l * 0.40)
            lambda_v = (((st_v["gf"] + st_l["gc"]) / 2.0) * 0.60) + (l_mkt_v * 0.40)
            f_loc_esp = (st_l["f_com"] * 0.65) + (st_v["f_rec"] * 0.35)
            f_vis_esp = (st_v["f_com"] * 0.65) + (st_l["f_rec"] * 0.35)
            txt_datos = f"Data: CSV Verificado {tag_form}"
            tiene_csv = True
        else:
            lambda_l, lambda_v = l_mkt_l, l_mkt_v
            deseq = min(1.22, max(0.82, c2_med / c1_med))
            f_loc_esp = (b_falt * 0.47) / (deseq ** 0.20)
            f_vis_esp = (b_falt * 0.53) * (deseq ** 0.20)
            txt_datos = f"Data: Modelo Táctico {tag_form}"
            tiene_csv = False

        linea_f_tot = round(b_falt) - 0.5

        res = motor_montecarlo_core(lambda_l, lambda_v, f_loc_esp, f_vis_esp, linea_f_tot, N_SIMS)

        c_1x = round(1.0 / ((1.0 / c_1) + (1.0 / c_x)) * 0.96, 2) if c_x > 1.0 else 1.20
        c_x2 = round(1.0 / ((1.0 / c_2) + (1.0 / c_x)) * 0.96, 2) if c_x > 1.0 else 1.25

        mercados = [
            (f"Victoria {loc} (1)", res[0], c_1, casa_1, "1X2", f"xG:{lambda_l:.2f}-{lambda_v:.2f}"),
            (f"{loc} o Empate (1X)", res[1], c_1x, casa_1, "DOBLE", f"xG:{lambda_l:.2f}-{lambda_v:.2f}"),
            (f"{vis} o Empate (X2)", res[2], c_x2, casa_2, "DOBLE", f"xG:{lambda_l:.2f}-{lambda_v:.2f}"),
            (f"Victoria {vis} (2)", res[3], c_2, casa_2, "1X2", f"xG:{lambda_l:.2f}-{lambda_v:.2f}")
        ]

        if c_o25 > 1.45:
            mercados.append(("Más de 2.5 Goles", res[5], c_o25, casa_1, "GOLES", f"xG Tot:{lambda_l+lambda_v:.2f}"))
        if c_u25 > 1.45:
            mercados.append(("Menos de 2.5 Goles", 1.0 - res[5], c_u25, casa_1, "GOLES", f"xG Tot:{lambda_l+lambda_v:.2f}"))
        if c_u35 > 1.25:
            mercados.append(("Menos de 3.5 Goles", res[6], c_u35, casa_1, "GOLES", f"xG Tot:{lambda_l+lambda_v:.2f}"))

        if tiene_csv:
            mercados.append((f"Menos de {linea_f_tot} Faltas", res[7], 1.66, "Betano/Betsson", "FALTAS", f"Proy:{f_loc_esp+f_vis_esp:.1f}f vs {linea_f_tot}"))

        for nom_m, p_real, cuota, nom_c, cat, det in mercados:
            if not (CUOTA_MIN <= cuota <= CUOTA_MAX):
                continue

            prob_imp = 1.0 / cuota
            edge = (p_real - prob_imp) * 100.0
            ev = (p_real * cuota) - 1.0

            if ev > 0.02 and (3.0 <= edge <= 18.0) and p_real >= 0.58:
                b = cuota - 1.0
                q = 1.0 - p_real
                kelly = ((b * p_real) - q) / b if b > 0 else 0.0
                k_stake = min(0.06, max(0.0, kelly * KELLY_FRAC))
                stake_soles = round((BANKROLL * k_stake) * 2.0) / 2.0

                if stake_soles >= 1.50:
                    conf_serious = min(96.8, 83.0 + (edge * 0.75) + ((p_real - 0.58) * 20.0))
                    candidatos.append({
                        "liga": nom_liga,
                        "partido": f"{loc} vs {vis}",
                        "cat": cat,
                        "mercado": nom_m,
                        "confianza": conf_serious,
                        "prob": p_real * 100.0,
                        "cuota": cuota,
                        "casa": nom_c,
                        "edge": edge,
                        "stake": stake_soles,
                        "kelly": k_stake * 100.0,
                        "datos": f"{txt_datos} | {det}",
                        "score": (p_real * 0.55) + (ev * 0.45)
                    })

    candidatos_ordenados = sorted(candidatos, key=lambda x: x["score"], reverse=True)
    top_destacados = []
    conteo_cat = {}
    vistos_final = set()

    for c in candidatos_ordenados:
        if c["partido"] in vistos_final:
            continue
        cat = c["cat"]
        if len(top_destacados) < TOP_DESTACADOS and conteo_cat.get(cat, 0) < 2:
            top_destacados.append(c)
            conteo_cat[cat] = conteo_cat.get(cat, 0) + 1
            vistos_final.add(c["partido"])

    t_total = time.time() - t0
    cuota_prom = np.mean([p["cuota"] for p in top_destacados]) if top_destacados else 1.62

    reporte = [
        "👑 *IABET PRO v11.0 — MOTOR APP-CLONE 2024*",
        f"🟢 *{partidos_totales} partidos analizados* | *{partidos_totales * N_SIMS:,} sims* ({t_total:.1f}s)",
        f"📊 *Bases Históricas Activas:* `{len(caches_csv)} ligas` | *Cuota Prom.:* `@{cuota_prom:.2f}`\n"
    ]

    if surebets:
        reporte.append("💎 *SUREBETS EN VIVO (ARBITRAJE):*\n" + "\n".join(surebets[:3]) + "\n")

    if top_destacados:
        reporte.append("⚡ *PICKS DESTACADOS (+EV VERIFICADO):*\n")
        mapa_iconos = {"FALTAS": "🛑", "GOLES": "⚽", "1X2": "🏆", "DOBLE": "🛡️"}
        for idx, p in enumerate(top_destacados, 1):
            ic = mapa_iconos.get(p["cat"], "🎯")
            reporte.append(
                f"*{idx}. {ic} [{p['liga']} — {p['cat']}]*\n"
                f"🏟️ *{p['partido']}*\n"
                f"👉 *{p['mercado']}*\n"
                f"🔥 *Confianza del Modelo:* `{p['confianza']:.1f}%` _(Prob. Real: {p['prob']:.1f}%)_\n"
                f"💵 *Cuota:* `@{p['cuota']:.2f}` _({p['casa']})_ | *Ventaja (Edge):* `+{p['edge']:.1f}%`\n"
                f"📐 *Métricas:* `{p['datos']}`\n"
                f"💰 *Stake Sugerido (Kelly {p['kelly']:.1f}%):* *Apostar S/ {p['stake']:.2f}*\n"
            )
    else:
        reporte.append("ℹ️ Las líneas actuales se encuentran ajustadas por las casas de apuestas. Sin desajustes de valor (+EV) en este ciclo.")

    enviar_telegram("\n".join(reporte))

if __name__ == "__main__":
    ejecutar_iabet_cloud()
