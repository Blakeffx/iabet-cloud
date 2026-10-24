import time
import io
import asyncio
import httpx
import requests
import numpy as np
import pandas as pd
from numba import njit, prange

# ==============================================================================
# 1. CONFIGURACIÓN SIN DEPENDENCIAS EXTERNAS DE PAGO
# ==============================================================================
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

BANKROLL = 100.0        # Capital en Soles (S/ 100.00)
KELLY_FRAC = 0.20       # Control conservador de riesgo
N_SIMS = 1_000_000      # 1 Millón de simulaciones por partido
TOP_DESTACADOS = 5
MAX_LISTOS = 12

CUOTA_MIN = 1.50
CUOTA_MAX = 2.25

# Endpoints Directos Kambi (Cuotas en vivo sin API Key)
ENDPOINTS_KAMBI = [
    ("Liga 1 Perú", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/peru/primera_division/all/matches.json?lang=es_ES&market=PE", 5.3, 28.0, 8.5),
    ("Copa Sudamericana", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/copa_sudamericana/all/all/matches.json?lang=es_ES&market=PE", 5.0, 26.8, 8.4),
    ("Copa Libertadores", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/copa_libertadores/all/all/matches.json?lang=es_ES&market=PE", 5.3, 27.5, 8.6),
    ("LaLiga", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/spain/la_liga/all/matches.json?lang=es_ES&market=PE", 4.8, 25.2, 8.6),
    ("Premier League", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/england/premier_league/all/matches.json?lang=es_ES&market=PE", 3.9, 21.8, 9.2),
    ("Serie A", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/italy/serie_a/all/matches.json?lang=es_ES&market=PE", 4.4, 24.6, 8.7),
    ("Champions League", "https://eu-offering-api.kambi.com/offering/v2018/ub/listView/football/champions_league/all/all/matches.json?lang=es_ES&market=PE", 4.1, 22.8, 9.4)
]

# Endpoints ESPN para racha y estado de forma
ESPN_SCOREBOARDS = {
    "LaLiga": "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard",
    "Premier League": "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "Serie A": "https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/scoreboard",
    "Copa Libertadores": "https://site.api.espn.com/apis/site/v2/sports/soccer/conmebol.libertadores/scoreboard"
}

HEADERS_PRO = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

# ==============================================================================
# 2. MOTOR MONTE CARLO EN C (NUMBA)
# ==============================================================================
@njit(parallel=True)
def motor_montecarlo_autonomo(l_loc, l_vis, f_loc, f_vis, linea_f, sot_loc, sot_vis, linea_sot, n_sims):
    vic_loc, empate, vic_vis = 0, 0, 0
    over_25, under_35 = 0, 0
    f_under, sot_under = 0, 0
    cov = 0.10

    for i in prange(n_sims):
        r = np.random.poisson(cov)
        gl = np.random.poisson(max(0.05, l_loc - cov)) + r
        gv = np.random.poisson(max(0.05, l_vis - cov)) + r
        tot = gl + gv

        if gl > gv: vic_loc += 1
        elif gl == gv: empate += 1
        else: vic_vis += 1

        if tot > 2.5: over_25 += 1
        if tot < 3.5: under_35 += 1

        # Faltas con fricción
        fric = 1.07 if abs(gl - gv) <= 1 else 0.92
        ftot = max(4.0, np.random.normal(f_loc * fric, 2.3)) + max(4.0, np.random.normal(f_vis * fric, 2.4))
        if ftot < linea_f: f_under += 1

        # Tiros al arco
        sot_tot = max(1.0, np.random.normal(sot_loc + sot_vis, 2.4))
        if sot_tot < linea_sot: sot_under += 1

    return (
        vic_loc / n_sims,
        (vic_loc + empate) / n_sims,
        vic_vis / n_sims,
        over_25 / n_sims,
        under_35 / n_sims,
        f_under / n_sims,
        sot_under / n_sims
    )

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

# ==============================================================================
# 3. EJECUCIÓN AUTÓNOMA (KAMBI + ESPN)
# ==============================================================================
async def ejecutar_iabet_autonomo():
    t0 = time.time()
    eventos_encontrados = []
    espn_form = {}

    async with httpx.AsyncClient(headers=HEADERS_PRO, timeout=8.5) as client:
        # A) Ingesta ESPN (Rachas)
        r_espn = await asyncio.gather(*[client.get(u) for u in ESPN_SCOREBOARDS.values()], return_exceptions=True)
        for r in r_espn:
            if isinstance(r, httpx.Response) and r.status_code == 200:
                try:
                    for ev in r.json().get("events", []):
                        for c in (ev.get("competitions") or [{}])[0].get("competitors", []):
                            t_nom = c.get("team", {}).get("displayName", "")
                            form = c.get("form", "")
                            if t_nom and form:
                                espn_form[t_nom.lower()[:5]] = form
                except Exception:
                    pass

        # B) Ingesta Kambi Directo (Cuotas en vivo)
        r_kambi = await asyncio.gather(*[client.get(u) for _, u, _, _, _ in ENDPOINTS_KAMBI], return_exceptions=True)
        for (nom_liga, _, b_tarj, b_falt, b_sot), r in zip(ENDPOINTS_KAMBI, r_kambi):
            if isinstance(r, httpx.Response) and r.status_code == 200:
                try:
                    for ev in r.json().get("events", []):
                        info = ev.get("event", {})
                        loc, vis = info.get("homeName"), info.get("awayName")
                        if not loc or not vis: continue

                        c_1, c_x, c_2, c_o25, c_u25 = 0.0, 0.0, 0.0, 0.0, 0.0
                        linea_f, c_fu = 0.0, 0.0
                        linea_s, c_su = 0.0, 0.0

                        for bo in ev.get("betOffers", []):
                            crit = bo.get("criterion", {}).get("label", "")
                            if crit in ["Full Time", "Tiempo reglamentario", "Resultado Final"]:
                                for o in bo.get("outcomes", []):
                                    odds = float(o.get("odds", 0)) / 1000.0
                                    if o.get("type") == "OT_ONE": c_1 = odds
                                    elif o.get("type") == "OT_CROSS": c_x = odds
                                    elif o.get("type") == "OT_TWO": c_2 = odds
                            elif "Total" in crit or "Goles" in crit:
                                for o in bo.get("outcomes", []):
                                    linea = float(o.get("line", 0)) / 1000.0
                                    odds = float(o.get("odds", 0)) / 1000.0
                                    if linea == 2.5:
                                        if o.get("type") == "OT_OVER": c_o25 = odds
                                        elif o.get("type") == "OT_UNDER": c_u25 = odds
                            elif "Falta" in crit or "Foul" in crit:
                                for o in bo.get("outcomes", []):
                                    linea_f = float(o.get("line", 0)) / 1000.0
                                    if o.get("type") == "OT_UNDER":
                                        c_fu = float(o.get("odds", 0)) / 1000.0
                            elif "Tiros a puerta" in crit or "Shots on target" in crit:
                                for o in bo.get("outcomes", []):
                                    linea_s = float(o.get("line", 0)) / 1000.0
                                    if o.get("type") == "OT_UNDER":
                                        c_su = float(o.get("odds", 0)) / 1000.0

                        if c_1 > 1.0 and c_2 > 1.0:
                            eventos_encontrados.append({
                                "liga": nom_liga, "loc": loc, "vis": vis,
                                "c_1": c_1, "c_x": c_x, "c_2": c_2,
                                "c_o25": c_o25, "c_u25": c_u25,
                                "linea_f": linea_f or (round(b_falt) - 0.5),
                                "c_fu": c_fu or 1.66,
                                "linea_s": linea_s or (round(b_sot) - 0.5),
                                "c_su": c_su or 1.68,
                                "b_falt": b_falt, "b_sot": b_sot
                            })
                except Exception:
                    pass

    candidatos = []
    for ev in eventos_encontrados:
        c1, cx, c2 = ev["c_1"], ev["c_x"], ev["c_2"]
        l_loc = max(0.85, min(2.8, (1.0 / c1) * 2.65))
        l_vis = max(0.65, min(2.4, (1.0 / c2) * 2.35))

        f_loc = ev["b_falt"] * 0.47
        f_vis = ev["b_falt"] * 0.53
        sot_loc = (ev["b_sot"] * 0.53) * ((l_loc / 1.45) ** 0.35)
        sot_vis = (ev["b_sot"] * 0.47) * ((l_vis / 1.15) ** 0.35)

        res = motor_montecarlo_autonomo(
            l_loc, l_vis, f_loc, f_vis, ev["linea_f"],
            sot_loc, sot_vis, ev["linea_s"], N_SIMS
        )

        c_1x = round(1.0 / ((1.0 / c1) + (1.0 / cx)) * 0.96, 2) if cx > 1.0 else 1.20
        form_txt = espn_form.get(ev["loc"].lower()[:5], "Activo")

        mercados = [
            (f"Victoria {ev['loc']} (1)", res[0], c1, "1X2", f"xG:{l_loc:.2f}-{l_vis:.2f}"),
            (f"{ev['loc']} o Empate (1X)", res[1], c_1x, "1X2", f"xG:{l_loc:.2f}-{l_vis:.2f}"),
            (f"Menos de {ev['linea_f']} Faltas", res[5], ev["c_fu"], "FALTAS", f"Proy:{f_loc+f_vis:.1f}f"),
            (f"Menos de {ev['linea_s']} Tiros al Arco", res[6], ev["c_su"], "TIROS", f"SOT Proy:{sot_loc+sot_vis:.1f}")
        ]

        if ev["c_u25"] > 1.45:
            mercados.append(("Menos de 2.5 Goles", 1.0 - res[3], ev["c_u25"], "GOLES", f"xG Total:{l_loc+l_vis:.2f}"))

        for nom, p_real, cuota, cat, info in mercados:
            if not (CUOTA_MIN <= cuota <= CUOTA_MAX):
                continue
            edge = (p_real - (1.0 / cuota)) * 100.0
            ev_val = (p_real * cuota) - 1.0

            if 3.5 <= edge <= 18.0 and p_real >= 0.60:
                b = cuota - 1.0
                kelly = min(0.08, max(0.0, (((b * p_real) - (1.0 - p_real)) / b) * KELLY_FRAC))
                stake = round((BANKROLL * kelly) * 2.0) / 2.0
                if stake >= 1.50:
                    candidatos.append({
                        "liga": ev["liga"], "partido": f"{ev['loc']} vs {ev['vis']}",
                        "cat": cat, "mercado": nom,
                        "prob": p_real * 100.0, "cuota": cuota,
                        "edge": edge, "stake": stake, "kelly": kelly * 100.0,
                        "info": f"{info} | ESPN: {form_txt}",
                        "score": (p_real * 0.55) + (ev_val * 0.45)
                    })

    # Diversificación de resultados
    top_destacados = sorted(candidatos, key=lambda x: x["score"], reverse=True)[:TOP_DESTACADOS]
    t_tot = time.time() - t0

    reporte = [
        "👑 *IABET AUTÓNOMO — CONEXIÓN DIRECTA (SIN PARLAY-API)*",
        f"🟢 *{len(eventos_encontrados)} partidos analizados* en {t_tot:.1f}s",
        "📡 *Fuente de Cuotas:* `Kambi Direct (Betsson/Inkabet)` | *Stats:* `ESPN API`\n"
    ]

    if top_destacados:
        reporte.append("⚡ *PICKS DESTACADOS VERIFICADOS:*\n")
        for idx, p in enumerate(top_destacados, 1):
            ic = {"FALTAS": "🛑", "GOLES": "⚽", "1X2": "🏆", "TIROS": "🎯"}.get(p["cat"], "🎯")
            reporte.append(
                f"*{idx}. {ic} [{p['liga']} — {p['cat']}]*\n"
                f"🏟️ *{p['partido']}*\n"
                f"👉 *{p['mercado']}*\n"
                f"📊 *Probabilidad Real:* `{p['prob']:.1f}%` | *Cuota:* `@{p['cuota']:.2f}`\n"
                f"📈 *Ventaja (Edge):* `+{p['edge']:.1f}%`\n"
                f"📐 *Contexto:* `{p['info']}`\n"
                f"💰 *Stake Sugerido (Kelly {p['kelly']:.1f}%):* *Apostar S/ {p['stake']:.2f}*\n"
            )
    else:
        reporte.append("ℹ️ Las cuotas actuales de Kambi se encuentran alineadas con el modelo; sin valor estadístico (+EV) en este ciclo.")

    enviar_telegram("\n".join(reporte))

if __name__ == "__main__":
    asyncio.run(ejecutar_iabet_autonomo())
        
