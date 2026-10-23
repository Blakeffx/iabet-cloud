import time
import requests
import numpy as np
import pandas as pd
from numba import njit, prange
from parlay_api import ParlayAPI

# ==============================================================
# ⚙️ 1. TUS CREDENCIALES
# ==============================================================
API_KEY = "105d8edef7a4869116a817c106dc86a8"
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

DEPORTE = "soccer_spain_la_liga"
BANKROLL = 100.0   
KELLY_FRAC = 0.25  
N_SIMS = 1_000_000

# ==============================================================
# 📲 2. CONEXIÓN CON TELEGRAM
# ==============================================================
def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error de red: {e}")

# ==============================================================
# 🧠 3. MOTOR DE MONTE CARLO (GOLES Y TIROS A PUERTA)
# ==============================================================
@njit(parallel=True)
def motor_montecarlo_goles(lambda_loc, lambda_vis, n_sims):
    vic_loc = 0
    empate = 0
    vic_vis = 0
    over_15 = 0
    over_25 = 0
    covarianza = 0.10

    for i in prange(n_sims):
        ritmo = np.random.poisson(covarianza)
        g_l = np.random.poisson(max(0.05, lambda_loc - covarianza)) + ritmo
        g_v = np.random.poisson(max(0.05, lambda_vis - covarianza)) + ritmo
        tot = g_l + g_v

        if g_l > g_v: vic_loc += 1
        elif g_l == g_v: empate += 1
        else: vic_vis += 1

        if tot > 1.5: over_15 += 1
        if tot > 2.5: over_25 += 1

    return vic_loc / n_sims, empate / n_sims, vic_vis / n_sims, (vic_loc + empate) / n_sims

@njit(parallel=True)
def motor_montecarlo_tiros(lambda_tiros, n_sims):
    """Simula las probabilidades de que un jugador cumpla +0.5 tiros al arco"""
    aciertos = 0
    for i in prange(n_sims):
        if np.random.poisson(lambda_tiros) >= 1:
            aciertos += 1
    return aciertos / n_sims

def dec(c):
    if c is None: return 2.00
    c = float(c)
    if c >= 100: return (c / 100.0) + 1.0
    elif c <= -100: return (100.0 / abs(c)) + 1.0
    return c

# ==============================================================
# 📡 4. EJECUCIÓN EN LA NUBE (GOLES + PROPS DE JUGADORES)
# ==============================================================
def ejecutar_escaner_nube():
    t0 = time.time()
    
    try:
        cliente = ParlayAPI(api_key=API_KEY)
        # Pedimos tanto el mercado de resultado (h2h) como el de tiros a puerta de jugadores
        eventos = cliente.odds(DEPORTE, regions="eu", markets="h2h,player_shots_on_target")
    except Exception as e:
        enviar_telegram(f"❌ *Error IABET Cloud:* No se pudo conectar a ParlayAPI.\n`{e}`")
        return

    picks_alta_confianza = []
    surebets_encontradas = []
    partidos_validos = 0

    for evento in eventos:
        loc = evento.get("home_team")
        vis = evento.get("away_team")
        if not loc or not vis or "(Bookings)" in loc or "(Corners)" in loc:
            continue

        bookmakers = evento.get("bookmakers", [])
        if not bookmakers:
            continue

        partidos_validos += 1
        casa_principal = bookmakers[0]
        mercados = casa_principal.get("markets", [])

        # --- A) DETECTOR DE SUREBETS ---
        mejor_1, mejor_X, mejor_2 = {"cuota": 0.0, "casa": ""}, {"cuota": 0.0, "casa": ""}, {"cuota": 0.0, "casa": ""}
        for casa in bookmakers:
            nom_casa = casa.get("title", "Casa")
            for m in casa.get("markets", []):
                if m.get("key") == "h2h":
                    for out in m.get("outcomes", []):
                        nom_op = out.get("name")
                        precio = dec(out.get("price"))
                        if nom_op == loc and precio > mejor_1["cuota"]: mejor_1 = {"cuota": precio, "casa": nom_casa}
                        elif nom_op == vis and precio > mejor_2["cuota"]: mejor_2 = {"cuota": precio, "casa": nom_casa}
                        elif nom_op == "Draw" and precio > mejor_X["cuota"]: mejor_X = {"cuota": precio, "casa": nom_casa}

        if mejor_1["cuota"] > 0 and mejor_X["cuota"] > 0 and mejor_2["cuota"] > 0:
            margen = (1/mejor_1["cuota"]) + (1/mejor_X["cuota"]) + (1/mejor_2["cuota"])
            if margen < 1.0:
                rent = ((1.0 / margen) - 1.0) * 100
                s1 = BANKROLL / (mejor_1["cuota"] * margen)
                sX = BANKROLL / (mejor_X["cuota"] * margen)
                s2 = BANKROLL / (mejor_2["cuota"] * margen)
                surebets_encontradas.append(
                    f"🚨 *SUREBET ({rent:.2f}% Ganancia)*\n⚽ {loc} vs {vis}\n"
                    f"• 1 ({mejor_1['casa']} @{mejor_1['cuota']:.2f}): *S/ {s1:.2f}*\n"
                    f"• X ({mejor_X['casa']} @{mejor_X['cuota']:.2f}): *S/ {sX:.2f}*\n"
                    f"• 2 ({mejor_2['casa']} @{mejor_2['cuota']:.2f}): *S/ {s2:.2f}*\n"
                )

        # --- B) EVALUACIÓN DE GOLES Y RESULTADOS ---
        cuotas_1x2 = {}
        player_props = []
        
        for m in mercados:
            if m.get("key") == "h2h":
                for out in m.get("outcomes", []):
                    cuotas_1x2[out.get("name")] = out.get("price")
            elif m.get("key") == "player_shots_on_target":
                # Capturamos las líneas de tiros a puerta de los jugadores
                for out in m.get("outcomes", []):
                    if out.get("description") and "+0.5" in str(out.get("point", "")):
                        player_props.append({
                            "jugador": out.get("description"),
                            "cuota": dec(out.get("price"))
                        })

        if cuotas_1x2:
            c_1 = dec(cuotas_1x2.get(loc, 2.00))
            c_2 = dec(cuotas_1x2.get(vis, 3.50))
            c_x = dec(cuotas_1x2.get("Draw", 3.20))
            c_1x = round(1 / ((1/c_1) + (1/c_x)), 2)

            lambda_l = max(0.8, min(3.0, (1.0 / c_1) * 2.6))
            lambda_v = max(0.5, min(2.5, (1.0 / c_2) * 2.2))

            res_goles = motor_montecarlo_goles(lambda_l, lambda_v, N_SIMS)

            mercados_evaluar = {
                f"Victoria {loc} (1)": (res_goles[0], c_1),
                f"{loc} o Empate (1X)": (res_goles[3], c_1x)
            }

            for nom, (prob_real, cuota) in mercados_evaluar.items():
                ev = (prob_real * cuota) - 1.0
                b = cuota - 1.0
                q = 1.0 - prob_real
                kelly_puro = ((b * prob_real) - q) / b if b > 0 else 0
                dinero = BANKROLL * max(0.0, kelly_puro * KELLY_FRAC)

                if ev > 0.0 and prob_real >= 0.70 and dinero >= 1.0:
                    picks_alta_confianza.append(
                        f"⚽ *{loc} vs {vis}*\n👉 `{nom}`\n"
                        f"📊 Confianza: *{prob_real*100:.1f}%* | Cuota: *{cuota:.2f}*\n"
                        f"💰 Apostar: *S/ {dinero:.2f}* (EV: +{ev*100:.1f}%)\n"
                    )

        # --- C) EVALUACIÓN DE PLAYER PROPS (TIROS AL ARCO) ---
        for prop in player_props:
            # Estimamos un lambda conservador de tiros basado en la cuota ofrecida por la casa
            lambda_estimada_tiros = 1.35 if prop["cuota"] < 1.40 else 1.15
            prob_real_tiros = motor_montecarlo_tiros(lambda_estimada_tiros, N_SIMS)
            
            cuota_prop = prop["cuota"]
            ev_prop = (prob_real_tiros * cuota_prop) - 1.0
            b_prop = cuota_prop - 1.0
            q_prop = 1.0 - prob_real_tiros
            kelly_prop = ((b_prop * prob_real_tiros) - q_prop) / b_prop if b_prop > 0 else 0
            dinero_prop = BANKROLL * max(0.0, kelly_prop * KELLY_FRAC)

            if ev_prop > 0.0 and prob_real_tiros >= 0.70 and dinero_prop >= 1.0:
                picks_alta_confianza.append(
                    f"🎯 *PROP TIROS: {loc} vs {vis}*\n"
                    f"👉 Jugador: `{prop['jugador']}` (+0.5 Remates al Arco)\n"
                    f"📊 Confianza: *{prob_real_tiros*100:.1f}%* | Cuota: *{cuota_prop:.2f}*\n"
                    f"💰 Apostar: *S/ {dinero_prop:.2f}* (EV: +{ev_prop*100:.1f}%)\n"
                )

    duracion = time.time() - t0
    bloques = [
        f"☁️ *REPORTE IABET CLOUD (CON TIROS AL ARCO)*",
        f"✅ ParlayAPI: *{partidos_validos} partidos* procesados en {duracion:.2f}s.\n"
    ]

    if surebets_encontradas: bloques.append("\n".join(surebets_encontradas))
    if picks_alta_confianza: bloques.append("🎯 *PICKS ALTA CONFIANZA (70%-100%):*\n" + "\n".join(picks_alta_confianza))
    else: bloques.append("ℹ️ Sin picks >=70% con +EV en este barrido.")

    enviar_telegram("\n".join(bloques))

if __name__ == "__main__":
    ejecutar_escaner_nube()
        
