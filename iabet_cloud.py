import json
import re
from datetime import datetime, timezone
from scrapling.fetchers import FetcherSession, StealthySession

# ==============================================================================
# 1. CONFIGURACIÓN DEL EXTRACTOR NINJA + ARBITRAJE IABET
# ==============================================================================
URL_NINJABET_BASE = "https://ninjabet.es/"
URL_NINJABET_DUTCHER = "https://ninjabet.es/dutcher"  # Herramienta de SureBets
BANKROLL_SUREBET = 100.0                              # Capital base en Soles (S/ 100)
ARCHIVO_SALIDA_NINJA = "ninjabet_intelligence.json"

# Opcional: Si tienes una cuenta en NinjaBet, pega aquí tu cookie de sesión
# (o cárgala desde GitHub Secrets) para desbloquear el JSON completo del Dutcher.
NINJABET_SESSION_COOKIE = "" 


def extraer_datos_publicos_y_bonos_ninjabet():
    """
    Usa FetcherSession de Scrapling con suplantación TLS de Chrome para extraer:
    1. Ofertas/Bonos activos (Matched Betting) publicados en NinjaBet.
    2. Eventos muestra de MultiOdds / ValueBets.
    3. Rendimiento auditado de SureBets y ValueBets para benchmarking de IABET.
    """
    datos_extraidos = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "bonos_matched_betting": [],
        "multiodds_muestra": [],
        "benchmark_ninjabet": {}
    }

    # FetcherSession imita el fingerprint TLS/JA3 real de Chrome sin abrir navegador
    with FetcherSession(impersonate="chrome", stealthy_headers=True, timeout=15) as session:
        page = session.get(URL_NINJABET_BASE)
        if page.status != 200:
            print(f"[!] Estado HTTP inesperado en NinjaBet: {page.status}")
            return datos_extraidos

        texto_completo = page.get_all_text(separator=" | ")

        # 1. Extraer Benchmark de SureBets y ValueBets de NinjaBet mediante Regex
        match_sb = re.search(r"(\+\d+[,\.]\d+\s*%)\s*Rendimiento sobre lo apostado", texto_completo)
        match_vb = re.search(r"(\+\d+[,\.]\d+\s*%)\s*Rendimiento.*?(\d+[\.\d]*)\s*Apuestas cerradas", texto_completo)
        if match_sb:
            datos_extraidos["benchmark_ninjabet"]["surebet_yield"] = match_sb.group(1)
        if match_vb:
            datos_extraidos["benchmark_ninjabet"]["valuebet_yield"] = match_vb.group(1)
            datos_extraidos["benchmark_ninjabet"]["valuebet_muestra"] = match_vb.group(2)

        # 2. Extraer Feed de Bonos / Ofertas Recurrentes (Matched Betting)
        # Usamos selectores flexibles de Scrapling para capturar tarjetas de bonos
        patrones_bonos = re.findall(
            r"(Bono de Bienvenida|Club Apuestas Gratis|Oferta Cashback|Apuesta Gratis|Reembolso).*?Ganancia esperada.*?(€\s*\d+).*?Tipo.*?(Registro|Recarga|Recurrente)",
            texto_completo,
             flags=re.DOTALL
        )
        for nombre_bono, ganancia_eur, tipo_bono in patrones_bonos:
            datos_extraidos["bonos_matched_betting"].append({
                "titulo": nombre_bono.strip(),
                "ganancia_estimada_eur": ganancia_eur.strip(),
                "categoria": tipo_bono.strip()
            })

        # 3. Extraer oportunidades visibles del bloque MultiOdds / Comparador
        bloques_partidos = page.css("div, li")
        for bloque in bloques_partidos:
            txt = bloque.text or ""
            if " vs " in txt and any(casa in txt for casa in ["Bet365", "Codere", "Sportium", "Betfair", "Bwin", "William Hill"]):
                cuota_match = re.search(r"\b([1-9]\.\d{2})\b", txt)
                if cuota_match and len(txt) < 140:
                    datos_extraidos["multiodds_muestra"].append({
                        "detalle_crudo": " ".join(txt.split()),
                        "cuota_detectada": float(cuota_match.group(1))
                    })

    return datos_extraidos


def interceptar_api_dutcher_stealth(cookies_dict=None):
    """
    Usa StealthySession de Scrapling para saltar Cloudflare Turnstile e inspeccionar
    las tablas dinámicas o endpoints XHR/Ajax del Dutcher de NinjaBet.
    """
    oportunidades_dutcher = []

    with StealthySession(headless=True, solve_cloudflare=True, network_idle=True) as stealth_session:
        page = stealth_session.fetch(URL_NINJABET_DUTCHER)

        # Con adaptive=True, Scrapling recuerda la estructura de la tabla aunque cambie el CSS
        filas_tabla = page.css("table tr, .dutcher-row, .oddsmatcher-row", adaptive=True)

        for fila in filas_tabla:
            celdas = [c.text.strip() for c in fila.css("td, .cell") if c.text]
            if len(celdas) >= 4:
                oportunidades_dutcher.append(celdas)

    return oportunidades_dutcher


# ==============================================================================
# 2. MOTOR "DUTCHER" PROPIO DE IABET (CALCULADORA DE SUREBETS Y REDONDEO)
# ==============================================================================
def calcular_dutcher_iabet(evento, liga, mercado_a, cuota_a, casa_a, mercado_b, cuota_b, casa_b, bankroll=100.0):
    """
    Replica la lógica del Dutcher de NinjaBet dentro de IABET:
    Verifica si dos cuotas opuestas generan una SureBet (margen < 1.0) y calcula
    los stakes exactos y redondeados para evitar bloqueos (gubbings) en Perú.
    """
    inv_a = 1.0 / cuota_a
    inv_b = 1.0 / cuota_b
    suma_inversas = inv_a + inv_b

    # Si suma_inversas < 1.0, existe ganancia matemática garantizada (SureBet)
    if suma_inversas >= 1.0:
        rating_pct = (1.0 / suma_inversas) * 100.0
        return {
            "es_surebet": False,
            "rating_ninjabet_pct": round(rating_pct, 2),
            "mensaje": f"Rating {rating_pct:.2f}% (Útil para liberar bonos / Rollover, no es SureBet pura)"
        }

    # Cálculo exacto de Stakes (proporcional a la probabilidad implícita)
    stake_a_exacto = bankroll * (inv_a / suma_inversas)
    stake_b_exacto = bankroll * (inv_b / suma_inversas)

    # Redondeo de seguridad antifraude (a enteros o múltiplos de S/ 1.00)
    stake_a_red = round(stake_a_exacto)
    stake_b_red = bankroll - stake_a_red

    retorno_si_gana_a = stake_a_red * cuota_a
    retorno_si_gana_b = stake_b_red * cuota_b
    beneficio_min_garantizado = min(retorno_si_gana_a, retorno_si_gana_b) - bankroll
    roi_garantizado_pct = (beneficio_min_garantizado / bankroll) * 100.0

    return {
        "es_surebet": True,
        "evento": evento,
        "liga": liga,
        "rating_ninjabet_pct": round((1.0 / suma_inversas) * 100.0, 2),
        "roi_garantizado_pct": round(roi_garantizado_pct, 2),
        "beneficio_neto_soles": round(beneficio_min_garantizado, 2),
        "apuesta_1": {
            "casa": casa_a,
            "seleccion": mercado_a,
            "cuota": cuota_a,
            "stake_sugerido_soles": stake_a_red,
            "retorno_bruto": round(retorno_si_gana_a, 2)
        },
        "apuesta_2": {
            "casa": casa_b,
            "seleccion": mercado_b,
            "cuota": cuota_b,
            "stake_sugerido_soles": stake_b_red,
            "retorno_bruto": round(retorno_si_gana_b, 2)
        }
    }


if __name__ == "__main__":
    print("🚀 Iniciando extracción furtiva con Scrapling en NinjaBet.es...")
    intel = extraer_datos_publicos_y_bonos_ninjabet()

    # Ejemplo de cruce con nuestro Dutcher de IABET (Caso real Over/Under 2.5 entre 2 casas)
    ejemplo_dutcher = calcular_dutcher_iabet(
        evento="Universitario vs Alianza Lima",
        liga="Liga 1 Perú",
        mercado_a="Más de 2.5 Goles",
        cuota_a=2.15,
        casa_a="Betsson (Kambi)",
        mercado_b="Menos de 2.5 Goles",
        cuota_b=1.98,
        casa_b="Betano / Apuesta Total",
        bankroll=BANKROLL_SUREBET
    )
    intel["ejemplo_dutcher_calculado"] = ejemplo_dutcher

    with open(ARCHIVO_SALIDA_NINJA, "w", encoding="utf-8") as f:
        json.dump(intel, f, ensure_ascii=False, indent=2)

    print(f"✅ Extracción completada. Datos guardados en {ARCHIVO_SALIDA_NINJA}")
    print(json.dumps(ejemplo_dutcher, indent=2, ensure_ascii=False))
    
