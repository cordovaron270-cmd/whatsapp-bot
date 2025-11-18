# app.py
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import PlainTextResponse, JSONResponse, StreamingResponse
import os, json, re, requests, csv
from io import StringIO
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, func, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timedelta
from typing import List, Tuple
from fastapi import FastAPI, Request, Response
import os
import json

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mi-token-secreto-verificacion")

# ================================
# WEBHOOK - VERIFICACIÓN (GET)
# ================================
@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Error de verificación", media_type="text/plain")

# ================================
# WEBHOOK - MENSAJES (POST)
# ================================
@app.post("/webhook")
async def webhook_receiver(request: Request):
    data = await request.json()

    # Mostrar el mensaje recibido en los logs
    print("📩 WEBHOOK RECIBIDO:")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    return {"status": "ok"}

# ===================== 1) .ENV Y VARIABLES BASE =====================
load_dotenv()

WHATSAPP_TOKEN      = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID     = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN        = os.getenv("VERIFY_TOKEN")

OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
SHEET_WEBHOOK       = os.getenv("SHEET_WEBHOOK", "")

ORG_NAME        = os.getenv("ORG_NAME", "Escuela de Idiomas del Ejército - Filial Santa Cruz")
CITY            = os.getenv("CITY", "Santa Cruz")
ADDRESS         = os.getenv("ADDRESS", "FINAL Calle Taperas 2do. Anillo detrás del INE 591, Santa Cruz de la Sierra, Bolivia")
GOOGLE_MAPS_LINK= os.getenv("GOOGLE_MAPS_LINK", "https://maps.app.goo.gl/TRStYJHnt6U5urkr6")
CONTACT_PHONE   = os.getenv("CONTACT_PHONE", "+591 78024823 / +591 67601808")
CONTACT_EMAIL   = os.getenv("CONTACT_EMAIL", "idiomas.scz@emi.edu.bo")

OPENING_HOURS   = os.getenv("OPENING_HOURS", "Lun–Vie 08:00–17:00")
COURSES         = [c.strip() for c in os.getenv("COURSES", "Inglés,Chino,Francés,Portugués,Quechua,Aymara").split(",")]
LEVELS          = [l.strip() for l in os.getenv("LEVELS", "A1,A2,B1,B2,C1,C2").split(",")]
PRICES          = os.getenv("PRICES", "Matricula Bs 230 una vez al año; Mensualidad Bs 330; Material Bs 70")
DISCOUNTS       = os.getenv("DISCOUNTS", "")
ENROLL_STEPS    = [p.strip() for p in os.getenv("ENROLL_STEPS", "Enviar foto de CI; Llenar formulario digital; Pago (QR o caja); Confirmación de aula y horario").split(";")]
PAYMENT_METHODS = os.getenv("PAYMENT_METHODS", "Efectivo, Transferencia, QR")

if not (WHATSAPP_TOKEN and PHONE_NUMBER_ID and VERIFY_TOKEN):
    raise RuntimeError("Faltan variables .env: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, VERIFY_TOKEN")

# ===================== 2) CONTENT.JSON + ADMIN =====================
CONTENT_PATH  = os.getenv("CONTENT_PATH", "content.json")
ADMIN_TOKEN   = os.getenv("ADMIN_TOKEN", "super-seguro-123")
ADMIN_WHATSAPP= os.getenv("ADMIN_WHATSAPP", "")  # ej. 5917XXXXXXX sin +
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

CONTENT = {}

def load_content():
    """Carga/mezcla content.json sobre las variables existentes."""
    global CONTENT, ORG_NAME, CITY, ADDRESS, GOOGLE_MAPS_LINK, OPENING_HOURS
    global PRICES, DISCOUNTS, PAYMENT_METHODS, COURSES, LEVELS, ENROLL_STEPS

    try:
        with open(CONTENT_PATH, "r", encoding="utf-8") as f:
            CONTENT = json.load(f)

        org     = CONTENT.get("org", {})
        catalog = CONTENT.get("catalog", {})

        ORG_NAME        = org.get("ORG_NAME", ORG_NAME)
        CITY            = org.get("CITY", CITY)
        ADDRESS         = org.get("ADDRESS", ADDRESS)
        GOOGLE_MAPS_LINK= org.get("GOOGLE_MAPS_LINK", GOOGLE_MAPS_LINK)
        OPENING_HOURS   = org.get("OPENING_HOURS", OPENING_HOURS)
        PRICES          = org.get("PRICES", PRICES)
        PROMOTION       = org.get("PROMOTION", "")
        if PROMOTION:
            CONTENT.setdefault("org", {})["PROMOTION"] = PROMOTION
        DISCOUNTS       = org.get("DISCOUNTS", DISCOUNTS)
        PAYMENT_METHODS = org.get("PAYMENT_METHODS", PAYMENT_METHODS)

        COURSES      = catalog.get("COURSES", COURSES)
        LEVELS       = catalog.get("LEVELS", LEVELS)
        ENROLL_STEPS = catalog.get("ENROLL_STEPS", ENROLL_STEPS)
        print("✅ content.json cargado/mezclado")
    except Exception as e:
        print("ℹ️ Sin content.json o error al leerlo:", e)

load_content()

# ===================== 3) DB LOCAL (SQLite) =====================
DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Lead(Base):
    __tablename__ = "leads"
    id           = Column(Integer, primary_key=True, index=True)
    wa_from      = Column(String(32), index=True)
    name         = Column(String(128), nullable=True)
    intent       = Column(String(64), nullable=True)
    last_message = Column(Text, nullable=True)

class SessionState(Base):
    __tablename__ = "sessions"
    wa_from = Column(String(32), primary_key=True)
    state   = Column(String(64), nullable=True, default="idle")
    data    = Column(Text, nullable=True)

class Enrollment(Base):
    __tablename__ = "enrollments"
    id            = Column(Integer, primary_key=True, index=True)
    wa_from       = Column(String(32), index=True)
    name          = Column(String(128), nullable=False)
    ci            = Column(String(32), nullable=False)
    course        = Column(String(64), nullable=False)
    level         = Column(String(16), nullable=False)
    schedule_pref = Column(String(64), nullable=True)
    contact_phone = Column(String(32), nullable=True)
    email         = Column(String(128), nullable=True)
    ci_image_url  = Column(Text, nullable=True)
    confirmed     = Column(Boolean, default=False)
    created_at    = Column(DateTime, server_default=func.now())

Base.metadata.create_all(bind=engine)

def _get_session(db, phone: str):
    s = db.query(SessionState).get(phone)
    if not s:
        s = SessionState(wa_from=phone, state="idle", data="{}")
        db.add(s)
        db.commit()
    try:
        payload = json.loads(s.data or "{}")
    except Exception:
        payload = {}
    return s, payload

def _save_session(db, phone: str, state: str, payload: dict):
    s = db.query(SessionState).get(phone)
    if not s:
        s = SessionState(wa_from=phone)
        db.add(s)
    s.state = state
    s.data  = json.dumps(payload or {})
    db.commit()

def _clear_session(db, phone: str):
    _save_session(db, phone, "idle", {})

# ===================== 4) APP =====================
app = FastAPI(title=f"{ORG_NAME} - WhatsApp Bot", version="1.8.1")

# ===================== 5) HELPERS: WhatsApp Graph =====================
def send_whatsapp_text(to_number: str, message: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message}
    }
    r = requests.post(url, headers=headers, json=data, timeout=30)
    if not r.ok:
        print("⚠️ Error al enviar mensaje:", r.text)
    return r.json()

def send_whatsapp_location(to_number: str, latitude: float, longitude: float, name: str, address: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "location",
        "location": {"latitude": latitude, "longitude": longitude, "name": name, "address": address},
    }
    r = requests.post(url, headers=headers, json=data, timeout=30)
    if not r.ok:
        print("⚠️ Error al enviar ubicación:", r.text)
    return r.json()

def send_whatsapp_buttons(to_number: str, body_text: str, buttons: List[Tuple[str, str]]):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    btns_payload = [{
        "type": "reply",
        "reply": {"id": bid, "title": (btitle or "")[:20]}
    } for bid, btitle in buttons][:3]

    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": btns_payload},
        },
    }
    r = requests.post(url, headers=headers, json=data, timeout=30)
    if not r.ok:
        print("⚠️ Error al enviar botones:", r.text)
    return r.json()

def send_whatsapp_list(to_number: str, body_text: str, title: str, rows: List[Tuple[str, str]]):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    sections = [{
        "title": (title or "Selecciona")[:24],
        "rows": [{"id": r[0], "title": r[1][:20]} for r in rows][:10]
    }]
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {"button": "Elegir", "sections": sections}
        }
    }
    r = requests.post(url, headers=headers, json=data, timeout=30)
    if not r.ok:
        print("⚠️ Error al enviar lista:", r.text)
    return r.json()

def course_rows(): return [(f"course_{c}", c) for c in COURSES]
def level_rows():  return [(f"level_{l}", l) for l in LEVELS]

def append_to_sheet(phone, name, intent, last_message, extra=None, type_: str = "lead"):
    """Envía datos a Google Sheets vía Apps Script."""
    if not SHEET_WEBHOOK:
        return
    try:
        payload = {
            "phone": phone or "",
            "name": name or "",
            "intent": intent or "",
            "last_message": last_message or "",
            "extra": extra or {},
            "type": type_ or "lead",
        }
        requests.post(SHEET_WEBHOOK, json=payload, timeout=10)
    except Exception as e:
        print("Sheet error:", e)

# ===================== 6) MENÚ / FAQ / BIENVENIDA =====================
def menu_principal():
    cursos = ", ".join(COURSES)
    niveles = ", ".join(LEVELS)

    return (
        "📌 *MENÚ PRINCIPAL*\n"
        "Selecciona una opción o escribe tu consulta:\n\n"
        "1️⃣ Horarios y atención\n"
        "2️⃣ Cursos y niveles\n"
        "3️⃣ Precios y promociones\n"
        "4️⃣ Inscripciones\n"
        "5️⃣ Ubicación / Cómo llegar\n"
        "6️⃣ Contacto\n"
        "7️⃣ Medios de pago\n"
        "—\n"
        f"📚 *Idiomas:* {cursos}\n"
        f"🎯 *Niveles:* {niveles}"
    )

def mensaje_bienvenida():
    promo_line = ""
    if CONTENT.get("org", {}).get("PROMOTION"):
        promo_line = f"\n💵 *Promoción vigente:* {CONTENT['org']['PROMOTION']}\n"

    cursos = ", ".join(COURSES)
    niveles = ", ".join(LEVELS)

    return (
        "👋 *¡Bienvenido/a a la Escuela de Idiomas del Ejército – Filial Santa Cruz!* 🇧🇴\n\n"
        "👇 *Para ayudarte más rápido, selecciona según tu caso:*\n\n"
        "🟢 *Si es tu primera vez aquí:*\n"
        "   • Escribe *3* para ver *precios y promociones*\n"
        "   • Escribe *4* para *inscribirte ahora mismo*\n\n"
        "🔵 *Si ya eres estudiante:*\n"
        "   • Escribe *1* para *horarios y atención*\n"
        "   • Escribe *7* para conocer *medios de pago*\n\n"
        "📝 También puedes escribir tu consulta directamente.\n"
        "   (Ej: “Horario para inglés A1”, “Quiero estudiar francés”, “Quiero pagar”).\n\n"
        f"📍 *Dirección:* {ADDRESS}\n"
        f"🕐 *Horario de atención:* {OPENING_HOURS}\n"
        f"📚 *Idiomas:* {cursos}\n"
        f"🎯 *Niveles:* {niveles}\n"
        f"{promo_line}"
        "👇 *Menú principal:*\n"
        "1️⃣ Horarios y atención\n"
        "2️⃣ Cursos y niveles\n"
        "3️⃣ Precios y promociones\n"
        "4️⃣ Inscripciones\n"
        "5️⃣ Ubicación / Cómo llegar\n"
        "6️⃣ Contacto\n"
        "7️⃣ Medios de pago"
    )

def answer_for_intent(intent: str, payload: dict):
    intent = (intent or "").lower()
    faq = (CONTENT.get("faq") or {})
    if faq.get(intent):
        return faq[intent]

    if intent == "horarios":
        return f"🕘 *Horarios:* {OPENING_HOURS}."
    if intent == "cursos":
        return f"📚 *Cursos:* {', '.join(COURSES)}.\n🎯 *Niveles:* {', '.join(LEVELS)}."
    if intent == "precios":
        promo = CONTENT.get("org", {}).get("PROMOTION")
        line_promo = f"\n🎖️ *Promoción:* {promo}" if promo else ""
        return (
            f"💵 *Precios:* {PRICES}.{line_promo}\n\n"
            "Si deseas, responde *4* o escribe *Inscripción* y te ayudo a registrarte ahora mismo. ✍️"
        )
    if intent == "inscripciones":
        pasos = "\n".join([f"- {p.strip()}" for p in ENROLL_STEPS if p.strip()])
        return f"📝 *Inscripciones (pasos):*\n{pasos}\n¿Te inscribo ahora mismo por aquí?"
    if intent == "ubicacion":
        return f"📍 *Dirección:* {ADDRESS}\n📌 Mapa: {GOOGLE_MAPS_LINK}"
    if intent == "contacto":
        faq_contacto = (CONTENT.get("faq") or {}).get("contacto")
        return faq_contacto or f"☎️ {CONTACT_PHONE}\n✉️ {CONTACT_EMAIL}"
    if intent == "pagos":
        t = (payload.get("last_text","") or "").lower()
        if any(w in t for w in ["qr","codigo qr","código qr"]):
            return "💳 *Pago por QR:* disponible. Te enviamos el código desde secretaría académica. Envíanos tu comprobante por aquí."
        if any(w in t for w in ["efectivo","cash"]):
            return "💵 *Efectivo:* puedes pagar en ventanilla de la filial."
        if any(w in t for w in ["transfer","transferencia","banco","cuenta"]):
            return "🏦 *Transferencia bancaria:* al confirmar tu inscripción te enviamos el número de cuenta."
        return f"💳 *Medios de pago:* {PAYMENT_METHODS}"
    return menu_principal()

# ===================== 7) REGLAS =====================
def detect_intent_rules(text: str) -> str:
    if not text:
        return "general"
    t = text.lower()

    rules = (CONTENT.get("rules") or {})
    for intent, palabras in rules.items():
        if any(p in t for p in palabras):
            return intent

    reglas_def = {
        "horarios": ["horario", "hora", "atienden", "abren", "cierran", "atencion", "atención"],
        "cursos": ["curso", "cursos", "idioma", "clase", "clases", "nivel", "niveles"],
        "precios": ["precio", "precios", "cuesta", "tarifa", "vale", "mensualidad", "inscripcion", "inscripción", "material"],
        "inscripciones": ["inscribir", "inscripcion", "inscripción", "requisito", "requisitos", "postular", "matricula", "matrícula"],
        "ubicacion": ["ubicacion", "ubicación", "direccion", "dirección", "donde", "dónde", "mapa", "llegar"],
        "contacto": ["contacto", "telefono", "teléfono", "email", "correo", "tiktok", "redes"],
        "pagos": ["pago", "pagos", "transferencia", "qr", "efectivo", "metodo", "método", "banco", "cuenta"],
    }
    for intent, palabras in reglas_def.items():
        if any(p in t for p in palabras):
            return intent

    shortcuts = {"1":"horarios","2":"cursos","3":"precios","4":"inscripciones","5":"ubicacion","6":"contacto","7":"pagos"}
    return shortcuts.get(t, "general")

# ===================== 8) IA (opcional) =====================
try:
    from openai import OpenAI
    _openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    _openai_client = None

def generate_ai_answer(user_text: str) -> str:
    """
    Genera una respuesta usando OpenAI si está disponible.
    Si no hay API key o ocurre un error, responde con el menú principal
    sin mostrar mensajes técnicos al usuario.
    """
    # IA NO CONFIGURADA
    if not OPENAI_API_KEY or _openai_client is None:
        return (
            "👋 Gracias por tu mensaje. Para ayudarte mejor, aquí tienes el menú principal.\n"
            "Selecciona una opción o escríbeme más detalles:\n\n"
            + menu_principal()
        )

    try:
        system_prompt = (
            f"Eres un asistente para *{ORG_NAME}* en {CITY}. "
            f"Datos: Dirección {ADDRESS}. Mapa {GOOGLE_MAPS_LINK}. Horarios {OPENING_HOURS}. "
            f"Cursos {', '.join(COURSES)}. Niveles {', '.join(LEVELS)}. Precios {PRICES}. "
            f"Medios de pago {PAYMENT_METHODS}. Contacto {CONTACT_PHONE}/{CONTACT_EMAIL}. "
            f"Si hay promoción, menciona: {CONTENT.get('org',{}).get('PROMOTION','(sin promoción)')}. "
            "Responde corto (≤6 líneas), claro y con tono amable."
        )
        resp = _openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role":"system","content":system_prompt},
                {"role":"user","content":user_text}
            ],
            max_tokens=220, temperature=0.3,
        )
        return resp.choices[0].message.content.strip()

    except Exception as e:
        print("⚠️ Error IA:", e)
        return (
            "👋 Gracias por tu mensaje. Para ayudarte mejor, aquí tienes el menú principal.\n"
            "Selecciona una opción o escríbeme más detalles:\n\n"
            + menu_principal()
        )


# ===================== 9) VALIDACIONES =====================
AFFIRM = {"si","sí","claro","ok","okay","vale","yes","de acuerdo","confirmo","acepto"}
NEGATE  = {"no","nop","no gracias","cancelar","cancelado"}
SMALL_TALK_OK = {"ok","oki","gracias","gracias!","thanks","listo","entendido","ya","vale","de acuerdo"}

HMAP = {"hoy":0, "mañana":1}
DMAP = {"lun":0,"mar":1,"mié":2,"mie":2,"jue":3,"vie":4,"sáb":5,"sab":5,"dom":6,
        "lunes":0,"martes":1,"miércoles":2,"miercoles":2,"jueves":3,"viernes":4,"sábado":5,"domingo":6}

ESCAPE_WORDS = {"menu", "menú", "inicio", "cancelar", "salir", "0"}

def is_valid_name(text: str) -> bool:
    return bool(re.search(r"[a-zA-ZÁÉÍÓÚÜÑáéíóúüñ]{2,}", text or ""))

def is_valid_ci(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9\-\.]{5,12}", (text or "").strip()))

def parse_day_time(text: str):
    if not text: return None
    t = text.lower().strip()
    now = datetime.now()

    m = re.search(r"(hoy|mañana)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if m:
        day, hh, mm, ap = m.groups()
        d = now + timedelta(days=HMAP[day])
        h = int(hh); mnt = int(mm or 0)
        if ap and ap == "pm" and h < 12: h += 12
        return d.replace(hour=h, minute=mnt, second=0, microsecond=0)

    m = re.search(r"(lun|mar|mi[eé]|jue|vie|s[aá]b|dom|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+(\d{1,2})(?::(\d{2}))?", t)
    if m:
        dayword, hh, mm = m.groups()
        key = dayword[:3]
        target_wd = DMAP.get(key, DMAP.get(dayword, None))
        if target_wd is not None:
            delta = (target_wd - now.weekday()) % 7
            d = now + timedelta(days=delta)
            return d.replace(hour=int(hh), minute=int(mm or 0), second=0, microsecond=0)

    m = re.search(r"\b(\d{1,2})(?::(\d{2}))\b", t)
    if m:
        hh, mm = m.groups()
        return now.replace(hour=int(hh), minute=int(mm or 0), second=0, microsecond=0)

    m = re.search(r"\b([01]?\d|2[0-3])\s*[-–]\s*([01]?\d|2[0-3])\b", t)
    if m:
        a, _ = m.groups()
        return now.replace(hour=int(a), minute=0, second=0, microsecond=0)

    return None

def is_greeting(tnorm: str) -> bool:
    return tnorm in {"hola","buenas","buenos días","buen dia","buen día","buenas tardes","buenas noches","saludos"}

# ===================== 10) ADMIN HELPERS =====================
def is_admin(wa_from: str) -> bool:
    return (ADMIN_WHATSAPP or "").strip() != "" and (wa_from or "").strip() == ADMIN_WHATSAPP.strip()

def send_whatsapp_document(to_number: str, file_url: str, filename: str = "reporte.csv", caption: str = ""):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "document",
        "document": {"link": file_url, "filename": filename, "caption": caption[:1024]},
    }
    r = requests.post(url, headers=headers, json=data, timeout=30)
    if not r.ok:
        print("⚠️ Error al enviar documento:", r.text)
    return r.json()

# ===================== 11) WEBHOOKS =====================
@app.get("/webhook", response_class=PlainTextResponse)
async def verify(request: Request):
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="Verificación fallida")

@app.post("/webhook")
async def webhook_receiver(request: Request):
    body = await request.json()
    try:
        print("\n=== WEBHOOK BODY ===")
        print(json.dumps(body, ensure_ascii=False, indent=2))
    except Exception:
        pass

    try:
        entry   = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value   = changes.get("value", {})

        if "statuses" in value:
            return JSONResponse({"status": "status_event_ignored"})

        messages = value.get("messages", [])
        if not messages:
            return JSONResponse({"status": "no_message"})

        msg     = messages[0]
        from_wa = msg.get("from")
        name    = value.get("contacts", [{}])[0].get("profile", {}).get("name", "")

        text = ""; button_id = None
        mtype = msg.get("type")
        if mtype == "text":
            text = msg["text"]["body"].strip()
        elif mtype == "interactive":
            inter = msg.get("interactive", {})
            if "button_reply" in inter:
                br = inter["button_reply"]
                text = (br.get("title") or "").strip()
                button_id = br.get("id")
            elif "list_reply" in inter:
                lr = inter["list_reply"]
                text = (lr.get("title") or "").strip()
        elif mtype == "image":
            text = ""  # puede ser CI en foto

        tnorm = (text or "").lower().strip()

        db = SessionLocal()
        sess, payload = _get_session(db, from_wa)

        payload["last_text"] = text
        _save_session(db, from_wa, sess.state or "idle", payload)

        # Atajo global: resetear sesión y volver al menú
        if tnorm in ESCAPE_WORDS:
            _clear_session(db, from_wa)
            send_whatsapp_text(from_wa, "🔁 Volvemos al inicio.\n\n" + menu_principal())
            db.close(); return JSONResponse({"status":"ok","flow":"reset_menu"})

        # SALUDO
        if is_greeting(tnorm):
            send_whatsapp_text(from_wa, mensaje_bienvenida())
            db.close(); return JSONResponse({"status":"ok","flow":"saludo_inicial"})

        # SMALL TALK
        last_intent = payload.get("last_intent","")
        if tnorm in SMALL_TALK_OK and last_intent and sess.state == "idle":
            send_whatsapp_text(from_wa, "👌 Perfecto. Si necesitas algo más, escribe un número del menú.")
            db.close(); return JSONResponse({"status":"ok","flow":"small_talk"})

        # ===== COMANDOS ADMIN POR WHATSAPP =====
        if is_admin(from_wa) and tnorm:
            parts = tnorm.split()
            cmd = parts[0] if parts else ""
            arg1 = parts[1] if len(parts) > 1 else ""
            arg2 = parts[2] if len(parts) > 2 else ""
            try:
                if cmd == "stats":
                    total_leads = db.query(Lead).count()
                    by_intent_rows = db.query(Lead.intent, func.count(Lead.id)).group_by(Lead.intent).all()
                    insc_total = db.query(Enrollment).count()
                    by_course_rows = db.query(Enrollment.course, func.count(Enrollment.id)).group_by(Enrollment.course).all()
                    by_level_rows  = db.query(Enrollment.level,  func.count(Enrollment.id)).group_by(Enrollment.level).all()

                    def fmt_pairs(rows):
                        if not rows: return "—"
                        rows = sorted(rows, key=lambda x: (x[1] or 0), reverse=True)
                        return ", ".join([f"{(x[0] or 'general')}: {x[1]}" for x in rows])

                    msg_out = (
                        "📊 *Estadísticas del bot*\n\n"
                        f"• Leads totales: *{total_leads}*\n"
                        f"• Por intención: {fmt_pairs(by_intent_rows)}\n\n"
                        f"• Inscripciones: *{insc_total}*\n"
                        f"• Por curso: {fmt_pairs(by_course_rows)}\n"
                        f"• Por nivel: {fmt_pairs(by_level_rows)}"
                    )
                    send_whatsapp_text(from_wa, msg_out)
                    db.close(); return JSONResponse({"status":"ok","admin":"stats"})

                if cmd == "leads":
                    only_reservar = (arg1 == "reservar" or arg2 == "reservar")
                    offsets = [x for x in [arg1, arg2] if x.isdigit()]
                    offset = int(offsets[0]) if offsets else 0
                    q = db.query(Lead).order_by(Lead.id.desc())
                    if only_reservar:
                        q = q.filter(Lead.intent == "reservar")
                    rows = q.offset(offset).limit(10).all()
                    if not rows:
                        send_whatsapp_text(from_wa, "No hay más leads para mostrar.")
                        db.close(); return JSONResponse({"status":"ok","admin":"leads_empty"})
                    lines = []
                    for r in rows:
                        lines.append(
                            f"#{r.id} • {r.intent or 'general'}\n"
                            f"👤 {r.name or '-'} | 📱 {r.wa_from}\n"
                            f"💬 {(r.last_message or '')[:120]}"
                        )
                    footer = f"\nMostrando {offset+1}-{offset+len(rows)}. Envía: *leads {('reservar ' if only_reservar else '')}{offset+10}* para siguiente página."
                    send_whatsapp_text(from_wa, "📥 *Leads*\n\n" + "\n\n".join(lines) + footer)
                    db.close(); return JSONResponse({"status":"ok","admin":"leads_list"})

                if cmd == "insc":
                    offset = int(arg1) if arg1.isdigit() else 0
                    rows = db.query(Enrollment).order_by(Enrollment.id.desc()).offset(offset).limit(10).all()
                    if not rows:
                        send_whatsapp_text(from_wa, "No hay más inscripciones para mostrar.")
                        db.close(); return JSONResponse({"status":"ok","admin":"insc_empty"})
                    lines = []
                    for r in rows:
                        lines.append(
                            f"#{r.id} • {r.course} {r.level}\n"
                            f"👤 {r.name} ({r.ci})\n"
                            f"🕐 {r.schedule_pref or '-'} | 📱 {r.wa_from}"
                        )
                    footer = f"\nMostrando {offset+1}-{offset+len(rows)}. Envía: *insc {offset+10}* para siguiente página."
                    send_whatsapp_text(from_wa, "🆕 *Inscripciones*\n\n" + "\n\n".join(lines) + footer)
                    db.close(); return JSONResponse({"status":"ok","admin":"insc_list"})

                if cmd == "export" and arg1 in {"insc","leads"}:
                    if not PUBLIC_BASE_URL:
                        send_whatsapp_text(from_wa, "Configura PUBLIC_BASE_URL en .env para enviar documentos por WhatsApp.")
                        db.close(); return JSONResponse({"status":"ok","admin":"export_no_baseurl"})
                    if arg1 == "insc":
                        file_url = f"{PUBLIC_BASE_URL}/export/enrollments.csv"
                        send_whatsapp_document(from_wa, file_url, filename="inscripciones.csv", caption="Inscripciones CSV")
                        db.close(); return JSONResponse({"status":"ok","admin":"export_insc"})
                    if arg1 == "leads":
                        file_url = f"{PUBLIC_BASE_URL}/export/leads.csv"
                        send_whatsapp_document(from_wa, file_url, filename="leads.csv", caption="Leads CSV")
                        db.close(); return JSONResponse({"status":"ok","admin":"export_leads"})
            except Exception as e:
                print("Admin cmd error:", e)
                send_whatsapp_text(from_wa, f"Error procesando comando admin: {e}")
                db.close(); return JSONResponse({"status":"ok","admin":"error"})

        # ===== RESERVAS =====
        if sess.state == "awaiting_reserve_yesno":
            affirmative = (button_id == "yes_reserve") or (tnorm in AFFIRM)
            negative    = (button_id == "no_reserve")  or (tnorm in NEGATE)
            if affirmative:
                _save_session(db, from_wa, "reserva_pide_nombre", payload)
                send_whatsapp_text(from_wa, "Perfecto ✅. ¿Cuál es tu *nombre completo*?")
                db.close(); return JSONResponse({"status":"ok","flow":"reserva_nombre"})
            if negative:
                _clear_session(db, from_wa)
                send_whatsapp_text(from_wa, "Sin problema. Si necesitas algo más, dime.\n\n" + menu_principal())
                db.close(); return JSONResponse({"status":"ok","flow":"reserva_cancel"})
            send_whatsapp_buttons(from_wa, "¿Deseas *reservar* un cupo?", [("yes_reserve","Sí"),("no_reserve","No")])
            db.close(); return JSONResponse({"status":"ok","flow":"repregunta_yesno"})

        if sess.state == "reserva_pide_nombre":
            if not is_valid_name(text):
                send_whatsapp_text(from_wa, "Por favor, envíame tu *nombre y apellido* (solo letras).")
                db.close(); return JSONResponse({"status":"ok","flow":"reintenta_nombre"})
            payload["nombre"] = text.strip()
            _save_session(db, from_wa, "reserva_pide_hora", payload)
            send_whatsapp_text(from_wa, "Gracias. ¿Qué *día y hora* prefieres? (Ej: *martes 10:00* o *mañana 9–11*)")
            db.close(); return JSONResponse({"status":"ok","flow":"reserva_horario"})

        if sess.state == "reserva_pide_hora":
            dt = parse_day_time(text)
            if not dt:
                send_whatsapp_text(from_wa, "No pude entender el horario 😅. *Ejemplos:* _martes 10:00_, _mañana 9–11_, _hoy 18:30_.")
                db.close(); return JSONResponse({"status":"ok","flow":"reserva_hora_retry"})
            payload["horario"] = dt.strftime("%A %d/%m %H:%M")
            _save_session(db, from_wa, "reserva_confirmar", payload)
            resumen = (
                f"Confírmame por favor:\n"
                f"• Nombre: *{payload.get('nombre','')}*\n"
                f"• Horario: *{payload.get('horario','')}*\n\n"
                "¿Confirmas la *reserva*?"
            )
            send_whatsapp_text(from_wa, resumen)
            send_whatsapp_buttons(from_wa, "Confirma por favor:", [("confirm_yes","Sí"),("confirm_no","No")])
            db.close(); return JSONResponse({"status":"ok","flow":"reserva_confirmar"})

        if sess.state == "reserva_confirmar":
            affirmative = (button_id == "confirm_yes") or (tnorm in AFFIRM)
            negative    = (button_id == "confirm_no")  or (tnorm in NEGATE)
            if affirmative:
                lead = Lead(wa_from=from_wa, name=payload.get("nombre"), intent="reservar", last_message=payload.get("horario"))
                db.add(lead); db.commit()
                append_to_sheet(from_wa, payload.get("nombre"), "reservar", payload.get("horario"), extra={}, type_="lead")
                if ADMIN_WHATSAPP:
                    try:
                        send_whatsapp_text(
                            ADMIN_WHATSAPP,
                            f"📥 Nueva *reserva*:\n• Nombre: {payload.get('nombre')}\n• Tel: {from_wa}\n• Horario: {payload.get('horario')}"
                        )
                    except Exception as e:
                        print("Aviso admin error:", e)
                _clear_session(db, from_wa)
                send_whatsapp_text(from_wa, "🎉 ¡Listo! Registré tu *reserva*. Te contactaremos para confirmar disponibilidad.\n\n" + menu_principal())
                db.close(); return JSONResponse({"status":"ok","flow":"reserva_cerrada"})
            if negative:
                _clear_session(db, from_wa)
                send_whatsapp_text(from_wa, "Reserva cancelada. Aquí tienes el menú por si necesitas otra cosa:\n\n" + menu_principal())
                db.close(); return JSONResponse({"status":"ok","flow":"reserva_cancelada"})
            resumen = f"Confirma por favor:\n• Nombre: *{payload.get('nombre','')}*\n• Horario: *{payload.get('horario','')}*"
            send_whatsapp_text(from_wa, resumen)
            send_whatsapp_buttons(from_wa, "Confirma por favor:", [("confirm_yes","Sí"),("confirm_no","No")])
            db.close(); return JSONResponse({"status":"ok","flow":"confirm_reintento"})

        # ===== INTENCIÓN (se usará más abajo también) =====
        intent = detect_intent_rules(text)
        payload["last_intent"] = intent
        _save_session(db, from_wa, sess.state or "idle", payload)

        # ===== FLUJO INSCRIPCIONES =====
        if intent == "inscripciones" and sess.state not in {
            "insc_pide_ci","insc_pide_nombre","insc_pide_curso","insc_pide_nivel","insc_pide_hora","insc_confirmar"
        }:
            payload["insc"] = {}
            _save_session(db, from_wa, "insc_pide_ci", payload)
            send_whatsapp_text(from_wa, "Perfecto. Para iniciar tu inscripción, envíame tu *CI* (ej: 1234567). También puedes enviar foto del CI.")
            db.close(); return JSONResponse({"status":"ok","flow":"insc_ci"})

        if sess.state == "insc_pide_ci":
            # 1) Si es imagen → CI en foto
            if mtype == "image":
                payload.setdefault("insc", {})["ci_image_url"] = "(foto recibida)"
                _save_session(db, from_wa, "insc_pide_nombre", payload)
                send_whatsapp_text(from_wa, "Recibí la foto 👍. Ahora envíame tu *nombre completo*.")
                db.close(); return JSONResponse({"status":"ok","flow":"insc_nombre"})

            # 2) Si parece CI válido → seguimos flujo normal
            if is_valid_ci(text):
                payload.setdefault("insc", {})["ci"] = text.strip()
                _save_session(db, from_wa, "insc_pide_nombre", payload)
                send_whatsapp_text(from_wa, "Gracias. Ahora envíame tu *nombre completo*.")
                db.close(); return JSONResponse({"status":"ok","flow":"insc_nombre"})

            # 3) NO es CI → lo interpretamos como una nueva consulta
            new_intent = detect_intent_rules(text)
            _clear_session(db, from_wa)      # salimos del flujo de inscripción
            payload["last_intent"] = new_intent
            _save_session(db, from_wa, "idle", payload)

            if new_intent == "inscripciones":
                # Si aun así quiere inscripciones, reiniciamos bien
                payload["insc"] = {}
                _save_session(db, from_wa, "insc_pide_ci", payload)
                send_whatsapp_text(from_wa, "Para la inscripción necesito tu *CI* (ej: 1234567) o foto del documento.")
                db.close(); return JSONResponse({"status":"ok","flow":"insc_ci_restart"})

            # Respuesta normal para la nueva intención (ej: “Horario para inglés A1”)
            msg_out = answer_for_intent(new_intent, payload) if new_intent != "general" else generate_ai_answer(text or "")
            send_whatsapp_text(from_wa, msg_out)
            if new_intent == "ubicacion":
                try:
                    send_whatsapp_location(from_wa, -17.776126747602, -63.167443644971414, ORG_NAME, ADDRESS)
                except Exception as e:
                    print("Ubicación error:", e)

            lead = Lead(wa_from=from_wa, name=name, intent=new_intent, last_message=text)
            db.add(lead); db.commit(); db.close()
            append_to_sheet(from_wa, name, new_intent, text, extra={}, type_="lead")
            return JSONResponse({"status":"ok","intent":new_intent})

        if sess.state == "insc_pide_nombre":
            if not is_valid_name(text):
                send_whatsapp_text(from_wa, "Por favor, envíame tu *nombre y apellido* (solo letras).")
                db.close(); return JSONResponse({"status":"ok","flow":"insc_nombre_retry"})
            payload["insc"]["name"] = text.strip()
            _save_session(db, from_wa, "insc_pide_curso", payload)
            send_whatsapp_list(from_wa, "Elige el *idioma* que deseas cursar:", "Idiomas", course_rows())
            db.close(); return JSONResponse({"status":"ok","flow":"insc_curso"})

        if sess.state == "insc_pide_curso":
            chosen = None
            if mtype == "interactive" and "list_reply" in msg.get("interactive", {}):
                chosen = msg["interactive"]["list_reply"].get("title")
            if not chosen or chosen not in COURSES:
                if text in COURSES:
                    chosen = text
                else:
                    send_whatsapp_list(from_wa, "Por favor elige un *idioma* de la lista:", "Idiomas", course_rows())
                    db.close(); return JSONResponse({"status":"ok","flow":"insc_curso_retry"})
            payload["insc"]["course"] = chosen
            _save_session(db, from_wa, "insc_pide_nivel", payload)
            send_whatsapp_list(from_wa, "Elige tu *nivel*:", "Niveles", level_rows())
            db.close(); return JSONResponse({"status":"ok","flow":"insc_nivel"})

        if sess.state == "insc_pide_nivel":
            chosen = None
            if mtype == "interactive" and "list_reply" in msg.get("interactive", {}):
                chosen = msg["interactive"]["list_reply"].get("title")
            if not chosen or chosen not in LEVELS:
                if text in LEVELS:
                    chosen = text
                else:
                    send_whatsapp_list(from_wa, "Elige tu *nivel*:", "Niveles", level_rows())
                    db.close(); return JSONResponse({"status":"ok","flow":"insc_nivel_retry"})
            payload["insc"]["level"] = chosen
            _save_session(db, from_wa, "insc_pide_hora", payload)
            send_whatsapp_text(from_wa, "¿Qué *horario* prefieres? (ej: Mañanas 08:30–11:00 / Tardes 14:30–17:00 / Noches 19:00–21:30)")
            db.close(); return JSONResponse({"status":"ok","flow":"insc_hora"})

        if sess.state == "insc_pide_hora":
            payload["insc"]["schedule_pref"] = text.strip() if text else ""
            _save_session(db, from_wa, "insc_confirmar", payload)
            ins = payload["insc"]
            resumen = (
                "✅ *Resumen de tu inscripción:*\n"
                f"- CI: {ins.get('ci','(foto)')}\n"
                f"- Nombre: {ins.get('name','')}\n"
                f"- Curso: {ins.get('course','')}\n"
                f"- Nivel: {ins.get('level','')}\n"
                f"- Horario: {ins.get('schedule_pref','')}\n\n"
                "¿Confirmas? (Sí/No)"
            )
            send_whatsapp_text(from_wa, resumen)
            send_whatsapp_buttons(from_wa, "Confirma por favor:", [("confirm_yes","Sí"),("confirm_no","No")])
            db.close(); return JSONResponse({"status":"ok","flow":"insc_confirmar"})

        if sess.state == "insc_confirmar":
            affirmative = (button_id == "confirm_yes") or (tnorm in AFFIRM)
            negative    = (button_id == "confirm_no")  or (tnorm in NEGATE)
            if affirmative:
                ins = payload.get("insc", {})
                e = Enrollment(
                    wa_from=from_wa,
                    name=ins.get("name",""),
                    ci=ins.get("ci", "(foto)"),
                    course=ins.get("course",""),
                    level=ins.get("level",""),
                    schedule_pref=ins.get("schedule_pref",""),
                    ci_image_url=ins.get("ci_image_url")
                )
                db.add(e); db.commit()
                append_to_sheet(
                    from_wa,
                    ins.get("name"),
                    "inscripcion",
                    "",
                    extra=ins,
                    type_="inscripcion"
                )
                if ADMIN_WHATSAPP:
                    send_whatsapp_text(
                        ADMIN_WHATSAPP,
                        f"🆕 *Nueva inscripción*\n• Nombre: {e.name}\n• CI: {e.ci}\n• Curso/Nivel: {e.course} {e.level}\n• Horario: {e.schedule_pref}\n• Tel: {from_wa}"
                    )
                _clear_session(db, from_wa)
                send_whatsapp_text(from_wa, "🎉 ¡Inscripción registrada! Te contactaremos para confirmar aula y fecha de inicio.")
                db.close(); return JSONResponse({"status":"ok","flow":"insc_ok"})
            if negative:
                _clear_session(db, from_wa)
                send_whatsapp_text(from_wa, "Inscripción cancelada. Si deseas, puedes iniciar nuevamente con *Inscripciones*.")
                db.close(); return JSONResponse({"status":"ok","flow":"insc_cancel"})
            send_whatsapp_buttons(from_wa, "Confirma por favor:", [("confirm_yes","Sí"),("confirm_no","No")])
            db.close(); return JSONResponse({"status":"ok","flow":"insc_confirm_retry"})

        # ===== RESPUESTAS NORMALES (cuando no estamos en flujo especial) =====
        if intent == "horarios":
            _save_session(db, from_wa, "awaiting_reserve_yesno", payload)
            send_whatsapp_text(from_wa, answer_for_intent(intent, payload))
            send_whatsapp_buttons(from_wa, "¿Deseas *reservar* un cupo?", [("yes_reserve","Sí"),("no_reserve","No")])
        else:
            msg_out = answer_for_intent(intent, payload) if intent != "general" else generate_ai_answer(text or "")
            send_whatsapp_text(from_wa, msg_out)
            if intent == "ubicacion":
                try:
                    send_whatsapp_location(from_wa, -17.776126747602, -63.167443644971414, ORG_NAME, ADDRESS)
                except Exception as e:
                    print("Ubicación error:", e)

        # Guardar lead + hoja
        lead = Lead(wa_from=from_wa, name=name, intent=intent, last_message=text)
        db.add(lead); db.commit(); db.close()
        append_to_sheet(from_wa, name, intent, text, extra={}, type_="lead")
        return JSONResponse({"status":"ok","intent":intent})

    except Exception as e:
        print("❌ Error webhook:", e)
        return JSONResponse({"status":"error","detail":str(e)}, status_code=500)

# ===================== 12) ENDPOINTS ADMIN =====================
@app.post("/admin/reload")
def admin_reload(x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="No autorizado")
    load_content()
    return {"ok": True, "msg": "Contenido recargado"}

@app.post("/admin/override")
async def admin_override(req: Request, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="No autorizado")
    data = await req.json()
    org     = data.get("org") or {}
    catalog = data.get("catalog") or {}
    if org:
        for k, v in org.items():
            globals()[k] = v
    if catalog:
        for k, v in catalog.items():
            if k in ["COURSES","LEVELS","ENROLL_STEPS"]:
                globals()[k] = v
    if data.get("faq"):   CONTENT.setdefault("faq", {}).update(data["faq"])
    if data.get("rules"): CONTENT.setdefault("rules", {}).update(data["rules"])
    return {"ok": True, "msg": "Overrides aplicados (en memoria)"}

# ===================== 13) ENDPOINTS AUX =====================
@app.get("/leads")
def list_leads():
    db = SessionLocal()
    rows = db.query(Lead).order_by(Lead.id.desc()).limit(100).all()
    out = [{"id": r.id, "phone": r.wa_from, "name": r.name, "intent": r.intent, "last_message": r.last_message} for r in rows]
    db.close()
    return out

@app.get("/stats")
def stats():
    db = SessionLocal()
    total_leads = db.query(Lead).count()
    by_intent = {}
    for (i, cnt) in db.query(Lead.intent, func.count(Lead.id)).group_by(Lead.intent).all():
        by_intent[i or "general"] = cnt
    enrolls = db.query(Enrollment).count()
    by_course = dict(db.query(Enrollment.course, func.count(Enrollment.id)).group_by(Enrollment.course).all())
    by_level  = dict(db.query(Enrollment.level,  func.count(Enrollment.id)).group_by(Enrollment.level).all())
    db.close()
    return {
        "leads_total": total_leads,
        "leads_por_intent": by_intent,
        "inscripciones_total": enrolls,
        "inscripciones_por_curso": by_course,
        "inscripciones_por_nivel": by_level,
    }

@app.get("/export/enrollments.csv")
def export_enrollments_csv():
    db = SessionLocal()
    rows = db.query(Enrollment).order_by(Enrollment.id.desc()).all()
    db.close()
    f = StringIO()
    w = csv.writer(f)
    w.writerow(["id","wa_from","name","ci","course","level","schedule_pref","ci_image_url","created_at"])
    for r in rows:
        w.writerow([r.id, r.wa_from, r.name, r.ci, r.course, r.level, r.schedule_pref, r.ci_image_url, r.created_at])
    f.seek(0)
    return StreamingResponse(f, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=enrollments.csv"})

@app.get("/export/leads.csv")
def export_leads_csv():
    db = SessionLocal()
    rows = db.query(Lead).order_by(Lead.id.desc()).all()
    db.close()
    f = StringIO()
    w = csv.writer(f)
    w.writerow(["id","wa_from","name","intent","last_message"])
    for r in rows:
        w.writerow([r.id, r.wa_from, r.name, r.intent, (r.last_message or "").replace("\n"," ")])
    f.seek(0)
    return StreamingResponse(f, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"})

@app.get("/")
def root():
    return {
        "status":"ok",
        "org":ORG_NAME,
        "endpoints":[
            "/webhook",
            "/leads",
            "/stats",
            "/export/leads.csv",
            "/export/enrollments.csv",
            "/admin/reload",
            "/admin/override",
            "/docs"
        ]
    }


from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"status": "running"}

@app.get("/test")
def test():
    return {"message": "Bot funcionando correctamente en Render"}
