import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telebot
from telebot import types

# ====== Xavfsiz sozlamalar: muhit o'zgaruvchilarida saqlang ======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN muhit o'zgaruvchisini sozlang.")

ADMIN_IDS = [
    int(value.strip())
    for value in os.environ.get("ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
]
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS muhit o'zgaruvchisini sozlang (masalan: 123456789).")
# ==================================================================

bot = telebot.TeleBot(BOT_TOKEN)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_error(404)
            return
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

# ---------- JSON baza ----------
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
lock = threading.Lock()


def load():
    default = {"users": [], "admins": [], "projects": [], "announcements": [], "orders": [], "messages": []}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            default.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return default


db = load()


def save():
    with lock:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)


# ---------- Yordamchilar ----------
state = {}  # user_id -> {"step": ..., ...}


def now_ts():
    return int(datetime.now().timestamp() * 1000)


def fmt_date(ts):
    tz = timezone(timedelta(hours=5))  # Toshkent
    return datetime.fromtimestamp(ts / 1000, tz).strftime("%d.%m.%Y %H:%M")


def is_admin(uid):
    return uid in ADMIN_IDS or any(a["id"] == uid for a in db["admins"])


def admin_ids():
    return list({*ADMIN_IDS, *[a["id"] for a in db["admins"]]})


def get_user(uid):
    return next((u for u in db["users"] if u["id"] == uid), None)


def user_info(u):
    uname = f" (@{u['username']})" if u.get("username") else ""
    return f"👤 {u['name']}{uname}\n🆔 {u['id']}\n📞 {u['phone']}"


def send_long(chat_id, text):
    for i in range(0, len(text), 3900):
        bot.send_message(chat_id, text[i:i + 3900])


def notify_admins(text, markup=None):
    for aid in admin_ids():
        try:
            bot.send_message(aid, text, reply_markup=markup)
        except Exception as e:
            print("Adminga yuborib bo'lmadi:", aid, e)


# Tugma ranglari (Telegram Bot API "style"): danger = qizil, primary = ko'k
class StyledButton(types.InlineKeyboardButton):
    def __init__(self, text, callback_data, style):
        super().__init__(text, callback_data=callback_data)
        self._style = style

    def to_dict(self):
        d = super().to_dict()
        d["style"] = self._style
        return d


def red(text, data):
    return StyledButton(text, data, "danger")


def blue(text, data):
    return StyledButton(text, data, "primary")


def markup(rows):
    m = types.InlineKeyboardMarkup()
    for row in rows:
        m.row(*row)
    return m


PRICES = (
    "💰 Narxlar (taxminiy):\n\n"
    "🌐 Web sayt: 90 000 – 150 000 so'm\n"
    "🤖 Telegram bot: 100 000 – 200 000 so'm\n\n"
    "Aniq narx loyihaning murakkabligiga qarab belgilanadi."
)


# ---------- Menyular ----------
def main_menu(chat_id):
    bot.send_message(
        chat_id,
        "🏠 Asosiy menyu",
        reply_markup=markup([
            [red("📁 Loyihalar", "m_projects"), red("📢 E'lonlar", "m_ann")],
            [red("✉️ Adminga murojaat", "m_contact"), red("🛒 Loyiha buyurtma qilish", "m_order")],
            [red("💰 Narxlar", "m_prices")],
        ]),
    )


def admin_menu(chat_id):
    bot.send_message(
        chat_id,
        "🛠 Admin panel",
        reply_markup=markup([
            [blue("➕ Admin qo'shish", "a_addadmin"), blue("➖ Admin o'chirish", "a_deladmin")],
            [blue("➕ Loyiha qo'shish", "a_addproj"), blue("➖ Loyiha o'chirish", "a_delproj")],
            [blue("➕ E'lon qo'shish", "a_addann"), blue("➖ E'lon o'chirish", "a_delann")],
            [blue("👥 Mijozlar", "a_users"), blue("🧾 Buyurtmalar", "a_orders")],
            [blue("💬 Xabarlar", "a_msgs")],
            [blue("🏠 Asosiy menyu", "a_home")],
        ]),
    )


def ask_phone(chat_id):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True))
    bot.send_message(
        chat_id,
        "Assalomu alaykum! Botdan foydalanish uchun telefon raqamingizni yuboring 👇",
        reply_markup=kb,
    )


# ---------- Ro'yxatdan o'tkazish ----------
@bot.message_handler(commands=["start"])
def cmd_start(m):
    state.pop(m.from_user.id, None)
    if get_user(m.from_user.id):
        main_menu(m.chat.id)
    else:
        ask_phone(m.chat.id)


@bot.message_handler(content_types=["contact"])
def on_contact(m):
    c = m.contact
    if c.user_id != m.from_user.id:
        bot.send_message(m.chat.id, "Iltimos, faqat o'z telefon raqamingizni yuboring (tugma orqali).")
        return
    if not get_user(m.from_user.id):
        name = " ".join(filter(None, [m.from_user.first_name, m.from_user.last_name]))
        db["users"].append({
            "id": m.from_user.id,
            "name": name,
            "username": m.from_user.username or "",
            "phone": c.phone_number,
            "date": now_ts(),
        })
        save()
    bot.send_message(m.chat.id, "✅ Rahmat! Ro'yxatdan o'tdingiz.", reply_markup=types.ReplyKeyboardRemove())
    main_menu(m.chat.id)


@bot.message_handler(commands=["menu", "cancel"])
def cmd_menu(m):
    if not get_user(m.from_user.id):
        return ask_phone(m.chat.id)
    state.pop(m.from_user.id, None)
    main_menu(m.chat.id)


@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    if not get_user(m.from_user.id):
        return ask_phone(m.chat.id)
    if not is_admin(m.from_user.id):
        return bot.send_message(m.chat.id, "⛔ Siz admin emassiz.")
    state.pop(m.from_user.id, None)
    admin_menu(m.chat.id)


# ---------- Tugmalar ----------
ADMIN_PREFIXES = ("a_", "da_", "dp_", "dn_", "reply_")


@bot.callback_query_handler(func=lambda c: True)
def on_callback(c):
    uid = c.from_user.id
    chat = c.message.chat.id
    d = c.data
    bot.answer_callback_query(c.id)

    if not get_user(uid):
        return ask_phone(chat)

    if d.startswith(ADMIN_PREFIXES) and not is_admin(uid):
        return bot.send_message(chat, "⛔ Ruxsat yo'q.")

    # --- Foydalanuvchi menyusi ---
    if d == "m_projects":
        if not db["projects"]:
            return bot.send_message(chat, "📁 Hozircha loyihalar yo'q.")
        text = "\n\n".join(
            f"{i + 1}. {p['title']}\n{p['desc']}" + (f"\n🔗 {p['link']}" if p["link"] else "")
            for i, p in enumerate(db["projects"])
        )
        send_long(chat, "📁 Loyihalarimiz:\n\n" + text)

    elif d == "m_ann":
        if not db["announcements"]:
            return bot.send_message(chat, "📢 Hozircha e'lonlar yo'q.")
        text = "\n\n".join(f"📌 {a['text']}\n🕒 {fmt_date(a['date'])}" for a in db["announcements"])
        send_long(chat, "📢 E'lonlar:\n\n" + text)

    elif d == "m_prices":
        bot.send_message(chat, PRICES)

    elif d == "m_contact":
        state[uid] = {"step": "contact_admin"}
        bot.send_message(chat, "✍️ Xabaringizni kiriting (bekor qilish: /cancel):")

    elif d == "m_order":
        bot.send_message(
            chat,
            "Nima buyurtma qilmoqchisiz?",
            reply_markup=markup([
                [red("🌐 Web sayt (90–150 ming)", "o_web")],
                [red("🤖 Telegram bot (100–200 ming)", "o_bot")],
            ]),
        )

    elif d in ("o_web", "o_bot"):
        typ = "Web sayt" if d == "o_web" else "Telegram bot"
        state[uid] = {"step": "order_desc", "type": typ}
        bot.send_message(chat, f"{typ} haqida batafsil yozing (nima kerak, qanday funksiyalar...). Bekor qilish: /cancel")

    # --- Admin menyusi ---
    elif d == "a_home":
        main_menu(chat)

    elif d == "a_addadmin":
        state[uid] = {"step": "add_admin"}
        bot.send_message(chat, "Yangi adminning Telegram ID raqamini yuboring (bekor qilish: /cancel):")

    elif d == "a_deladmin":
        if not db["admins"]:
            return bot.send_message(chat, "Qo'shimcha adminlar yo'q.")
        bot.send_message(
            chat, "Qaysi adminni o'chirasiz?",
            reply_markup=markup([[blue(f"❌ {a['id']}", f"da_{a['id']}")] for a in db["admins"]]),
        )

    elif d.startswith("da_"):
        aid = int(d[3:])
        db["admins"] = [a for a in db["admins"] if a["id"] != aid]
        save()
        bot.send_message(chat, f"✅ Admin {aid} o'chirildi.")

    elif d == "a_addproj":
        state[uid] = {"step": "proj_title"}
        bot.send_message(chat, "Loyiha nomini kiriting (bekor qilish: /cancel):")

    elif d == "a_delproj":
        if not db["projects"]:
            return bot.send_message(chat, "Loyihalar yo'q.")
        bot.send_message(
            chat, "Qaysi loyihani o'chirasiz?",
            reply_markup=markup([[blue(f"❌ {p['title']}", f"dp_{p['id']}")] for p in db["projects"]]),
        )

    elif d.startswith("dp_"):
        db["projects"] = [p for p in db["projects"] if str(p["id"]) != d[3:]]
        save()
        bot.send_message(chat, "✅ Loyiha o'chirildi.")

    elif d == "a_addann":
        state[uid] = {"step": "ann_text"}
        bot.send_message(chat, "E'lon matnini kiriting (bekor qilish: /cancel):")

    elif d == "a_delann":
        if not db["announcements"]:
            return bot.send_message(chat, "E'lonlar yo'q.")
        bot.send_message(
            chat, "Qaysi e'lonni o'chirasiz?",
            reply_markup=markup([[blue(f"❌ {a['text'][:40]}", f"dn_{a['id']}")] for a in db["announcements"]]),
        )

    elif d.startswith("dn_"):
        db["announcements"] = [a for a in db["announcements"] if str(a["id"]) != d[3:]]
        save()
        bot.send_message(chat, "✅ E'lon o'chirildi.")

    elif d == "a_users":
        if not db["users"]:
            return bot.send_message(chat, "Mijozlar yo'q.")
        text = "\n\n".join(
            f"{i + 1}.\n{user_info(u)}\n🕒 {fmt_date(u['date'])}" for i, u in enumerate(db["users"])
        )
        send_long(chat, f"👥 Mijozlar ({len(db['users'])}):\n\n" + text)

    elif d == "a_orders":
        if not db["orders"]:
            return bot.send_message(chat, "Buyurtmalar yo'q.")
        parts = []
        for i, o in enumerate(db["orders"]):
            u = get_user(o["userId"])
            parts.append(
                f"{i + 1}. {o['type']}\n{user_info(u) if u else 'ID: ' + str(o['userId'])}\n"
                f"📝 {o['text']}\n🕒 {fmt_date(o['date'])}"
            )
        send_long(chat, f"🧾 Buyurtmalar ({len(db['orders'])}):\n\n" + "\n\n".join(parts))

    elif d == "a_msgs":
        if not db["messages"]:
            return bot.send_message(chat, "Xabarlar yo'q.")
        parts = []
        for i, mm in enumerate(db["messages"]):
            u = get_user(mm["userId"])
            parts.append(
                f"{i + 1}. {user_info(u) if u else 'ID: ' + str(mm['userId'])}\n"
                f"💬 {mm['text']}\n🕒 {fmt_date(mm['date'])}"
            )
        send_long(chat, f"💬 Xabarlar ({len(db['messages'])}):\n\n" + "\n\n".join(parts))

    elif d.startswith("reply_"):
        state[uid] = {"step": "reply", "to": int(d[6:])}
        bot.send_message(chat, "Javobingizni yozing (bekor qilish: /cancel):")


# ---------- Matnli xabarlar (holatlar) ----------
@bot.message_handler(content_types=["text"])
def on_text(m):
    uid = m.from_user.id
    chat = m.chat.id
    text = m.text.strip()

    if not get_user(uid):
        return ask_phone(chat)

    st = state.get(uid)
    if not st:
        return main_menu(chat)

    step = st["step"]

    if step == "contact_admin":
        db["messages"].append({"id": now_ts(), "userId": uid, "text": text, "date": now_ts()})
        save()
        state.pop(uid, None)
        notify_admins(
            f"✉️ Yangi xabar:\n\n{user_info(get_user(uid))}\n\n💬 {text}",
            markup([[blue("↩️ Javob berish", f"reply_{uid}")]]),
        )
        bot.send_message(chat, "✅ Xabaringiz adminga yuborildi.")
        main_menu(chat)

    elif step == "order_desc":
        db["orders"].append({"id": now_ts(), "userId": uid, "type": st["type"], "text": text, "date": now_ts()})
        save()
        state.pop(uid, None)
        notify_admins(
            f"🛒 Yangi buyurtma: {st['type']}\n\n{user_info(get_user(uid))}\n\n📝 {text}",
            markup([[blue("↩️ Javob berish", f"reply_{uid}")]]),
        )
        bot.send_message(chat, "✅ Buyurtmangiz qabul qilindi. Tez orada admin siz bilan bog'lanadi.")
        main_menu(chat)

    elif step == "reply" and is_admin(uid):
        try:
            bot.send_message(st["to"], f"📩 Admindan javob:\n\n{text}")
            bot.send_message(chat, "✅ Javob yuborildi.")
        except Exception:
            bot.send_message(chat, "❌ Yuborib bo'lmadi (foydalanuvchi botni bloklagan bo'lishi mumkin).")
        state.pop(uid, None)

    elif step == "add_admin" and is_admin(uid):
        if not text.isdigit():
            return bot.send_message(chat, "❌ ID faqat raqamlardan iborat bo'lishi kerak. Qayta kiriting:")
        new_id = int(text)
        state.pop(uid, None)
        if is_admin(new_id):
            return bot.send_message(chat, "Bu foydalanuvchi allaqachon admin.")
        db["admins"].append({"id": new_id, "date": now_ts()})
        save()
        bot.send_message(chat, f"✅ {new_id} admin qilib qo'shildi.")

    elif step == "proj_title" and is_admin(uid):
        state[uid] = {"step": "proj_desc", "title": text}
        bot.send_message(chat, "Loyiha tavsifini kiriting:")

    elif step == "proj_desc" and is_admin(uid):
        state[uid] = {"step": "proj_link", "title": st["title"], "desc": text}
        bot.send_message(chat, 'Loyiha havolasini kiriting (yo\'q bo\'lsa "-" yuboring):')

    elif step == "proj_link" and is_admin(uid):
        db["projects"].append({
            "id": now_ts(),
            "title": st["title"],
            "desc": st["desc"],
            "link": "" if text == "-" else text,
        })
        save()
        state.pop(uid, None)
        bot.send_message(chat, "✅ Loyiha qo'shildi.")

    elif step == "ann_text" and is_admin(uid):
        db["announcements"].append({"id": now_ts(), "text": text, "date": now_ts()})
        save()
        state.pop(uid, None)
        bot.send_message(chat, "✅ E'lon qo'shildi.")


if __name__ == "__main__":
    start_health_server()
    print("Bot ishga tushdi ✅")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"Telegram API bilan aloqa uzildi: {e}. 5 soniyadan keyin qayta ulanaman.")
            time.sleep(5)
