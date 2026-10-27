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
from parlay_api import ParlayAPI

# ==============================================================================
# 1. CONFIGURACIÓN COMERCIAL Y CREDENCIALES (IABET SaaS v12.0)
# ==============================================================================
API_KEY = "105d8edef7a4869116a817c106dc86a8"
TELEGRAM_TOKEN = "8902181028:AAE1ywEiCc6do90ZPclI8GnUdmVqKBc7iOU"
TELEGRAM_CHAT_ID = "5203732922"

# INTERRUPTOR DE NEGOCIO:
# "PRUEBA" -> Muestra todos los picks abiertos en la web para ganar credibilidad.
# "VENTA"  -> Bloquea con efecto Blur los picks TIER S en la web para vender planes VIP.
MODO_COMERCIAL = "PRUEBA"
LINK_CONTACTO_VIP = "https://t.me/+TuEnlaceDeContacto"  # Cámbialo por tu link de WhatsApp o Telegram

BANKROLL_INICIAL = 100.0
KELLY_FRAC = 0.20
N_SIMS = 1_000_000
CUOTA_MIN = 1.45
CUOTA_MAX = 2.35
ARCHIVO_AUDITORIA = "auditoria_iabet.json"

LIGAS_FUTBOL = {
    "soccer_epl": ("Premier League", "E0", "eng.1", 3.8, 22.5, 9.2),
    "soccer_spain_la_liga": ("LaLiga", "SP1", "esp.1", 4.8, 25.5, 8.4),
    "soccer_italy_serie_a": ("Serie A", "I1", "ita.1", 4.4, 24.5, 8.6),
    "soccer_germany_bundesliga": ("Bundesliga", "D1", "ger.1", 3.9, 21.5, 9.6),
    "soccer_france_ligue_one": ("Ligue 1", "F1", "fra.1", 3.8, 23.5, 8.5),
    "soccer_uefa_champs_league": ("Champions League", None, "uefa.champions", 4.1, 22.8, 9.4),
    "soccer_conmebol_copa_libertadores": ("Copa Libertadores", None, "conmebol.libertadores", 5.4, 27.5, 8.0),
    "soccer_conmebol_copa_sudamericana": ("Copa Sudamericana", None, "conmebol.sudamericana", 5.3, 27.0, 7.9),
    "soccer_peru_primera_division": ("Liga 1 Perú", None, "per.1", 5.2, 26.8, 8.1),
    "soccer_brazil_campeonato": ("Brasileirão", None, "bra.1", 5.0, 26.5, 8.5),
    "soccer_argentina_primera_division": ("Liga Argentina", None, "arg.1", 5.1, 26.5, 7.8)
}

# ==============================================================================
# 2. FASE 1: MOTOR MONTE CARLO ACELERADO EN C (1,000,000 SIMULACIONES)
# ==============================================================================
@njit(parallel=True)
def motor_montecarlo_core(l_loc, l_vis, media_stat, es_poisson_stat, linea_stat, n_sims=1_000_000):
    c_1 = 0
    c_x = 0
    c_2 = 0
    c_ov15 = 0
    c_ov25 = 0
    c_un35 = 0
    c_btts = 0
    c_stat_over = 0
    c_stat_under = 0
    cov = 0.09

    for i in prange(n_sims):
        ritmo = np.random.poisson(cov)
        gl = np.random.poisson(max(0.05, l_loc - cov)) + ritmo
        gv = np.random.poisson(max(0.05, l_vis - cov)) + ritmo
        tot = gl + gv

        if gl > gv:
            c_1 += 1
        elif gl == gv:
            c_x += 1
        else:
            c_2 += 1

        if tot > 1.5:
            c_ov15 += 1
        if tot > 2.5:
            c_ov25 += 1
        if tot < 3.5:
            c_un35 += 1
        if gl > 0 and gv > 0:
            c_btts += 1

        if es_poisson_stat == 1:
            val_s = np.random.poisson(max(0.5, media_stat))
        else:
            std_s = max(1.8, np.sqrt(max(1.0, media_stat)) * 0.92)
            val_s = np.random.normal(media_stat, std_s)

        if val_s > linea_stat:
            c_stat_over += 1
        elif val_s < linea_stat:
            c_stat_under += 1

    return (
        c_1 / n_sims,
        c_x / n_sims,
        c_2 / n_sims,
        (c_1 + c_x) / n_sims,
        (c_2 + c_x) / n_sims,
        c_ov15 / n_sims,
        c_ov25 / n_sims,
        c_un35 / n_sims,
        c_btts / n_sims,
        c_stat_over / n_sims,
        c_stat_under / n_sims
    )

# ==============================================================================
# 3. FASE 1: INGESTA MULTIHILO (CSV HISTÓRICO L5·L10·L20 + ESPN API EN VIVO)
# ==============================================================================
def normalizar_nombre(nombre):
    if not nombre:
        return ""
    limpio = str(nombre).lower()
    for p in ["fc ", "cf ", "cd ", "ca ", "sc ", "ac ", "as ", "ud ", "rcd ", "real ", "sporting ", "club ", "deportivo ", "united", "city"]:
        limpio = limpio.replace(p, "")
    return limpio.strip()

def descargar_csv_liga(item):
    cod, info = item
    div_csv = info[1]
    if not div_csv:
        return cod, None
    cabeceras = {"User-Agent": "Mozilla/5.0"}
    for temp in ["2526", "2425", "2324"]:
        url = f"https://www.football-data.co.uk/mmz4281/{temp}/{div_csv}.csv"
        try:
            r = requests.get(url, headers=cabeceras, timeout=7)
            if r.status_code == 200 and len(r.text) > 500:
                df = pd.read_csv(io.StringIO(r.text))
                if "HomeTeam" in df.columns and "FTHG" in df.columns:
                    return cod, df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])
        except Exception:
            continue
    return cod, None

def descargar_espn_liga(item):
    cod, info = item
    espn_slug = info[2]
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{espn_slug}/scoreboard"
    resultados = {}
    try:
        r = requests.get(url, timeout=7)
        if r.status_code == 200:
            data = r.json()
            for ev in data.get("events", []):
                comp = ev.get("competitions", [{}])[0]
                estado = comp.get("status", {}).get("type", {}).get("state", "pre")
                equipos = comp.get("competitors", [])
                if len(equipos) == 2:
                    t_home = next((x for x in equipos if x.get("homeAway") == "home"), equipos[0])
                    t_away = next((x for x in equipos if x.get("homeAway") == "away"), equipos[1])
                    n_h = t_home.get("team", {}).get("displayName", "")
                    n_a = t_away.get("team", {}).get("displayName", "")
                    s_h = int(float(t_home.get("score", 0) or 0))
                    s_a = int(float(t_away.get("score", 0) or 0))
                    rec_h = t_home.get("records", [{}])[0].get("summary", "N/A") if t_home.get("records") else "N/A"
                    rec_a = t_away.get("records", [{}])[0].get("summary", "N/A") if t_away.get("records") else "N/A"
                    clave = f"{normalizar_nombre(n_h)}__{normalizar_nombre(n_a)}"
                    resultados[clave] = {
                        "home": n_h, "away": n_a, "state": estado,
                        "home_score": s_h, "away_score": s_a,
                        "rec_h": rec_h, "rec_a": rec_a
                    }
    except Exception:
        pass
    return cod, resultados

def obtener_datos_externos():
    caches_csv = {}
    espn_datos = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cod, df in ex.map(descargar_csv_liga, LIGAS_FUTBOL.items()):
            if df is not None:
                caches_csv[cod] = df
        for cod, res in ex.map(descargar_espn_liga, LIGAS_FUTBOL.items()):
            espn_datos.update(res)
    return caches_csv, espn_datos

def calcular_ventanas_l5_l10_l20(df, eq_loc, eq_vis, col_h, col_a, linea, direccion="UNDER"):
    if df is None or col_h not in df.columns or col_a not in df.columns:
        return None
    nl, nv = normalizar_nombre(eq_loc), normalizar_nombre(eq_vis)
    mask = (
        df["HomeTeam"].astype(str).apply(normalizar_nombre).str.contains(nl[:5], na=False) |
        df["AwayTeam"].astype(str).apply(normalizar_nombre).str.contains(nv[:5], na=False)
    )
    sub = df[mask].tail(20)
    if len(sub) < 6:
        return None
    totales = (sub[col_h] + sub[col_a]).dropna().values
    if len(totales) < 6:
        return None

    def hit_rate(arr):
        if len(arr) == 0:
            return 0.0
        aciertos = np.sum(arr < linea) if direccion == "UNDER" else np.sum(arr > linea)
        return float((aciertos / len(arr)) * 100.0)

    return {
        "media": float(np.mean(totales)),
        "L5": hit_rate(totales[-5:]),
        "L10": hit_rate(totales[-10:]),
        "L20": hit_rate(totales[-20:])
    }

# ==============================================================================
# 4. FASE 2: MOTOR DE AUDITORÍA AUTOMÁTICA E INMUTABLE (VERIFICACIÓN ESPN)
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
            "pendientes": 0,
            "racha_actual": "0W"
        },
        "historial": []
    }

def auditar_picks_con_espn(auditoria, espn_datos):
    historial = auditoria.get("historial", [])
    cambios = False

    for pick in historial:
        if pick.get("estado") != "PENDIENTE":
            continue
        clave = f"{normalizar_nombre(pick.get('local', ''))}__{normalizar_nombre(pick.get('visita', ''))}"
        partido_espn = espn_datos.get(clave)

        # Búsqueda flexible si el nombre varía ligeramente
        if not partido_espn:
            nl = normalizar_nombre(pick.get("local", ""))[:5]
            nv = normalizar_nombre(pick.get("visita", ""))[:5]
            for k, val in espn_datos.items():
                if nl in k and nv in k:
                    partido_espn = val
                    break

        if partido_espn and partido_espn.get("state") == "post":
            gh = partido_espn["home_score"]
            ga = partido_espn["away_score"]
            gtot = gh + ga
            mercado = pick.get("mercado", "")
            gano = None

            if "Victoria Local (1)" in mercado:
                gano = (gh > ga)
            elif "Victoria Visita (2)" in mercado:
                gano = (ga > gh)
            elif "Empate (X)" in mercado:
                gano = (gh == ga)
            elif "Doble Oportunidad 1X" in mercado:
                gano = (gh >= ga)
            elif "Doble Oportunidad X2" in mercado:
                gano = (ga >= gh)
            elif "Más de 1.5 Goles" in mercado:
                gano = (gtot > 1.5)
            elif "Más de 2.5 Goles" in mercado:
                gano = (gtot > 2.5)
            elif "Menos de 3.5 Goles" in mercado:
                gano = (gtot < 3.5)
            elif "Ambos Anotan" in mercado:
                gano = (gh > 0 and ga > 0)

            if gano is not None:
                pick["marcador_final"] = f"{gh}-{ga}"
                stake = float(pick.get("stake", 2.5))
                cuota = float(pick.get("cuota", 1.60))
                if gano:
                    pick["estado"] = "GANADO"
                    pick["profit"] = round(stake * (cuota - 1.0), 2)
                else:
                    pick["estado"] = "PERDIDO"
                    pick["profit"] = round(-stake, 2)
                cambios = True

    # Recalcular métricas financieras auditadas
    cerrados = [p for p in historial if p.get("estado") in ("GANADO", "PERDIDO")]
    ganados = sum(1 for p in cerrados if p["estado"] == "GANADO")
    perdidos = sum(1 for p in cerrados if p["estado"] == "PERDIDO")
    pendientes = sum(1 for p in historial if p.get("estado") == "PENDIENTE")
    total_cerrados = ganados + perdidos

    profit_total = sum(float(p.get("profit", 0.0)) for p in cerrados)
    stake_total = sum(float(p.get("stake", 0.0)) for p in cerrados)

    win_rate = (ganados / total_cerrados * 100.0) if total_cerrados > 0 else 0.0
    roi = (profit_total / stake_total * 100.0) if stake_total > 0 else 0.0

    # Calcular racha actual
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
        "pendientes": pendientes,
        "racha_actual": f"{racha_cnt}{racha_tipo}" if racha_cnt > 0 else "En espera"
    }
    return auditoria

def clasificar_tier(prob_pct, edge_pct):
    if prob_pct >= 72.0 and edge_pct >= 3.5:
        return "TIER S", "💎 TIER S (Blindado)", 1
    elif prob_pct >= 64.0 and edge_pct >= 4.0:
        return "TIER A", "🥇 TIER A (Alta Ventaja)", 2
    elif prob_pct >= 56.0 and edge_pct >= 4.5:
        return "TIER B", "🥈 TIER B (Valor +EV)", 3
    return "DESCARTADO", "❌", 99

# ==============================================================================
# 5. FASE 3: GENERADOR DE PLATAFORMA WEB COMERCIAL (index.html)
# ==============================================================================
def generar_web_comercial(auditoria, picks_jornada, surebets, partidos_analizados):
    res = auditoria["resumen"]
    historial = auditoria["historial"][-30:][::-1]  # Últimos 30 picks auditados
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
                    <span>🔒 PICK EXCLUSIVO TIER S ({p['prob']:.1f}% Confianza)</span>
                    <a href="{LINK_CONTACTO_VIP}" target="_blank" class="btn-vip">Desbloquear Plan VIP</a>
                </div>
            </div>
            """

        tarjetas_html += f"""
        <div class="pick-card">
            <div class="pick-header">
                <span class="league-tag">{p['liga']}</span>
                <span class="tier-badge" style="background:{badge_color}22; color:{badge_color}; border:1px solid {badge_color};">{p['tier_label']}</span>
            </div>
            <div class="CardBody {blur_class}">
                <h3 class="match-title">{p['local']} vs {p['visita']}</h3>
                <div class="market-box">
                    <div>
                        <span class="market-label">PRONÓSTICO IA</span>
                        <div class="market-name">{p['mercado']}</div>
                    </div>
                    <div class="odd-pill">@{p['cuota']:.2f}</div>
                </div>
                <div class="metrics-grid">
                    <div class="metric-item">
                        <span class="m-label">Prob. Real</span>
                        <span class="m-val" style="color:#10b981;">{p['prob']:.1f}%</span>
                    </div>
                    <div class="metric-item">
                        <span class="m-label">Edge (+EV)</span>
                        <span class="m-val" style="color:#38bdf8;">+{p['edge']:.1f}%</span>
                    </div>
                    <div class="metric-item">
                        <span class="m-label">Stake Kelly</span>
                        <span class="m-val">S/ {p['stake']:.2f}</span>
                    </div>
                    <div class="metric-item">
                        <span class="m-label">Casa Óptima</span>
                        <span class="m-val">{p['casa']}</span>
                    </div>
                </div>
                <div class="l-bars">
                    <span>📊 Consenso Histórico: <b>{p['ventanas']}</b></span>
                </div>
                <p class="ai-note">🧠 {p['explicacion']}</p>
            </div>
            {overlay_vip}
        </div>
        """

    if not tarjetas_html:
        tarjetas_html = """
        <div class="empty-state">
            <h3>🛡️ Mercado Eficiente en este Ciclo</h3>
            <p>El motor analizó las líneas actuales y descartó cuotas sin ventaja matemática suficiente (+EV). El próximo escaneo automático actualizará nuevas oportunidades.</p>
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
        else:
            badge_est = '<span class="st-pend">⏳ EN JUEGO / PEND.</span>'
            prof_str = '<span style="color:#94a3b8;">S/ 0.00</span>'

        filas_tabla += f"""
        <tr>
            <td>{h.get('fecha', '')}</td>
            <td><b>{h.get('local', '')} vs {h.get('visita', '')}</b><br><small style="color:#94a3b8;">{h.get('liga', '')}</small></td>
            <td>{h.get('mercado', '')}</td>
            <td>@{float(h.get('cuota', 1.0)):.2f}</td>
            <td>{h.get('prob', 0):.1f}%</td>
            <td>{h.get('marcador_final', '-')}</td>
            <td>{badge_est}</td>
            <td>{prof_str}</td>
        </tr>
        """

    html_completo = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IABET Quant | Predicciones Deportivas Auditadas con IA</title>
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
        .container {{ max-width: 1180px; margin: 0 auto; padding: 24px 16px; }}
        .hero {{ text-align: center; padding: 28px 16px; background: radial-gradient(circle at top, #1e293b60, transparent 70%); border-radius: 16px; margin-bottom: 28px; border: 1px solid var(--border); }}
        .hero h1 {{ font-size: 2rem; font-weight: 800; margin-bottom: 8px; }}
        .hero p {{ color: var(--muted); max-width: 680px; margin: 0 auto; font-size: 0.95rem; }}
        .stats-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin-bottom: 32px; }}
        .stat-box {{ background: var(--card); border: 1px solid var(--border); padding: 18px; border-radius: 12px; text-align: center; }}
        .stat-box .s-label {{ font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-box .s-val {{ font-size: 1.65rem; font-weight: 800; margin-top: 4px; color: #fff; }}
        .section-title {{ font-size: 1.25rem; font-weight: 700; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }}
        .picks-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 18px; margin-bottom: 42px; }}
        .pick-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px; position: relative; overflow: hidden; transition: transform 0.2s; }}
        .pick-card:hover {{ transform: translateY(-3px); border-color: #334155; }}
        .pick-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
        .league-tag {{ font-size: 0.75rem; color: var(--muted); font-weight: 600; text-transform: uppercase; }}
        .tier-badge {{ font-size: 0.72rem; font-weight: 700; padding: 3px 9px; border-radius: 99px; }}
        .match-title {{ font-size: 1.1rem; font-weight: 700; margin-bottom: 12px; color: #fff; }}
        .market-box {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }}
        .market-label {{ font-size: 0.68rem; color: var(--muted); font-weight: 700; }}
        .market-name {{ font-size: 1rem; font-weight: 800; color: #38bdf8; }}
        .odd-pill {{ background: #10b98120; color: #10b981; font-weight: 800; font-size: 1.1rem; padding: 6px 12px; border-radius: 8px; border: 1px solid #10b98140; }}
        .metrics-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; text-align: center; background: #0b0f19; padding: 10px; border-radius: 8px; }}
        .m-label {{ display: block; font-size: 0.68rem; color: var(--muted); }}
        .m-val {{ font-size: 0.88rem; font-weight: 700; }}
        .l-bars {{ font-size: 0.78rem; color: #cbd5e1; background: #1e293b60; padding: 7px 10px; border-radius: 6px; margin-bottom: 10px; }}
        .ai-note {{ font-size: 0.8rem; color: var(--muted); border-top: 1px solid var(--border); padding-top: 8px; }}
        .blur-lock {{ filter: blur(6px); user-select: none; pointer-events: none; }}
        .vip-overlay {{ position: absolute; inset: 0; background: rgba(11, 15, 25, 0.72); display: flex; align-items: center; justify-content: center; padding: 20px; text-align: center; }}
        .vip-box {{ background: #111827; border: 1px solid #10b981; padding: 16px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.6); }}
        .vip-box span {{ display: block; font-weight: 700; font-size: 0.9rem; margin-bottom: 10px; color: #fff; }}
        .btn-vip {{ display: inline-block; background: #10b981; color: #052e16; font-weight: 800; text-decoration: none; padding: 8px 16px; border-radius: 8px; font-size: 0.85rem; }}
        .table-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; text-align: left; }}
        th {{ background: #0f172a; color: var(--muted); padding: 12px 14px; font-size: 0.75rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }}
        td {{ padding: 12px 14px; border-bottom: 1px solid var(--border); }}
        .st-won {{ background: #10b98120; color: #10b981; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.75rem; }}
        .st-lost {{ background: #ef444420; color: #ef4444; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.75rem; }}
        .st-pend {{ background: #f59e0b20; color: #f59e0b; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 0.75rem; }}
        .empty-state {{ grid-column: 1 / -1; background: var(--card); border: 1px dashed #334155; padding: 36px; border-radius: 14px; text-align: center; color: var(--muted); }}
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="logo">⚡ IABET <span>QUANT PRO</span></div>
        <div class="status-pill">🟢 Motor Activo | Modo: {MODO_COMERCIAL}</div>
    </nav>
    <div class="container">
        <div class="hero">
            <h1>Inteligencia Artificial Cuantitativa & Auditoría en Vivo</h1>
            <p>1,000,000 de simulaciones de Monte Carlo por partido, De-Vigging institucional contra consenso de mercado y validación histórica L5 · L10 · L20 verificada automáticamente con ESPN API.</p>
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
                <div class="s-label">Racha Actual</div>
                <div class="s-val">🔥 {res['racha_actual']}</div>
            </div>
        </div>

        <div class="section-title">
            <span>🎯 Picks Destacados de la Jornada ({len(picks_jornada)} seleccionados de {partidos_analizados} partidos)</span>
            <small style="color:var(--muted); font-size:0.8rem;">Actualizado: {fecha_act}</small>
        </div>
        <div class="picks-grid">
            {tarjetas_html}
        </div>

        <div class="section-title">
            <span>📋 Historial de Auditoría Transparente (Verificación Automática ESPN)</span>
        </div>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>Fecha</th>
                        <th>Partido / Torneo</th>
                        <th>Pronóstico IA</th>
                        <th>Cuota</th>
                        <th>Confianza</th>
                        <th>Resultado</th>
                        <th>Estado</th>
                        <th>Beneficio</th>
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
# 6. ORQUESTADOR PRINCIPAL: ESCANEO, AUDITORÍA, WEB Y TELEGRAM
# ==============================================================================
def enviar_telegram_seguro(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    bloques = [texto[i:i+3900] for i in range(0, len(texto), 3900)]
    for b in bloques:
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": b}, timeout=10)
        except Exception:
            pass

def ejecutar_iabet_saas():
    t0 = time.time()
    caches_csv, espn_datos = obtener_datos_externos()

    # 1. Auditar picks anteriores con resultados oficiales de ESPN
    auditoria = cargar_auditoria()
    auditoria = auditar_picks_con_espn(auditoria, espn_datos)
    bankroll_dinamico = max(50.0, float(auditoria["resumen"]["bankroll_actual"]))

    # 2. Descargar cuotas en vivo de ParlayAPI
    cliente = ParlayAPI(api_key=API_KEY)
    eventos_todos = []
    for cod, info in LIGAS_FUTBOL.items():
        try:
            evs = cliente.odds(cod, regions="eu", markets="h2h,totals")
            if evs:
                for e in evs:
                    e["cod_liga"] = cod
                    e["liga"] = info[0]
                    e["base_tarj"] = info[3]
                    e["base_falt"] = info[4]
                    e["base_sot"] = info[5]
                    eventos_todos.append(e)
        except Exception:
            continue

    candidatos = []
    surebets = []
    partidos_analizados = 0
    vistos = set()

    for ev in eventos_todos:
        loc = ev.get("home_team")
        vis = ev.get("away_team")
        liga = ev.get("liga", "Fútbol")
        cod_liga = ev.get("cod_liga")
        if not loc or not vis:
            continue
        id_unico = f"{normalizar_nombre(loc)}__{normalizar_nombre(vis)}"
        if id_unico in vistos:
            continue
        vistos.add(id_unico)

        bms = ev.get("bookmakers", [])
        if not bms:
            continue

        # Extraer tanto la MEJOR cuota como el PROMEDIO de mercado (para De-Vigging real)
        odds_1, odds_x, odds_2 = [], [], []
        best_1, best_x, best_2 = (0.0, ""), (0.0, ""), (0.0, "")
        odds_ov25, odds_un25 = [], []
        best_ov25, best_un25 = (0.0, ""), (0.0, "")

        for b in bms:
            nb = b.get("title", "Bookie")
            for m in b.get("markets", []):
                mk = m.get("key")
                if mk == "h2h":
                    for o in m.get("outcomes", []):
                        pr = float(o.get("price", 0))
                        if pr <= 1.01:
                            continue
                        if o.get("name") == loc:
                            odds_1.append(pr)
                            if pr > best_1[0]: best_1 = (pr, nb)
                        elif o.get("name") == vis:
                            odds_2.append(pr)
                            if pr > best_2[0]: best_2 = (pr, nb)
                        else:
                            odds_x.append(pr)
                            if pr > best_x[0]: best_x = (pr, nb)
                elif mk == "totals":
                    for o in m.get("outcomes", []):
                        pt = float(o.get("point", 0) or 0)
                        pr = float(o.get("price", 0) or 0)
                        if abs(pt - 2.5) < 0.1 and pr > 1.01:
                            if o.get("name") == "Over":
                                odds_ov25.append(pr)
                                if pr > best_ov25[0]: best_ov25 = (pr, nb)
                            elif o.get("name") == "Under":
                                odds_un25.append(pr)
                                if pr > best_un25[0]: best_un25 = (pr, nb)

        if not odds_1 or not odds_2 or not odds_x:
            continue
        partidos_analizados += 1

        # Detector de Surebets
        inv_sum = (1.0 / best_1[0]) + (1.0 / best_x[0]) + (1.0 / best_2[0])
        if inv_sum < 0.995:
            ben = ((1.0 / inv_sum) - 1.0) * 100.0
            surebets.append(f"🔥 SUREBET (+{ben:.2f}%): {loc} vs {vis} | 1: @{best_1[0]} ({best_1[1]}) | X: @{best_x[0]} ({best_x[1]}) | 2: @{best_2[0]} ({best_2[1]})")

        # De-Vigging de Consenso (Probabilidad limpia del mercado)
        m1, mx, m2 = np.mean(odds_1), np.mean(odds_x), np.mean(odds_2)
        overround = (1.0 / m1) + (1.0 / mx) + (1.0 / m2)
        p_cons_1 = (1.0 / m1) / overround
        p_cons_2 = (1.0 / m2) / overround

        goles_esp_tot = 2.62
        if odds_ov25 and odds_un25:
            mov, mun = np.mean(odds_ov25), np.mean(odds_un25)
            p_ov_clean = (1.0 / mov) / ((1.0 / mov) + (1.0 / mun))
            goles_esp_tot = 2.15 + (p_ov_clean * 1.15)

        df_liga = caches_csv.get(cod_liga)
        vent_goles = calcular_ventanas_l5_l10_l20(df_liga, loc, vis, "FTHG", "FTAG", 2.5, "OVER")
        if vent_goles:
            goles_esp_tot = (goles_esp_tot * 0.65) + (vent_goles["media"] * 0.35)

        peso_loc = p_cons_1 / max(0.10, (p_cons_1 + p_cons_2))
        l_loc = max(0.35, goles_esp_tot * peso_loc * 1.04)
        l_vis = max(0.30, goles_esp_tot * (1.0 - peso_loc) * 0.96)

        res_mc = motor_montecarlo_core(l_loc, l_vis, ev["base_falt"], 0, ev["base_falt"], N_SIMS)

        c_1x = 1.0 / ((1.0 / best_1[0]) + (1.0 / best_x[0]))
        c_x2 = 1.0 / ((1.0 / best_2[0]) + (1.0 / best_x[0]))

        mercados_eval = [
            ("Victoria Local (1)", res_mc[0], best_1[0], best_1[1]),
            ("Victoria Visita (2)", res_mc[2], best_2[0], best_2[1]),
            ("Doble Oportunidad 1X", res_mc[3], c_1x, best_1[1]),
            ("Doble Oportunidad X2", res_mc[4], c_x2, best_2[1]),
        ]
        if best_ov25[0] > 1.05:
            mercados_eval.append(("Más de 2.5 Goles", res_mc[6], best_ov25[0], best_ov25[1]))
        if best_un25[0] > 1.05:
            c_un35_est = max(1.32, best_un25[0] * 0.78)
            mercados_eval.append(("Menos de 3.5 Goles", res_mc[7], c_un35_est, best_un25[1]))

        str_vent = f"L5: {vent_goles['L5']:.0f}% | L10: {vent_goles['L10']:.0f}% | L20: {vent_goles['L20']:.0f}%" if vent_goles else "Consenso De-Vig + Monte Carlo 1M"

        for nom_m, p_real, cuota_m, casa_m in mercados_eval:
            if not (CUOTA_MIN <= cuota_m <= CUOTA_MAX):
                continue
            p_imp = 1.0 / cuota_m
            edge_pct = (p_real - p_imp) * 100.0
            if edge_pct < 3.5 or edge_pct > 18.0:
                continue

            prob_pct = p_real * 100.0
            tier_cod, tier_label, tier_rank = clasificar_tier(prob_pct, edge_pct)
            if tier_cod == "DESCARTADO":
                continue

            b = cuota_m - 1.0
            kelly = max(0.0, (((b * p_real) - (1.0 - p_real)) / b) * KELLY_FRAC)
            stake_soles = min(6.0, max(1.50, round((bankroll_dinamico * kelly) * 2) / 2))

            candidatos.append({
                "fecha": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "liga": liga,
                "local": loc,
                "visita": vis,
                "mercado": nom_m,
                "cuota": round(cuota_m, 2),
                "casa": casa_m,
                "prob": round(prob_pct, 1),
                "edge": round(edge_pct, 1),
                "stake": stake_soles,
                "tier_cod": tier_cod,
                "tier_label": tier_label,
                "tier_rank": tier_rank,
                "ventanas": str_vent,
                "explicacion": f"Simulación 1M proyecta λ=({l_loc:.2f} vs {l_vis:.2f}). Cuota @{cuota_m:.2f} en {casa_m} supera la línea justa sin vig.",
                "estado": "PENDIENTE",
                "marcador_final": "-",
                "profit": 0.0
            })

    # Ordenar por Tier y mayor ventaja matemática, diversificando partidos
    candidatos.sort(key=lambda x: (x["tier_rank"], -x["edge"]))
    picks_finales = []
    partidos_usados = set()
    for c in candidatos:
        clave_p = f"{c['local']}_{c['visita']}"
        if clave_p not in partidos_usados and len(picks_finales) < 8:
            picks_finales.append(c)
            partidos_usados.add(clave_p)

    # Registrar nuevos picks en la base de datos de auditoría (sin duplicar)
    claves_hist = {f"{h['fecha']}_{h['local']}_{h['mercado']}" for h in auditoria["historial"]}
    for pf in picks_finales:
        k_nuevo = f"{pf['fecha']}_{pf['local']}_{pf['mercado']}"
        if k_nuevo not in claves_hist:
            auditoria["historial"].append(pf)
            claves_hist.add(k_nuevo)

    auditoria["resumen"]["pendientes"] = sum(1 for p in auditoria["historial"] if p.get("estado") == "PENDIENTE")

    with open(ARCHIVO_AUDITORIA, "w", encoding="utf-8") as f:
        json.dump(auditoria, f, ensure_ascii=False, indent=2)

    # 3. Generar la Página Web Comercial (index.html)
    generar_web_comercial(auditoria, picks_finales, surebets, partidos_analizados)

    # 4. Enviar Reporte Ejecutivo Auditado a Telegram
    res = auditoria["resumen"]
    dur = time.time() - t0
    lineas_tg = [
        "⚡ IABET QUANT SaaS v12.0 (REPORTE AUDITADO)",
        f"📊 Efectividad: {res['win_rate_pct']:.1f}% ({res['ganados']}W-{res['perdidos']}L) | ROI: {res['roi_pct']:+.1f}%",
        f"💰 Bankroll Auditado: S/ {res['bankroll_actual']:.2f} | Racha: 🔥 {res['racha_actual']}",
        f"⏱️ {partidos_analizados} partidos escaneados en {dur:.1f}s | Modo Web: {MODO_COMERCIAL}",
        "━━━━━━━━━━━━━━━━━━━━"
    ]

    if surebets:
        lineas_tg.append("🚨 SUREBETS DETECTADAS:")
        lineas_tg.extend(surebets[:3])
        lineas_tg.append("━━━━━━━━━━━━━━━━━━━━")

    if picks_finales:
        for idx, p in enumerate(picks_finales, 1):
            lineas_tg.append(
                f"{idx}. {p['tier_label']}\n"
                f"⚽ {p['local']} vs {p['visita']} ({p['liga']})\n"
                f"🎯 Pick: {p['mercado']}  👉  @{p['cuota']:.2f} ({p['casa']})\n"
                f"📈 Confianza: {p['prob']:.1f}% | Edge: +{p['edge']:.1f}% | Stake: S/ {p['stake']:.2f}\n"
                f"📊 {p['ventanas']}\n"
            )
    else:
        lineas_tg.append("🛡️ Ciclo completado: Cuotas alineadas con el modelo en esta ventana horaria.")

    enviar_telegram_seguro("\n".join(lineas_tg))
    print("✅ Ejecución IABET SaaS v12.0 completada con éxito.")

if __name__ == "__main__":
    ejecutar_iabet_saas()
