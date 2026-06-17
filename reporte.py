import os
import json
import requests
from datetime import datetime, timedelta
import gspread
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
SHEET_ID          = "1F3SP6ZRFqXG5FA1md4AIF2v_tVEvPbZX7tT7TfYjIog"
GHL_SHEET_ID = "1yd2IKXL_BMicw-8kOEObQBuiCQLNYbtlIa4sur-STig"
CLARITY_TOKEN     = os.getenv("CLARITY_TOKEN")
CLARITY_PROJECT   = "w88ytv9ego"
CLICKUP_CHANNEL   = "rk6q9-12737"
CLICKUP_TOKEN     = os.getenv("CLICKUP_TOKEN")
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def get_gspread_client():
    token_info = json.loads(GOOGLE_TOKEN_JSON)
    creds = Credentials(
        token=token_info.get("token"),
        refresh_token=token_info.get("refresh_token"),
        token_uri=token_info.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_info.get("client_id"),
        client_secret=token_info.get("client_secret"),
        scopes=SCOPES,
    )
    return gspread.authorize(creds)

# ── GA4 desde Google Sheets ──────────────────────────────────────────────────
def get_ga4_data(gc):
    sh = gc.open_by_key(SHEET_ID)

    ws_ayer = sh.worksheet("LP Junio 2026 - Ayer")
    data_ayer = ws_ayer.get_all_values()

    ws_7d = sh.worksheet("LP Junio 2026 - 7 dias")
    data_7d = ws_7d.get_all_values()

    def parse_totals(data):
        totals = {"sessions": 0, "activeUsers": 0, "conversion_gratuito": 0, "conversion_upsell": 0}
        for i, row in enumerate(data):
            if "sessions" in row or "Sessions" in row:
                try:
                    vals = data[i + 1]
                    totals["sessions"]             = int(float(vals[3])) if len(vals) > 3 and vals[3] else 0
                    totals["activeUsers"]          = int(float(vals[4])) if len(vals) > 4 and vals[4] else 0
                    totals["conversion_gratuito"]  = int(float(vals[5])) if len(vals) > 5 and vals[5] else 0
                    totals["conversion_upsell"]    = int(float(vals[6])) if len(vals) > 6 and vals[6] else 0
                except (ValueError, IndexError):
                    pass
                break
        return totals

    def parse_sources(data):
        sources = []
        header_found = False
        for row in data:
            if "pagePath" in row or "Page Path" in row:
                header_found = True
                continue
            if header_found and len(row) > 3 and row[0] and row[1]:
                try:
                    sessions = int(float(row[3])) if row[3] else 0
                    sources.append({"source": row[1], "device": row[2], "sessions": sessions})
                except ValueError:
                    pass
        sources.sort(key=lambda x: x["sessions"], reverse=True)
        return sources[:3]

    return {
        "ayer": parse_totals(data_ayer),
        "siete_dias": parse_totals(data_7d),
        "top_fuentes": parse_sources(data_ayer),
    }

# ── GHL desde Google Sheets ──────────────────────────────────────────────────
def get_ghl_data(gc):
    sh = sh = gc.open_by_key(GHL_SHEET_ID)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    def contar_filas(worksheet_name, col_fecha=1):
        """Cuenta filas totales y de ayer en una pestaña dado el índice de columna de fecha (0-based)."""
        try:
            ws = sh.worksheet(worksheet_name)
            rows = ws.get_all_values()
            total = 0
            ayer = 0
            for row in rows[1:]:  # skip header
                if len(row) > col_fecha and row[col_fecha]:
                    total += 1
                    fecha_str = row[col_fecha].strip()
                    # Intenta varios formatos de fecha
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
                        try:
                            fecha = datetime.strptime(fecha_str[:10], fmt[:10])
                            if fecha.strftime("%Y-%m-%d") == yesterday:
                                ayer += 1
                            break
                        except ValueError:
                            continue
            return {"total": total, "ayer": ayer}
        except Exception as e:
            print(f"Error leyendo {worksheet_name}: {e}")
            return {"total": 0, "ayer": 0}

    registros_ads  = contar_filas("ADS | Regisros The Office", col_fecha=1)
    registros_org  = contar_filas("ORG | Regisros The Office", col_fecha=1)
    ventas_vip     = contar_filas("Ventas UPSELL VIP", col_fecha=1)
    pagos_fallidos = contar_filas("Pagos Fallidos UPSELL", col_fecha=1)

    return {
        "registros_ads_ayer":  registros_ads["ayer"],
        "registros_org_ayer":  registros_org["ayer"],
        "registros_ads_total": registros_ads["total"],
        "registros_org_total": registros_org["total"],
        "ventas_ayer":         ventas_vip["ayer"],
        "ventas_total":        ventas_vip["total"],
        "pagos_fallidos_ayer": pagos_fallidos["ayer"],
        "pagos_fallidos_total":pagos_fallidos["total"],
    }

# ── Clarity ──────────────────────────────────────────────────────────────────
def get_clarity_data():
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    url = "https://www.clarity.ms/export-data/api/v1/project-live-insights"
    headers = {"Authorization": f"Bearer {CLARITY_TOKEN}"}
    params = {
        "projectId": CLARITY_PROJECT,
        "startDate": yesterday,
        "endDate": yesterday,
        "dimension": "All",
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            result = {"scroll_depth": 0, "dead_clicks": 0, "rage_clicks": 0, "sessions": 0}
            for item in data:
                metric = item.get("metricName")
                info = item.get("information", [{}])[0]
                if metric == "DeadClickCount":
                    result["dead_clicks"] = int(info.get("subTotal", 0))
                    result["sessions"]    = int(info.get("sessionsCount", 0))
                    result["scroll_depth"] = round(float(info.get("sessionsWithoutMetricPercentage", 0)), 1)
                elif metric == "RageClickCount":
                    result["rage_clicks"] = int(info.get("subTotal", 0))
            return result
    except Exception as e:
        print(f"Clarity error: {e}")
    return {"scroll_depth": 0, "dead_clicks": 0, "rage_clicks": 0, "sessions": 0}

# ── Acciones ─────────────────────────────────────────────────────────────────
def generar_acciones(ga4, clarity, ghl):
    acciones = {"urgente": [], "medio": [], "bajo": []}
    ayer = ga4["ayer"]

    registros_ayer = ghl["registros_ads_ayer"] + ghl["registros_org_ayer"]
    tasa = (registros_ayer / ayer["sessions"] * 100) if ayer["sessions"] > 0 else 0
    upsell_rate = (ghl["ventas_ayer"] / registros_ayer * 100) if registros_ayer > 0 else 0

    if registros_ayer == 0 and ayer["sessions"] > 50:
        acciones["urgente"].append("0 registros nuevos ayer — revisar formulario y flujo de registro")
    elif tasa < 10 and ayer["sessions"] > 50:
        acciones["urgente"].append(f"Tasa de conversión baja ({tasa:.1f}%) — revisar CTA y formulario en LP")

    if clarity["rage_clicks"] > 10:
        acciones["urgente"].append(f"🔴 {clarity['rage_clicks']} rage clicks — posible elemento roto en la página")

    if clarity["dead_clicks"] > 20:
        acciones["medio"].append(f"{clarity['dead_clicks']} dead clicks — revisar elementos no interactivos")

    if clarity["scroll_depth"] < 40:
        acciones["medio"].append(f"Scroll depth bajo ({clarity['scroll_depth']}%) — hero section puede no estar enganchando")

    if ghl["pagos_fallidos_ayer"] > 0:
        acciones["medio"].append(f"{ghl['pagos_fallidos_ayer']} pago(s) fallido(s) ayer — seguimiento con Jenn")

    if upsell_rate < 5 and registros_ayer > 10:
        acciones["medio"].append(f"Tasa de upsell baja ({upsell_rate:.1f}%) — revisar página de upsell")

    if not acciones["urgente"] and not acciones["medio"]:
        acciones["bajo"].append("Todo en parámetros normales — continuar monitoreando")

    return acciones, tasa, upsell_rate

# ── Mensaje ──────────────────────────────────────────────────────────────────
def build_message(ga4, clarity, ghl, acciones, tasa, upsell_rate):
    ayer = ga4["ayer"]
    siete = ga4["siete_dias"]
    hoy = datetime.now().strftime("%d/%m/%Y")

    registros_ayer  = ghl["registros_ads_ayer"] + ghl["registros_org_ayer"]
    registros_total = ghl["registros_ads_total"] + ghl["registros_org_total"]

    fuentes_txt = ""
    for f in ga4["top_fuentes"]:
        fuentes_txt += f"\n    • {f['source']} ({f['device']}): {f['sessions']} sesiones"

    urgente_txt = "\n".join(f"  🔴 {a}" for a in acciones["urgente"]) or "  ✅ Sin alertas urgentes"
    medio_txt   = "\n".join(f"  🟡 {a}" for a in acciones["medio"])   or "  ✅ Sin alertas medias"
    bajo_txt    = "\n".join(f"  🟢 {a}" for a in acciones["bajo"])    or ""

    msg = f"""📊 *THE OFFICE JUN 2026 | Reporte Diario*
📅 {hoy}

━━━━━━━━━━━━━━━━━━━
📋 GHL — REGISTROS Y VENTAS
━━━━━━━━━━━━━━━━━━━
AYER:
• Registros ADS:      {ghl['registros_ads_ayer']}
• Registros ORG:      {ghl['registros_org_ayer']}
• Total registros:    {registros_ayer}
• Ventas Speaker Pro: {ghl['ventas_ayer']}
• Pagos fallidos:     {ghl['pagos_fallidos_ayer']}
• Tasa conversión:    {tasa:.1f}%
• Tasa upsell:        {upsell_rate:.1f}%

ACUMULADO TOTAL:
• Registros ADS:      {ghl['registros_ads_total']}
• Registros ORG:      {ghl['registros_org_total']}
• Total registros:    {registros_total}
• Ventas Speaker Pro: {ghl['ventas_total']}
• Pagos fallidos:     {ghl['pagos_fallidos_total']}

━━━━━━━━━━━━━━━━━━━
📈 GA4 — TRÁFICO LP AYER
━━━━━━━━━━━━━━━━━━━
• Sesiones:           {ayer['sessions']}
• Usuarios activos:   {ayer['activeUsers']}

Top fuentes:{fuentes_txt}

━━━━━━━━━━━━━━━━━━━
📈 GA4 — TRÁFICO LP 7 DÍAS
━━━━━━━━━━━━━━━━━━━
• Sesiones:           {siete['sessions']}
• Usuarios activos:   {siete['activeUsers']}

━━━━━━━━━━━━━━━━━━━
🎯 CLARITY — AYER
━━━━━━━━━━━━━━━━━━━
• Sesiones grabadas:  {clarity['sessions']}
• Scroll depth prom:  {clarity['scroll_depth']}%
• Dead clicks:        {clarity['dead_clicks']}
• Rage clicks:        {clarity['rage_clicks']}

━━━━━━━━━━━━━━━━━━━
🚦 ACCIONES A TOMAR
━━━━━━━━━━━━━━━━━━━
URGENTE:
{urgente_txt}

MEDIO:
{medio_txt}
{bajo_txt}
"""
    return msg

# ── Enviar a ClickUp ─────────────────────────────────────────────────────────
def send_to_clickup(message):
    url = f"https://api.clickup.com/api/v2/view/{CLICKUP_CHANNEL}/comment"
    headers = {
        "Authorization": CLICKUP_TOKEN,
        "Content-Type": "application/json",
    }
    payload = {"comment_text": message, "notify_all": True}
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    if r.status_code in (200, 201):
        print("✅ Reporte enviado a ClickUp")
    else:
        print(f"❌ Error ClickUp: {r.status_code} — {r.text}")

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Conectando a Google Sheets...")
    gc = get_gspread_client()

    print("Obteniendo datos de GA4...")
    ga4 = get_ga4_data(gc)

    print("Obteniendo datos de GHL (Sheets)...")
    ghl = get_ghl_data(gc)

    print("Obteniendo datos de Clarity...")
    clarity = get_clarity_data()

    print("Generando acciones...")
    acciones, tasa, upsell_rate = generar_acciones(ga4, clarity, ghl)

    print("Construyendo mensaje...")
    msg = build_message(ga4, clarity, ghl, acciones, tasa, upsell_rate)
    print(msg)

    print("Enviando a ClickUp...")
    send_to_clickup(msg)