# ============================================================
#   WHATSAPP BOT + FASTAPI + OPENAI + SQLITE
#   Escuela de Idiomas del Ejército - Filial Santa Cruz
#   Archivo totalmente corregido y unificado
# ============================================================

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import PlainTextResponse, JSONResponse, StreamingResponse
import os, json, re, requests, csv
from io import StringIO
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, func, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timedelta
from typing import List, Tuple
from openai import OpenAI
import traceback

# ============================================================
#                 1) INICIALIZACIÓN FASTAPI
# ============================================================
app = FastAPI(
    title="Escuela de Idiomas - WhatsApp Bot",
    version="2.0.0",
    description="Bot de WhatsApp conectado a WhatsApp Cloud API + IA + SQLite"
)

# ============================================================
#                     2) VARIABLES .ENV
# ============================================================
load_dotenv()

WHATSAPP_TOKEN      = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID     = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN        = os.getenv("VERIFY_TOKEN", "mi-token-secreto-verificacion")

OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
SHEET_WEBHOOK       = os.getenv("SHEET_WEBHOOK", "")

ORG_NAME        = os.getenv("ORG_NAME", "Escuela de Idiomas del Ejército - Filial Santa Cruz")
CITY            = os.getenv("CITY", "Santa Cruz")
ADDRESS         = os.getenv("ADDRESS", "FINAL Calle Taperas, 2do. Anillo detrás del INE, Santa Cruz")
GOOGLE_MAPS_LINK= os.getenv("GOOGLE_MAPS_LINK", "https://maps.app.goo.gl/TRStYJHnt6U5urkr6")
CONTACT_PHONE   = os.getenv("CONTACT_PHONE", "+59178024823")
CONTACT_EMAIL   = os.getenv("CONTACT_EMAIL", "idiomas.scz@emi.edu.bo")

OPENING_HOURS   = os.getenv("OPENING_HOURS", "Lun–Vie 08:00–17:00")
COURSES         = [c.strip() for c in os.getenv("COURSES", "Inglés,Chino,Francés,Portugués").split(",")]
LEVELS          = [l.strip() for l in os.getenv("LEVELS", "A1,A2,B1,B2").split(",")]
PRICES          = os.getenv("PRICES", "Matrícula Bs 230; Mensualidad Bs 330; Material Bs 70")
PAYMENT_METHODS = os.getenv("PAYMENT_METHODS", "Efectivo, Transferencia, QR")
CONTENT_PATH    = os.getenv("CONTENT_PATH", "content.json")
ADMIN_TOKEN     = os.getenv("ADMIN_TOKEN", "super-seguro-123")
ADMIN_WHATSAPP  = os.getenv("ADMIN_WHATSAPP", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

if not (WHATSAPP_TOKEN and PHONE_NUMBER_ID and VERIFY_TOKEN):
    raise RuntimeError("❌ ERROR: faltan variables .env necesarias")

# ============================================================
#                 3) CARGA content.json
# ============================================================
CONTENT = {}

def load_content():
    global CONTENT
    try:
        with open(CONTENT_PATH, "r", encoding="utf-8") as f:
            CONTENT = json.load(f)
        print("✅ content.json cargado correctamente")
    except:
        print("ℹ️ No existe content.json o está vacío")
        CONTENT = {}

load_content()

# ============================================================
#                      4) BASE DE DATOS
# ============================================================
DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Lead(Base):
    __tablename__ = "leads"
    id           = Column(Integer, primary_key=True)
    wa_from      = Column(String(32))
    name         = Column(String(128))
    intent       = Column(String(64))
    last_message = Column(Text)

class SessionState(Base):
    __tablename__ = "sessions"
    wa_from = Column(String(32), primary_key=True)
    state   = Column(String(64))
    data    = Column(Text)

class Enrollment(Base):
    __tablename__ = "enrollments"
    id            = Column(Integer, primary_key=True)
    wa_from       = Column(String(32))
    name          = Column(String(128))
    ci            = Column(String(32))
    course        = Column(String(64))
    level         = Column(String(16))
    schedule_pref = Column(String(64))
    ci_image_url  = Column(Text)
    confirmed     = Column(Boolean, default=False)
    created_at    = Column(DateTime, server_default=func.now())

Base.metadata.create_all(bind=engine)

# Helpers de sesión
def _get_session(db, phone):
    s = db.query(SessionState).get(phone)
    if not s:
        s = SessionState(wa_from=phone, state="idle", data="{}")
        db.add(s); db.commit()
    try:
        return s, json.loads(s.data or "{}")
    except:
        return s, {}

def _save_session(db, phone, state, payload):
    s = db.query(SessionState).get(phone)
    if not s:
        s = SessionState(wa_from=phone)
        db.add(s)
    s.state = state
    s.data  = json.dumps(payload)
    db.commit()

def _clear_session(db, phone):
    _save_session(db, phone, "idle", {})

# ============================================================
#             5) HELPERS WHATSAPP GRAPH API
# ============================================================
def send_whatsapp_text(to, message):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type":"application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    try:
        r = requests.post(url, headers=headers, json=data)
        if not r.ok:
            print("⚠️ Error al enviar mensaje:", r.text)
        return r.json()
    except Exception as e:
        print("❌ Error enviando mensaje:", e)

# ============================================================
#                 6) WEBHOOK - VERIFICACIÓN
# ============================================================
@app.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="Token incorrecto")
# ============================================================
#              7) IA – OpenAI ChatGPT
# ============================================================
openai_client = None
if OPENAI_API_KEY:
    try:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        print("✅ OpenAI inicializado")
    except Exception as e:
        print("⚠️ No se pudo iniciar OpenAI:", e)

def generate_ai_answer(user_text: str) -> str:
    """
    Si la IA está configurada, responde con GPT.
    Caso contrario, devuelve menú principal.
    """
    if not openai_client:
        return menu_principal()

    try:
        system_prompt = (
            f"Eres asistente de *{ORG_NAME}*.\n"
            f"Dirección: {ADDRESS}\n"
            f"Mapa: {GOOGLE_MAPS_LINK}\n"
            f"Horarios: {OPENING_HOURS}\n"
            f"Cursos: {', '.join(COURSES)}\n"
            f"Niveles: {', '.join(LEVELS)}\n"
            f"Precios: {PRICES}\n"
            f"Medios de pago: {PAYMENT_METHODS}\n"
            "Responde corto, amable, ≤6 líneas."
        )
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            temperature=0.3,
            messages=[
                {"role":"system","content":system_prompt},
                {"role":"user","content":user_text}
            ]
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print("❌ Error IA:", e)
        return menu_principal()

# ============================================================
#                 8) INTENCIONES + MENÚS
# ============================================================
def menu_principal():
    return (
        "📌 *MENÚ PRINCIPAL*\n"
        "1️⃣ Horarios y atención\n"
        "2️⃣ Cursos y niveles\n"
        "3️⃣ Precios y promociones\n"
        "4️⃣ Inscripciones\n"
        "5️⃣ Ubicación\n"
        "6️⃣ Contacto\n"
        "7️⃣ Medios de pago\n"
        "—\n"
        f"📚 {', '.join(COURSES)}\n"
        f"🎯 {', '.join(LEVELS)}"
    )

def mensaje_bienvenida():
    promo = CONTENT.get("org", {}).get("PROMOTION", "")
    line_promo = f"\n🎖️ *Promoción:* {promo}" if promo else ""

    return (
        "👋 *¡Bienvenido/a a la Escuela de Idiomas del Ejército – Filial SCZ!*\n\n"
        "Para ayudarte más rápido:\n"
        "• Escribe *3* para ver precios\n"
        "• Escribe *4* para inscribirte\n"
        "• O selecciona una opción del menú\n\n"
        f"📍 Dirección: {ADDRESS}\n"
        f"🕐 Horarios: {OPENING_HOURS}\n"
        f"{line_promo}\n"
        "👇 Menú:\n" + menu_principal()
    )

def detect_intent_rules(text: str) -> str:
    if not text:
        return "general"

    t = text.lower()

    # Reglas personalizables desde content.json
    rules = CONTENT.get("rules", {})
    for intent, palabras in rules.items():
        if any(p in t for p in palabras):
            return intent

    # Reglas automáticas
    reglas_auto = {
        "horarios": ["horario", "hora", "atención", "atienden"],
        "cursos": ["curso", "idioma", "clases", "nivel"],
        "precios": ["precio", "cuesta", "mensualidad", "inscripción"],
        "inscripciones": ["inscribir", "requisito", "matrícula"],
        "ubicacion": ["ubicación", "dónde", "direccion", "mapa"],
        "contacto": ["contacto", "teléfono", "email", "correo"],
        "pagos": ["pago", "transferencia", "qr", "efectivo", "cuenta"],
    }

    for intent, palabras in reglas_auto.items():
        if any(p in t for p in palabras):
            return intent

    # Atajos
    shortcuts = {"1":"horarios","2":"cursos","3":"precios","4":"inscripciones","5":"ubicacion","6":"contacto","7":"pagos"}
    return shortcuts.get(t, "general")

# ============================================================
#                9) RESPUESTAS POR INTENCIÓN
# ============================================================
def answer_for_intent(intent: str, payload: dict):
    intent = (intent or "").lower()

    custom_faq = CONTENT.get("faq", {})
    if custom_faq.get(intent):
        return custom_faq[intent]

    if intent == "horarios":
        return f"🕘 *Horarios:* {OPENING_HOURS}"

    if intent == "cursos":
        return f"📚 *Cursos:* {', '.join(COURSES)}\n🎯 *Niveles:* {', '.join(LEVELS)}"

    if intent == "precios":
        promo = CONTENT.get("org", {}).get("PROMOTION", "")
        line = f"\n🎖️ *Promoción:* {promo}" if promo else ""
        return f"💵 *Precios:* {PRICES}{line}"

    if intent == "inscripciones":
        pasos = CONTENT.get("catalog", {}).get("ENROLL_STEPS", [
            "Enviar foto de CI",
            "Llenar formulario",
            "Realizar pago",
            "Confirmación de aula"
        ])
        pasos_txt = "\n".join([f"- {p}" for p in pasos])
        return f"📝 *Inscripciones:*\n{pasos_txt}\n\n¿Deseas inscribirte ahora mismo? (sí/no)"

    if intent == "ubicacion":
        return f"📍 Dirección: {ADDRESS}\n📌 Mapa: {GOOGLE_MAPS_LINK}"

    if intent == "contacto":
        return f"☎️ Teléfonos: {CONTACT_PHONE}\n✉️ Email: {CONTACT_EMAIL}"

    if intent == "pagos":
        return f"💳 *Medios de pago:* {PAYMENT_METHODS}"

    return menu_principal()

# ============================================================
#           10) WEBHOOK — RECEPCIÓN DE MENSAJES
# ============================================================
@app.post("/webhook")
async def webhook_receiver(request: Request):
    """
    Procesa mensajes entrantes de WhatsApp Cloud API
    """
    body = await request.json()

    try:
        print("\n=== WEBHOOK ===")
        print(json.dumps(body, indent=2, ensure_ascii=False))
    except:
        pass

    try:
        entry   = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value   = changes.get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return {"status":"no_message"}

        msg     = messages[0]
        from_wa = msg.get("from")
        name    = value.get("contacts", [{}])[0].get("profile", {}).get("name", "")
        mtype   = msg.get("type")

        # --------------------------
        # EXTRAER TEXTO / BOTONES
        # --------------------------
        text = ""
        if mtype == "text":
            text = msg["text"]["body"].strip()
        elif mtype == "interactive":
            inter = msg.get("interactive", {})
            if "button_reply" in inter:
                text = inter["button_reply"]["title"]
            elif "list_reply" in inter:
                text = inter["list_reply"]["title"]

        tnorm = text.lower().strip() if text else ""

        # --------------------------
        # SESIÓN
        # --------------------------
        db = SessionLocal()
        sess, payload = _get_session(db, from_wa)
        payload["last_text"] = text
        _save_session(db, from_wa, sess.state, payload)

        # --------------------------
        # SALUDO
        # --------------------------
        if tnorm in {"hola","buenas","buenos días","buenas tardes","buenas noches"}:
            send_whatsapp_text(from_wa, mensaje_bienvenida())
            db.close()
            return {"status":"ok","flow":"saludo"}

        # --------------------------
        # INTENCIÓN
        # --------------------------
        intent = detect_intent_rules(text)
        payload["last_intent"] = intent
        _save_session(db, from_wa, sess.state, payload)

        # --------------------------
        # RESPUESTA CON REGLAS
        # --------------------------
        if intent != "general":
            response = answer_for_intent(intent, payload)
            send_whatsapp_text(from_wa, response)
            
            # Ubicación con pin
            if intent == "ubicacion":
                try:
                    send_whatsapp_location(from_wa, -17.776126747602, -63.167443644971414, ORG_NAME, ADDRESS)
                except:
                    pass

        else:
            # IA SOLO SI ES GENERAL
            response = generate_ai_answer(text)
            send_whatsapp_text(from_wa, response)

        # --------------------------
        # REGISTRAR LEAD
        # --------------------------
        lead = Lead(wa_from=from_wa, name=name, intent=intent, last_message=text)
        db.add(lead); db.commit()

        db.close()
        return {"status":"ok"}

    except Exception as e:
        print("❌ ERROR EN WEBHOOK:", e)
        print(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)
# ============================================================
#                11) VALIDACIONES Y UTILIDADES
# ============================================================

AFFIRM = {"si","sí","claro","ok","okay","vale","yes","de acuerdo","confirmo","acepto"}
NEGATE = {"no","nop","no gracias","cancelar","cancelado"}

ESCAPE_WORDS = {"menu", "menú", "inicio", "cancelar", "salir", "0"}

def is_valid_name(text: str) -> bool:
    return bool(re.search(r"[a-zA-ZÁÉÍÓÚÜÑáéíóúüñ]{2,}", text or ""))

def is_valid_ci(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9\-\.]{5,12}", (text or "").strip()))

def parse_day_time(text: str):
    """
    Interpreta: “lunes 10:00”, “mañana 8”, “9–11”, etc.
    Devuelve datetime o None.
    """
    if not text:
        return None

    t = text.lower().strip()
    now = datetime.now()

    # hoy / mañana
    m = re.search(r"(hoy|mañana)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if m:
        day, hh, mm, ap = m.groups()
        d = now + timedelta(days=0 if day == "hoy" else 1)
        h = int(hh)
        mnt = int(mm or 0)
        if ap == "pm" and h < 12:
            h += 12
        return d.replace(hour=h, minute=mnt, second=0, microsecond=0)

    # lunes 10:00
    DMAP = {
        "lun":0,"mar":1,"mie":2,"mié":2,"jue":3,"vie":4,"sab":5,"sáb":5,"dom":6,
        "lunes":0,"martes":1,"miércoles":2,"miercoles":2,"jueves":3,"viernes":4,"sábado":5,"domingo":6
    }

    m = re.search(r"(lun|mar|mi[eé]|jue|vie|s[aá]b|dom|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+(\d{1,2})(?::(\d{2}))?", t)
    if m:
        dayword, hh, mm = m.groups()
        key = dayword.lower()
        if key in DMAP:
            target_wd = DMAP[key]
            delta = (target_wd - now.weekday()) % 7
            d = now + timedelta(days=delta)
            return d.replace(hour=int(hh), minute=int(mm or 0), second=0, microsecond=0)

    # 10:00
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))\b", t)
    if m:
        hh, mm = m.groups()
        return now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)

    # 9-11
    m = re.search(r"\b([01]?\d|2[0-3])\s*[-–]\s*([01]?\d|2[0-3])\b", t)
    if m:
        a, _ = m.groups()
        return now.replace(hour=int(a), minute=0, second=0, microsecond=0)

    return None

def is_greeting(text: str) -> bool:
    return text.lower() in {
        "hola","buenas","buenos días","buen día","buenas tardes","buenas noches","saludos"
    }

# ============================================================
#       12) FLUJO DE RESERVAS (RESCHEDULED / CITA)
# ============================================================

@app.post("/reservas/start")
def start_reserva_test():
    return {"msg":"Este endpoint solo es para pruebas manuales."}

def start_reserva(db, phone, payload):
    _save_session(db, phone, "reserva_pide_nombre", payload)
    send_whatsapp_text(phone, "Perfecto. ¿Cuál es tu *nombre completo*?")
    return

# ============================================================
#                13) FLUJO DE INSCRIPCIONES
# ============================================================

def iniciar_inscripcion(db, phone, payload):
    payload["insc"] = {}
    _save_session(db, phone, "insc_pide_ci", payload)
    send_whatsapp_text(phone, "Perfecto. Para iniciar tu inscripción, envíame tu *CI* o foto del documento.")
    return


def continuar_flujo_inscripciones(msg, text, mtype, from_wa, name, db, sess, payload):
    """
    Se llama desde webhook principal.
    Maneja cada estado paso a paso.
    """

    # -----------------------------
    # 1) PEDIR CI
    # -----------------------------
    if sess.state == "insc_pide_ci":

        # Caso: envió foto del CI
        if mtype == "image":
            payload["insc"]["ci_image_url"] = "(foto recibida)"
            _save_session(db, from_wa, "insc_pide_nombre", payload)
            send_whatsapp_text(from_wa, "Recibido 👍 Ahora envíame tu *nombre completo*.")
            return True

        # Caso: CI escrito válido
        if is_valid_ci(text):
            payload["insc"]["ci"] = text.strip()
            _save_session(db, from_wa, "insc_pide_nombre", payload)
            send_whatsapp_text(from_wa, "Gracias. Ahora envíame tu *nombre completo*.")
            return True

        # Si no es CI → interpretar como nueva intención
        new_int = detect_intent_rules(text)
        _clear_session(db, from_wa)
        payload["last_intent"] = new_int
        _save_session(db, from_wa, "idle", payload)

        if new_int == "inscripciones":
            iniciar_inscripcion(db, from_wa, payload)
            return True

        resp = answer_for_intent(new_int, payload) if new_int != "general" else generate_ai_answer(text)
        send_whatsapp_text(from_wa, resp)

        lead = Lead(wa_from=from_wa, name=name, intent=new_int, last_message=text)
        db.add(lead); db.commit()
        return True

    # -----------------------------
    # 2) PEDIR NOMBRE
    # -----------------------------
    if sess.state == "insc_pide_nombre":
        if not is_valid_name(text):
            send_whatsapp_text(from_wa, "Por favor, envíame tu *nombre y apellido* (solo letras).")
            return True
        payload["insc"]["name"] = text.strip()
        _save_session(db, from_wa, "insc_pide_curso", payload)
        send_whatsapp_list(from_wa, "Elige el *idioma* que quieres estudiar:", "Idiomas", [(c, c) for c in COURSES])
        return True

    # -----------------------------
    # 3) PEDIR CURSO
    # -----------------------------
    if sess.state == "insc_pide_curso":
        chosen = None
        if mtype == "interactive" and "list_reply" in msg.get("interactive", {}):
            chosen = msg["interactive"]["list_reply"]["title"]

        if not chosen or chosen not in COURSES:
            send_whatsapp_list(from_wa, "Selecciona un *idioma* válido:", "Idiomas", [(c, c) for c in COURSES])
            return True

        payload["insc"]["course"] = chosen
        _save_session(db, from_wa, "insc_pide_nivel", payload)
        send_whatsapp_list(from_wa, "Selecciona tu *nivel*:", "Niveles", [(l, l) for l in LEVELS])
        return True

    # -----------------------------
    # 4) PEDIR NIVEL
    # -----------------------------
    if sess.state == "insc_pide_nivel":
        chosen = None
        if mtype == "interactive" and "list_reply" in msg.get("interactive", {}):
            chosen = msg["interactive"]["list_reply"]["title"]

        if not chosen or chosen not in LEVELS:
            send_whatsapp_list(from_wa, "Selecciona un *nivel* válido:", "Niveles", [(l, l) for l in LEVELS])
            return True

        payload["insc"]["level"] = chosen
        _save_session(db, from_wa, "insc_pide_hora", payload)
        send_whatsapp_text(from_wa, "¿Qué *horario* prefieres? (Ej: Mañanas / Tardes / Noches)")
        return True

    # -----------------------------
    # 5) PEDIR HORARIO
    # -----------------------------
    if sess.state == "insc_pide_hora":
        payload["insc"]["schedule_pref"] = text
        _save_session(db, from_wa, "insc_confirmar", payload)

        ins = payload["insc"]
        resumen = (
            "📋 *Resumen de inscripción:*\n"
            f"- CI: {ins.get('ci', '(foto)')}\n"
            f"- Nombre: {ins['name']}\n"
            f"- Curso: {ins['course']}\n"
            f"- Nivel: {ins['level']}\n"
            f"- Horario: {ins['schedule_pref']}\n\n"
            "¿Confirmas? (sí/no)"
        )
        send_whatsapp_text(from_wa, resumen)
        return True

    # -----------------------------
    # 6) CONFIRMAR INSCRIPCIÓN
    # -----------------------------
    if sess.state == "insc_confirmar":
        if text.lower() in AFFIRM:
            ins = payload["insc"]

            new_reg = Enrollment(
                wa_from=from_wa,
                name=ins["name"],
                ci=ins.get("ci", "(foto)"),
                course=ins["course"],
                level=ins["level"],
                schedule_pref=ins.get("schedule_pref", ""),
                ci_image_url=ins.get("ci_image_url")
            )
            db.add(new_reg); db.commit()

            send_whatsapp_text(from_wa, "🎉 ¡Inscripción registrada! Te contactaremos para confirmar aula y fecha.")
            _clear_session(db, from_wa)
            return True

        if text.lower() in NEGATE:
            send_whatsapp_text(from_wa, "Inscripción cancelada. Puedes iniciar nuevamente escribiendo *inscripción*.")
            _clear_session(db, from_wa)
            return True

        send_whatsapp_text(from_wa, "Por favor, responde *sí* o *no*.")
        return True

    return False  # No pertenece al flujo
# ============================================================
#        14) MENSAJES INTERACTIVOS (BOTONES y LISTAS)
# ============================================================

def send_whatsapp_buttons(to, body, buttons):
    """
    buttons = [("ID1","Texto1"), ("ID2","Texto2")]
    """
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}",
               "Content-Type": "application/json"}

    btn_list = []
    for bid, text in buttons:
        btn_list.append({
            "type": "reply",
            "reply": {"id": bid, "title": text}
        })

    data = {
        "messaging_product":"whatsapp",
        "to": to,
        "type":"interactive",
        "interactive":{
            "type":"button",
            "body":{"text": body},
            "action":{"buttons": btn_list}
        }
    }
    return requests.post(url, headers=headers, json=data).json()


def send_whatsapp_list(to, body, title, rows):
    """
    rows = [(id, title), (id, title)]
    """
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}",
               "Content-Type": "application/json"}

    list_rows = []
    for rid, text in rows:
        list_rows.append({"id": rid, "title": text})

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": title,
                "sections": [
                    {"title": title, "rows": list_rows}
                ]
            }
        }
    }
    return requests.post(url, headers=headers, json=data).json()


def send_whatsapp_location(to, lat, lng, name, address):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}",
               "Content-Type":"application/json"}

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "location",
        "location": {
            "latitude": float(lat),
            "longitude": float(lng),
            "name": name,
            "address": address
        }
    }

    return requests.post(url, headers=headers, json=data).json()

# ============================================================
#             15) EXPORTAR CSV (LEADS / INSCRIPCIONES)
# ============================================================

@app.get("/export/leads.csv")
def export_leads():
    db = SessionLocal()
    rows = db.query(Lead).order_by(Lead.id.desc()).all()
    db.close()

    f = StringIO()
    w = csv.writer(f)
    w.writerow(["id","phone","name","intent","last_message"])
    for r in rows:
        w.writerow([r.id, r.wa_from, r.name, r.intent, r.last_message])
    f.seek(0)

    return StreamingResponse(
        f, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"}
    )


@app.get("/export/enrollments.csv")
def export_enrollments():
    db = SessionLocal()
    rows = db.query(Enrollment).order_by(Enrollment.id.desc()).all()
    db.close()

    f = StringIO()
    w = csv.writer(f)
    w.writerow(["id","wa_from","name","ci","course","level","schedule_pref","created_at"])
    for r in rows:
        w.writerow([
            r.id, r.wa_from, r.name, r.ci, r.course,
            r.level, r.schedule_pref, r.created_at
        ])
    f.seek(0)

    return StreamingResponse(
        f, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=enrollments.csv"}
    )

# ============================================================
#                       16) ADMIN
# ============================================================

@app.post("/admin/reload")
def admin_reload(x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="No autorizado")
    load_content()
    return {"ok":True,"msg":"Contenido recargado"}


@app.post("/admin/override")
async def admin_override(req: Request, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="No autorizado")

    data = await req.json()

    if data.get("faq"):
        CONTENT.setdefault("faq", {}).update(data["faq"])
    if data.get("rules"):
        CONTENT.setdefault("rules", {}).update(data["rules"])

    return {"ok":True, "msg":"Datos modificados"}

# ============================================================
#                        17) ROOT
# ============================================================

@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "WhatsApp Bot funcionando correctamente",
        "version": "2.0.0",
        "endpoints": [
            "/webhook",
            "/test",
            "/admin/reload",
            "/admin/override",
            "/export/leads.csv",
            "/export/enrollments.csv",
        ]
    }

# ============================================================
#                        18) TEST
# ============================================================

@app.get("/test")
def test():
    return {
        "message": "Bot funcionando correctamente en Render",
        "org": ORG_NAME,
        "courses": COURSES,
        "levels": LEVELS
    }
