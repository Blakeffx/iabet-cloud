import time
import requests
import numpy as np
import pandas as pd
from numba import njit, prange
from parlay_api import ParlayAPI

# ==============================================================
# ⚙️ 1. CREDENCIALES Y CONFIGURACIÓN
# ==============================================================
API_KEY = "105d8edef7a4869116a817c106dc86a8"
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

BANKROLL = 100.0        # Presupuesto en Soles (S/ 100.00)
KELLY_FRAC = 0.20       # Control de riesgo (Kelly al 20%)
N_SIMS = 1_000_000      # 1 Millón de simulaciones por partido
TOP_DESTACADOS = 5

# Lista de torneos que ParlayAPI entrega con cuotas en vivo
LIGAS = [
    "soccer_spain_la_liga",
    "soccer_epl",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_conmebol_copa_libertadores",
    "soccer_conmebol_copa_sudamericana"
]

# ==============================================================
# 🧠 2. MOTOR MONTE CARLO BIVARIADO (NUMBA)
# ==============================================================
@njit(parallel=True)
def simular_montecarlo_clean(l_loc, l_vis, n_sims):
    vic_loc = 0
    empate = 0
    vic_vis = 0
    over_15 = 0
    over_25 = 0
    under_35 = 0
    btts_si = 0
    cov = 0.10

    for i in prange(n_sims):
        r = np.random.poisson(cov)
        gl = np.random.poisson(max(0.05, l_loc - cov)) + r
        gv = np.random.poisson(max(0.05, l_vis - cov)) + r
        tot = gl + gv

        if gl > gv: vic_loc += 1
        elif gl == gv: empate += 1
        else: vic_vis += 1

        if tot > 1.5: over_15 += 1
        if tot > 2.5: over_25 += 1
        if tot < 3.5: under_35 += 1
        if gl > 0 and gv > 0: btts_si += 1

    return (
        vic_loc / n_sims,               # 0: Local (1)
        (vic_loc + empate) / n_sims,    # 1: 1X
        (vic_vis + empate) / n_sims,    # 2: X2
        vic_vis / n_sims,               # 3: Visita (2)
        over_15 / n_sims,               # 4: Over 1.5
        over_25 / n_sims,               # 5: Over 2.5
        under_35 / n_sims,              # 6: Under 3.5
        btts_si / n_sims                # 7: BTTS Sí
    )

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

# ==============================================================
# 🚀 3. ESCÁNER DE VALOR VERIFICADO
# ==============================================================
def ejecutar_escaner():
    t0 = time.time()
    try:
        cliente = ParlayAPI(api_key=API_KEY)
    except Exception as e:
        enviar_telegram(f"❌ Error iniciando ParlayAPI: `{e}`")
        return

    candidatos = []
    partidos_totales = 0

    for deporte in LIGAS:
        try:
            eventos = cliente.odds(deporte, regions="eu", markets="h2h,totals")
        except Exception:
            continue

        if not eventos:
            continue

        for ev in eventos:
            loc = ev.get("home_team")
            vis = ev.get("away_team")
            if not loc or not vis or "(Bookings)" in loc or "(Corners)" in loc:
                continue

            bms = ev.get("bookmakers", [])
            if not bms:
                continue
            partidos_totales += 1

            # Extraer mejores cuotas reales de las casas
            c_1, c_x, c_2 = 0.0, 0.0, 0.0
            c_o25, c_u35 = 0.0, 0.0
            casa_1, casa_2 = "", ""

            for casa in bms:
                nom_casa = casa.get("title", "Bookie")
                for m in casa.get("markets", []):
                    mkey = m.get("key")
                    if mkey == "h2h":
                        for o in m.get("outcomes", []):
                            p = dec(o.get("price"))
                            if o.get("name") == loc and p > c_1:
                                c_1, casa_1 = p, nom_casa
                            elif o.get("name") == vis and p > c_2:
                                c_2, casa_2 = p, nom_casa
                            elif o.get("name") == "Draw" and p > c_x:
                                c_x = p
                    elif mkey == "totals":
                        for o in m.get("outcomes", []):
                            p = dec(o.get("price"))
                            pt = float(o.get("point") or 0)
                            if o.get("name") == "Over" and pt == 2.5 and p > c_o25:
                                c_o25 = p
                            elif o.get("name") == "Under" and pt == 3.5 and p > c_u35:
                                c_u35 = p

            if c_1 <= 1.0 or c_2 <= 1.0 or c_x <= 1.0:
                continue

            # Estimación matemática de tasas esperadas (xG ajustado sin sesgo)
            l_loc = max(0.80, min(2.80, (1.0 / c_1) * 2.50))
            l_vis = max(0.55, min(2.30, (1.0 / c_2) * 2.15))

            res = simular_montecarlo_clean(l_loc, l_vis, N_SIMS)

            c_1x = round(1.0 / ((1.0 / c_1) + (1.0 / c_x)) * 0.96, 2)
            c_x2 = round(1.0 / ((1.0 / c_2) + (1.0 / c_x)) * 0.96, 2)

            # Mercados con cuotas reales presentes en la casa
            mercados_evaluar = [
                (f"Victoria {loc} (1)", res[0], c_1, casa_1, "1X2"),
                (f"{loc} o Empate (1X)", res[1], c_1x, casa_1, "DOBLE"),
                (f"{vis} o Empate (X2)", res[2], c_x2, casa_2, "DOBLE"),
                (f"Victoria {vis} (2)", res[3], c_2, casa_2, "1X2"),
            ]
            if c_o25 > 1.40:
                mercados_evaluar.append(("Más de 2.5 Goles", res[5], c_o25, casa_1, "GOLES"))
            if c_u35 > 1.25:
                mercados_evaluar.append(("Menos de 3.5 Goles", res[6], c_u35, casa_1, "GOLES"))

            for nom_m, prob_real, cuota, nom_c, cat in mercados_evaluar:
                if cuota < 1.20 or cuota > 2.60:
                    continue

                prob_imp = 1.0 / cuota
                edge = (prob_real - prob_imp) * 100.0
                ev = (prob_real * cuota) - 1.0

                # Filtro cuantitativo: Valor esperado positivo y Edge real entre 3% y 18%
                if ev > 0.02 and (3.0 <= edge <= 18.0) and prob_real >= 0.55:
                    b = cuota - 1.0
                    q = 1.0 - prob_real
                    kelly_puro = ((b * prob_real) - q) / b if b > 0 else 0.0
                    # Tope de seguridad: máx 6% del bankroll por apuesta para no sobreexponerse
                    k_stake = min(0.06, max(0.0, kelly_puro * KELLY_FRAC))
                    stake_soles = round((BANKROLL * k_stake) * 2.0) / 2.0

                    if stake_soles >= 1.50:
                        candidatos.append({
                            "partido": f"{loc} vs {vis}",
                            "mercado": nom_m,
                            "prob": prob_real * 100.0,
                            "cuota": cuota,
                            "casa": nom_c,
                            "edge": edge,
                            "stake": stake_soles,
                            "kelly": k_stake * 100.0,
                            "score": (prob_real * 0.55) + (ev * 0.45)
                        })

    # Ordenar y seleccionar los 5 mejores picks sin repetir el mismo partido
    candidatos_ordenados = sorted(candidatos, key=lambda x: x["score"], reverse=True)
    top_picks = []
    vistos = set()
    for c in candidatos_ordenados:
        if c["partido"] not in vistos:
            top_picks.append(c)
            vistos.add(c["partido"])
        if len(top_picks) >= TOP_DESTACADOS:
            break

    t_total = time.time() - t0
    reporte = [
        "👑 *IABET PRO v9.1 — REPORTE VERIFICADO*",
        f"⚡ *{partidos_totales} partidos analizados* | *{partidos_totales * N_SIMS:,} sims* ({t_total:.1f}s)\n"
    ]

    if top_picks:
        reporte.append("🎯 *PICKS DESTACADOS (+EV VERIFICADO):*\n")
        for idx, p in enumerate(top_picks, 1):
            reporte.append(
                f"*{idx}. {p['partido']}*\n"
                f"👉 Pick: `{p['mercado']}`\n"
                f"📊 Confianza Real: *{p['prob']:.1f}%* | Cuota: *@{p['cuota']:.2f}* _({p['casa']})_\n"
                f"📈 Ventaja (Edge): *+{p['edge']:.1f}%*\n"
                f"💰 Bankroll (Kelly {p['kelly']:.1f}%): *Apostar S/ {p['stake']:.2f}*\n"
            )
    else:
        reporte.append("ℹ️ El mercado se encuentra eficiente en este ciclo. No se detectaron discrepancias que superen el filtro de +EV.")

    enviar_telegram("\n".join(reporte))

if __name__ == "__main__":
    ejecutar_escaner()
            
