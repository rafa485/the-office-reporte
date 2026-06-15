import os
import json
import requests
from datetime import datetime, timedelta
import gspread
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
SHEET_ID         = "1F3SP6ZRFqXG5FA1md4AIF2v_tVEvPbZX7tT7TfYjIog"
CLARITY_TOKEN    = os.getenv("CLARITY_TOKEN")
CLARITY_PROJECT  = "w88ytv9ego"
CLICKUP_CHANNEL  = "rk6q9-12737"
CLICKUP_TOKEN    = os.getenv("CLICKUP_TOKEN")
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ── Google Sheets ────────────────────────────────────────────────────────────
def get_ga4_data():
    token_info = json.loads(GOOGLE_TOKEN_JSON)
    creds = Credentials(
        token=token_info.get("token"),
        refresh_token=token_info.get("refresh_token"),
        token_uri=token_info.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_info.get("client_id"),
        client_secret=token_info.get("client_secret"),
        scopes=SCOPES,
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    # Pestaña de ayer
    ws_ayer = sh.worksheet("LP Junio 2026 - Ayer")
    data_ayer = ws_ayer.get_all_values()

    # Pestaña de 7 días
    ws_7d = sh.worksheet("LP Junio 2026 - 7 dias")
    data_7d = ws_7d.get_all_values()

    def parse_totals(data):
        """Encuentra la fila de totales en el reporte GA4."""
        totals = {"sessions": 0, "activeUsers": 0, "conversion_gratuito": 0, "conversion_upsell": 0}
        for i, row in enumerate(data):
            if "sessions" in row or "Sessions" in row:
                # siguiente fila son los valores
                try:
                    vals = data[i + 1]
                    totals["sessions"] = int(float(vals[3])) if len(vals) > 3 and vals[3] else 0
                    totals["activeUsers"] = int(float(vals[4])) if len(vals) > 4 and vals[4] else 0
                    totals["conversion_gratuito"] = int(float(vals[5])) if len(vals) > 5 and vals[5] else 0
                    totals["conversion_upsell"] = int(float(vals[6])) if len(vals) > 6 and vals[6] else 0
                except (ValueError, IndexError):
                    pass
                break
        return totals

    def parse_sources(data):
        """Extrae top 3 fuentes por sesiones."""
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
                    result["sessions"] = int(info.get("sessionsCount", 0))
                    result["scroll_depth"] = round(float(info.get("sessionsWithoutMetricPercentage", 0)), 1)
                elif metric == "RageClickCount":
                    result["rage_clicks"] = int(info.get("subTotal", 0))
            return result
    except Exception as e:
        print(f"Clarity error: {e}")
    return {"scroll_depth": 0, "dead_clicks": 0, "rage_clicks": 0, "sessions": 0}

# ── Acciones por tier ────────────────────────────────────────────────────────
def generar_acciones(ga4, clarity):
    acciones = {"urgente": [], "medio": [], "bajo": []}
    ayer = ga4["ayer"]
    siete = ga4["siete_dias"]

    # Tasa de conversión
    tasa = (ayer["conversion_gratuito"] / ayer["sessions"] * 100) if ayer["sessions"] > 0 else 0

    if tasa == 0 and ayer["sessions"] > 50:
        acciones["urgente"].append("⚠️ 0 conversiones registradas — verificar que el evento registro_completado esté disparando en GTM")
    elif tasa < 10 and ayer["sessions"] > 50:
        acciones["urgente"].append(f"Tasa de conversión baja ({tasa:.1f}%) — revisar CTA y formulario en LP")

    if clarity["rage_clicks"] > 10:
        acciones["urgente"].append(f"🔴 {clarity['rage_clicks']} rage clicks — posible elemento roto en la página")

    if clarity["scroll_depth"] < 40:
        acciones["medio"].append(f"Scroll depth bajo ({clarity['scroll_depth']}%) — hero section puede no estar enganchando")

    if clarity["dead_clicks"] > 20:
        acciones["medio"].append(f"{clarity['dead_clicks']} dead clicks — revisar elementos no interactivos que confunden al usuario")

    upsell_rate = (ayer["conversion_upsell"] / ayer["conversion_gratuito"] * 100) if ayer["conversion_gratuito"] > 0 else 0
    if upsell_rate < 5 and ayer["conversion_gratuito"] > 10:
        acciones["medio"].append(f"Tasa de upsell baja ({upsell_rate:.1f}%) — revisar página de upsell y oferta VIP")

    if not acciones["urgente"] and not acciones["medio"]:
        acciones["bajo"].append("Todo en parámetros normales — continuar monitoreando")

    return acciones, tasa, upsell_rate

# ── Mensaje ClickUp ──────────────────────────────────────────────────────────
def build_message(ga4, clarity, acciones, tasa, upsell_rate):
    ayer = ga4["ayer"]
    siete = ga4["siete_dias"]
    hoy = datetime.now().strftime("%d/%m/%Y")

    fuentes_txt = ""
    for f in ga4["top_fuentes"]:
        fuentes_txt += f"\n    • {f['source']} ({f['device']}): {f['sessions']} sesiones"

    urgente_txt = "\n".join(f"  🔴 {a}" for a in acciones["urgente"]) or "  ✅ Sin alertas urgentes"
    medio_txt   = "\n".join(f"  🟡 {a}" for a in acciones["medio"])   or "  ✅ Sin alertas medias"
    bajo_txt    = "\n".join(f"  🟢 {a}" for a in acciones["bajo"])    or ""

    msg = f"""📊 *THE OFFICE JUN 2026 | Reporte Diario LP*
📅 {hoy}

━━━━━━━━━━━━━━━━━━━
📈 GA4 — AYER
━━━━━━━━━━━━━━━━━━━
• Sesiones LP:        {ayer['sessions']}
• Usuarios activos:   {ayer['activeUsers']}
• Registros gratuitos: {ayer['conversion_gratuito']}
• Ventas Speaker Pro: {ayer['conversion_upsell']}
• Tasa conversión LP: {tasa:.1f}%
• Tasa upsell:        {upsell_rate:.1f}%

Top fuentes:{fuentes_txt}

━━━━━━━━━━━━━━━━━━━
📅 GA4 — ÚLTIMOS 7 DÍAS
━━━━━━━━━━━━━━━━━━━
• Sesiones LP:        {siete['sessions']}
• Usuarios activos:   {siete['activeUsers']}
• Registros gratuitos: {siete['conversion_gratuito']}
• Ventas Speaker Pro: {siete['conversion_upsell']}

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
    print("Obteniendo datos de GA4...")
    ga4 = get_ga4_data()

    print("Obteniendo datos de Clarity...")
    clarity = get_clarity_data()

    print("Generando acciones...")
    acciones, tasa, upsell_rate = generar_acciones(ga4, clarity)

    print("Construyendo mensaje...")
    msg = build_message(ga4, clarity, acciones, tasa, upsell_rate)
    print(msg)

    print("Enviando a ClickUp...")
    send_to_clickup(msg)