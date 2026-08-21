import asyncio
import logging
import os
import sqlite3
import re
import hashlib
from io import BytesIO
from datetime import datetime, timedelta
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.enums import ChatAction, ChatMemberStatus
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logging.warning("groq o'rnatilmagan")

# === SOZLAMALAR ===
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = 5492502957
ADMIN_USERNAME = "@Javoh_123"
CHANNEL_USERNAME = "@Qoshiqyaratish1"  
CHANNEL_LINK = "https://t.me/Qoshiqyaratish1"  
SONG_PRICE_SHORT = 15000   # 1-2 daqiqalik
SONG_PRICE_FULL = 20000    # 3 daqiqalik
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip().strip('"').strip("'")
GROQ_TEXT_MODEL = "qwen/qwen3.6-27b"        # matn (AI Yordamchi, qo'shiq matni) - kop tillilikda kuchli
GROQ_VISION_MODEL = "qwen/qwen3.6-27b"      # rasm+matn (chekni tekshirish)
groq_client = None
if GROQ_AVAILABLE and GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
    _masked = f"{GROQ_API_KEY[:6]}...{GROQ_API_KEY[-4:]} (uzunligi: {len(GROQ_API_KEY)})" if len(GROQ_API_KEY) > 10 else "juda qisqa/notogri!"
    logging.info(f"[GROQ] Kalit yuklandi: {_masked}")
elif GROQ_AVAILABLE and not GROQ_API_KEY:
    logging.error("[GROQ] GROQ_API_KEY topilmadi (bosh qiymat)! Environment Variables'ni tekshiring.")

# === TARIFLAR ===
PLUS_PRICE = 40000
PLUS_DURATION_DAYS = 7
PLUS_SONGS = 5
PLUS_COOLDOWN_HOURS = 1      # har bir qo'shiq orasidagi kutish
PLUS_BONUS_DAYS = 2          # tarif tugagach bonus kunlar soni
PLUS_BONUS_PER_DAY = 1       # bonus kunda nechta 30s qo'shiq

PRO_PRICE = 65000
PRO_DURATION_DAYS = 7
PRO_SONGS = 7
PRO_COOLDOWN_HOURS = 1       # har bir qo'shiq orasidagi kutish
PRO_BONUS_DAYS = 2
PRO_BONUS_PER_DAY = 1        # bonus kunda nechta 1-2 daqiqalik qo'shiq

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi! .env faylida BOT_TOKEN=... yozing")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# === DATABASE ===
# === MA'LUMOTLAR BAZASI (doimiy joy) ===
# DB_PATH ni .env orqali sozlash mumkin. Agar server/hosting har deploy'da
# fayllarni tozalasa (Railway, Render, Docker va h.k.), .env ichida
# DB_PATH ni ULANGAN DISK (persistent volume) yo'liga ko'rsating, masalan:
#   DB_PATH=/data/music_bot.db
# Aks holda bot yangilanganda (qayta deploy qilinganda) statistika 0 dan boshlanadi.
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_bot.db"))

_db_is_new = not os.path.exists(DB_PATH)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_db_status_msg = "YANGI (yangi yaratildi)" if _db_is_new else "ESKI (mavjud, ma'lumotlar saqlanadi)"
logging.info(f"[DB] Ishlatilayotgan baza fayli: {DB_PATH} | Holati: {_db_status_msg}")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    fullname TEXT,
    username TEXT,
    balance INTEGER DEFAULT 0,
    total_paid INTEGER DEFAULT 0,
    used_secret INTEGER DEFAULT 0,
    pending_deposit INTEGER DEFAULT 0
)
""")
for col in ["used_secret", "pending_deposit"]:
    try:
        cursor.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass

# --- Tarif va bloklanish ustunlari ---
TARIFF_COLUMNS = {
    "tariff": "TEXT DEFAULT 'none'",              # 'none' | 'plus' | 'pro'
    "tariff_expires": "TEXT",                      # tarif tugash sanasi (ISO)
    "tariff_songs_left": "INTEGER DEFAULT 0",       # tarif davomida qolgan qo'shiqlar
    "tariff_last_song_at": "TEXT",                  # oxirgi qo'shiq buyurtma vaqti (Pro cooldown uchun)
    "bonus_until": "TEXT",                          # bonus davri tugash sanasi (ISO)
    "bonus_last_date": "TEXT",                      # bonusdan oxirgi foydalanilgan kun (YYYY-MM-DD)
    "bonus_used_today": "INTEGER DEFAULT 0",        # bugun ishlatilgan bonus soni
    "is_blocked": "INTEGER DEFAULT 0",               # foydalanuvchi botni bloklaganmi
}
for col, col_type in TARIFF_COLUMNS.items():
    try:
        cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
        conn.commit()
    except Exception:
        pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    description TEXT,
    file_id TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS promo_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    amount INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS promo_redemptions (
    user_id INTEGER PRIMARY KEY,
    code TEXT,
    amount INTEGER,
    used_date TEXT,
    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    file_id TEXT,
    photo_hash TEXT UNIQUE
)
""")
for col in ["file_id", "photo_hash"]:
    try:
        cursor.execute(f"ALTER TABLE deposits ADD COLUMN {col} TEXT")
        conn.commit()
    except Exception:
        pass
try:
    cursor.execute("ALTER TABLE deposits ADD COLUMN transaction_id TEXT")
    conn.commit()
except Exception:
    pass
conn.commit()

# === GROQ AI - CHEKNI TAHLIL QILISH ===
async def analyze_receipt_with_groq(image_content: bytes, expected_amount: int):
    """
    Chek rasmini Groq vision modeli orqali to'g'ridan-to'g'ri tahlil qiladi.
    Qaytaradi: dict {
        'valid': bool,           # chek haqiqiy to'lov cheki ko'rinishidami
        'amount': int|None,      # aniqlangan summa
        'transaction_id': str|None,  # tranzaksiya/operatsiya raqami
        'date': str|None,        # sana (agar bo'lsa)
        'reason': str            # agar valid=False bo'lsa sabab
    }
    """
    default_fail = {"valid": False, "amount": None, "transaction_id": None, "date": None,
                     "reason": "Rasmdan chek ma'lumotlari topilmadi. Aniqroq rasm yuboring."}
    if not GROQ_AVAILABLE or not groq_client:
        return default_fail
    try:
        import base64
        import json
        b64_image = base64.b64encode(image_content).decode("utf-8")
        prompt = (
            "Bu rasm to'lov cheki (bank/tolov ilovasi skrinshoti yoki PDF cheki) bo'lishi mumkin. "
            "Rasmni tahlil qil va FAQAT quyidagi JSON formatda javob ber, hech qanday boshqa matn yozma:\n"
            "{\n"
            '  "valid": true yoki false (bu haqiqatan tolov cheki/kvitansiyasi bolsa true),\n'
            '  "amount": summa raqam korinishida (som/UZS), agar topilmasa null,\n'
            '  "transaction_id": tranzaksiya yoki operatsiya raqami (agar bor bolsa), aks holda null,\n'
            '  "date": sana matn korinishida (agar bor bolsa), aks holda null,\n'
            '  "reason": agar valid=false bolsa qisqa sabab, aks holda bosh satr\n'
            "}"
        )
        response = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model=GROQ_VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
                    ],
                }
            ],
            response_format={"type": "json_object"},
        )
        raw_text = response.choices[0].message.content.strip()
        raw_text = re.sub(r'^```json\s*|\s*```$', '', raw_text, flags=re.MULTILINE).strip()
        result = json.loads(raw_text)

        amount = result.get("amount")
        try:
            amount = int(amount) if amount is not None else None
        except (ValueError, TypeError):
            amount = None

        return {
            "valid": bool(result.get("valid")) and amount is not None,
            "amount": amount,
            "transaction_id": str(result.get("transaction_id")) if result.get("transaction_id") else None,
            "date": result.get("date"),
            "reason": result.get("reason") or default_fail["reason"],
        }
    except Exception as e:
        logging.error(f"Groq chek tahlili xatolik: {e}")
        try:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ <b>Groq AI xatolik (chekni tekshirish)</b>\n\n<code>{str(e)[:800]}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return default_fail

# === DB FUNKSIYALARI ===
def db_register_user(user_id, fullname, username):
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (user_id, fullname, username, balance, used_secret, pending_deposit) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, fullname, username, 0, 0, 0)
        )
        conn.commit()
        return True
    cursor.execute("UPDATE users SET fullname = ?, username = ? WHERE user_id = ?", (fullname, username, user_id))
    conn.commit()
    return False

def db_get_user(user_id):
    cursor.execute("SELECT balance, total_paid, username, fullname, used_secret, pending_deposit FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def db_get_user_full(user_id):
    cursor.execute("""
        SELECT balance, total_paid, username, fullname, used_secret, pending_deposit,
               tariff, tariff_expires, tariff_songs_left, tariff_last_song_at,
               bonus_until, bonus_last_date, bonus_used_today, is_blocked
        FROM users WHERE user_id = ?
    """, (user_id,))
    return cursor.fetchone()

def db_set_blocked(user_id, blocked: bool):
    cursor.execute("UPDATE users SET is_blocked = ? WHERE user_id = ?", (1 if blocked else 0, user_id))
    conn.commit()

async def safe_send_message(user_id, text, **kwargs):
    """
    Foydalanuvchiga xabar yuborishga harakat qiladi. Agar u botni bloklagan bo'lsa
    (TelegramForbiddenError), buni bazada is_blocked=1 deb belgilaydi - shu orqali
    statistikada "faol a'zolar" soni avtomatik kamayadi. Muvaffaqiyatli yuborilsa,
    oldin bloklangan deb belgilangan bo'lsa ham qayta faol deb belgilaydi.
    Qaytaradi: True (yuborildi) yoki False (bloklagan/xato).
    """
    try:
        await bot.send_message(user_id, text, **kwargs)
        db_set_blocked(user_id, False)
        return True
    except TelegramForbiddenError:
        db_set_blocked(user_id, True)
        return False
    except Exception as e:
        logging.error(f"safe_send_message xatolik ({user_id}): {e}")
        return False

def db_activate_tariff(user_id, tariff: str):
    now = datetime.now()
    if tariff == "plus":
        expires = now + timedelta(days=PLUS_DURATION_DAYS)
        bonus_until = expires + timedelta(days=PLUS_BONUS_DAYS)
        songs = PLUS_SONGS
    else:
        expires = now + timedelta(days=PRO_DURATION_DAYS)
        bonus_until = expires + timedelta(days=PRO_BONUS_DAYS)
        songs = PRO_SONGS
    cursor.execute("""
        UPDATE users SET tariff = ?, tariff_expires = ?, tariff_songs_left = ?,
               tariff_last_song_at = NULL, bonus_until = ?, bonus_last_date = NULL, bonus_used_today = 0
        WHERE user_id = ?
    """, (tariff, expires.isoformat(), songs, bonus_until.isoformat(), user_id))
    conn.commit()

def db_decrement_tariff_song(user_id):
    cursor.execute("""
        UPDATE users SET tariff_songs_left = tariff_songs_left - 1, tariff_last_song_at = ?
        WHERE user_id = ?
    """, (datetime.now().isoformat(), user_id))
    conn.commit()

def db_use_bonus_song(user_id, today_str):
    cursor.execute("""
        UPDATE users SET bonus_last_date = ?, bonus_used_today = bonus_used_today + 1
        WHERE user_id = ?
    """, (today_str, user_id))
    conn.commit()

def db_reset_tariff(user_id):
    cursor.execute("""
        UPDATE users SET tariff = 'none', tariff_expires = NULL, tariff_songs_left = 0,
               tariff_last_song_at = NULL, bonus_until = NULL, bonus_last_date = NULL, bonus_used_today = 0
        WHERE user_id = ?
    """, (user_id,))
    conn.commit()

def get_tariff_status(user_id):
    """
    Foydalanuvchining joriy tarif holatini aniqlaydi va kerak bo'lsa DB'ni tozalaydi.
    Qaytaradi: dict {
        'tariff': 'none'|'plus'|'pro',
        'phase': 'active'|'bonus'|'expired'|'none',
        'songs_left': int,
        'expires': datetime|None,
        'bonus_until': datetime|None,
        'cooldown_ok': bool,
        'cooldown_remaining': timedelta|None,
        'bonus_available_today': bool,
    }
    """
    row = db_get_user_full(user_id)
    result = {
        "tariff": "none", "phase": "none", "songs_left": 0,
        "expires": None, "bonus_until": None,
        "cooldown_ok": True, "cooldown_remaining": None,
        "bonus_available_today": False,
    }
    if not row:
        return result

    (balance, total_paid, username, fullname, used_secret, pending_deposit,
     tariff, tariff_expires, songs_left, last_song_at,
     bonus_until, bonus_last_date, bonus_used_today, is_blocked) = row

    if not tariff or tariff == "none":
        return result

    now = datetime.now()
    expires_dt = datetime.fromisoformat(tariff_expires) if tariff_expires else None
    bonus_until_dt = datetime.fromisoformat(bonus_until) if bonus_until else None

    result["tariff"] = tariff
    result["expires"] = expires_dt
    result["bonus_until"] = bonus_until_dt
    result["songs_left"] = songs_left or 0

    # Tarif butunlay tugagan (bonus davri ham tugagan) bo'lsa - tozalaymiz
    if bonus_until_dt and now > bonus_until_dt:
        db_reset_tariff(user_id)
        return {
            "tariff": "none", "phase": "none", "songs_left": 0,
            "expires": None, "bonus_until": None,
            "cooldown_ok": True, "cooldown_remaining": None,
            "bonus_available_today": False,
        }

    # Asosiy tarif faol va qo'shiq qolgan
    if expires_dt and now <= expires_dt and (songs_left or 0) > 0:
        result["phase"] = "active"
        if last_song_at:
            cooldown_hours = PLUS_COOLDOWN_HOURS if tariff == "plus" else PRO_COOLDOWN_HOURS
            last_dt = datetime.fromisoformat(last_song_at)
            cooldown_end = last_dt + timedelta(hours=cooldown_hours)
            if now < cooldown_end:
                result["cooldown_ok"] = False
                result["cooldown_remaining"] = cooldown_end - now
        return result

    # Bonus davri (asosiy tarif tugagan yoki qo'shiqlar tugagan, lekin bonus muddati ichida)
    if bonus_until_dt and now <= bonus_until_dt:
        result["phase"] = "bonus"
        today_str = now.strftime("%Y-%m-%d")
        per_day = PLUS_BONUS_PER_DAY if tariff == "plus" else PRO_BONUS_PER_DAY
        used_today = bonus_used_today if bonus_last_date == today_str else 0
        result["bonus_available_today"] = used_today < per_day
        return result

    result["phase"] = "expired"
    return result

def db_add_balance(user_id, amount):
    cursor.execute("UPDATE users SET balance = balance + ?, total_paid = total_paid + ? WHERE user_id = ?", (amount, amount, user_id))
    conn.commit()

def db_deduct_balance(user_id, amount):
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()

def db_mark_secret_used(user_id):
    cursor.execute("UPDATE users SET used_secret = 1 WHERE user_id = ?", (user_id,))
    conn.commit()

def db_set_pending_deposit(user_id, amount):
    cursor.execute("UPDATE users SET pending_deposit = ? WHERE user_id = ?", (user_id, amount))
    conn.commit()

def db_clear_pending_deposit(user_id):
    cursor.execute("UPDATE users SET pending_deposit = 0 WHERE user_id = ?", (user_id,))
    conn.commit()

def db_get_stats():
    cursor.execute("SELECT COUNT(*), SUM(total_paid) FROM users WHERE is_blocked = 0 OR is_blocked IS NULL")
    active_count, total = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
    blocked_count = cursor.fetchone()[0]
    return active_count, total, blocked_count

def db_get_all_user_ids():
    cursor.execute("SELECT user_id FROM users")
    return [row[0] for row in cursor.fetchall()]

def db_get_samples():
    cursor.execute("SELECT id, title, description, file_id FROM samples")
    return cursor.fetchall()

def db_add_sample(title, description, file_id):
    cursor.execute("INSERT INTO samples (title, description, file_id) VALUES (?, ?, ?)", (title, description, file_id))
    conn.commit()

def db_delete_sample(sample_id):
    cursor.execute("DELETE FROM samples WHERE id = ?", (sample_id,))
    conn.commit()
    return cursor.rowcount > 0

def db_add_promo_code(code, amount):
    try:
        cursor.execute("INSERT INTO promo_codes (code, amount) VALUES (?, ?)", (code, amount))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False

def db_use_promo_code(code, user_id):
    """
    Promokodni bir martalik qilib ishlatadi VA har bir foydalanuvchi FAQAT
    KUNIGA BITTA marta (qaysi kod bo'lishidan qatiy nazar) promokoddan
    foydalana olishini taminlaydi. Aks holda bitta odam kanalga tashlangan
    barcha promokodlarni ketma-ket yigib olishi mumkin edi.
    Qaytaradi: (amount, None) - muvaffaqiyatli
               (None, "limit") - bugun allaqachon promokod ishlatgan
               (None, "invalid") - kod notogri/topilmadi
    Ikkala holatda ham (limit yoki invalid) chaqiruvchi hech qanday javob
    yubormasligi kerak - foydalanuvchiga sabab bildirilmaydi.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT used_date FROM promo_redemptions WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row and row[0] == today:
        return None, "limit"
    cursor.execute("SELECT id, amount FROM promo_codes WHERE code = ?", (code,))
    prow = cursor.fetchone()
    if not prow:
        return None, "invalid"
    promo_id, amount = prow
    cursor.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
    if cursor.rowcount == 0:
        conn.commit()
        return None, "invalid"
    cursor.execute(
        "INSERT INTO promo_redemptions (user_id, code, amount, used_date) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET code=excluded.code, amount=excluded.amount, "
        "used_date=excluded.used_date, used_at=CURRENT_TIMESTAMP",
        (user_id, code, amount, today)
    )
    conn.commit()
    return amount, None

def get_image_hash(image_content):
    return hashlib.md5(image_content).hexdigest()

def db_check_duplicate_hash(photo_hash):
    cursor.execute("SELECT id, user_id, status FROM deposits WHERE photo_hash = ?", (photo_hash,))
    return cursor.fetchone()

def db_check_duplicate_transaction(transaction_id):
    if not transaction_id:
        return None
    cursor.execute("SELECT id, user_id, status FROM deposits WHERE transaction_id = ?", (transaction_id,))
    return cursor.fetchone()

def db_add_deposit(user_id, amount, file_id, photo_hash, transaction_id=None):
    try:
        cursor.execute(
            "INSERT INTO deposits (user_id, amount, status, file_id, photo_hash, transaction_id) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, amount, 'pending', file_id, photo_hash, transaction_id)
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logging.error(f"db_add_deposit xatolik: {e}")
        conn.rollback()
        return None

def db_update_deposit_status(deposit_id, status):
    cursor.execute("UPDATE deposits SET status = ? WHERE id = ?", (status, deposit_id))
    conn.commit()

def db_get_user_deposit_count(user_id):
    cursor.execute("SELECT COUNT(*) FROM deposits WHERE user_id = ? AND status = 'accepted'", (user_id,))
    return cursor.fetchone()[0]

# === CHEKNI TEKSHIRISH (faqat AI - Gemini orqali) ===
async def check_receipt_photo(file_id, expected_amount):
    photo_hash = None
    try:
        file_info = await bot.get_file(file_id)
        destination = BytesIO()
        await bot.download_file(file_info.file_path, destination)
        image_content = destination.getvalue()

        photo_hash = get_image_hash(image_content)

        # 1) Rasm aynan bir xil (pixel-ma-pixel) qayta yuborilganmi?
        existing = db_check_duplicate_hash(photo_hash)
        if existing:
            _, _, status = existing
            if status == 'accepted':
                return False, "❌ Bu chek allaqachon ishlatilgan!", photo_hash, None
            elif status == 'pending':
                return False, "⏳ Bu chek allaqachon tekshirilmoqda!", photo_hash, None
            else:
                return False, "❌ Bu chek avval rad etilgan!", photo_hash, None

        if not GROQ_AVAILABLE or not groq_client:
            return False, "❌ Chekni avtomatik tekshirish hozircha ishlamayapti.", photo_hash, None

        # 2) Groq AI orqali rasmni tahlil qilamiz (OCR o'rniga to'g'ridan-to'g'ri AI)
        result = await analyze_receipt_with_groq(image_content, expected_amount)

        if not result["valid"]:
            return False, f"❌ {result['reason']}", photo_hash, result.get("transaction_id")

        transaction_id = result.get("transaction_id")

        # 3) Bir xil to'lov turli ko'rinishda (screenshot va PDF, masalan) yuborilganmi?
        # Bu tranzaksiya raqami orqali aniqlanadi - rasm boshqacha ko'rinsa ham ID bir xil bo'ladi.
        if transaction_id:
            dup_tx = db_check_duplicate_transaction(transaction_id)
            if dup_tx:
                _, _, status = dup_tx
                if status == 'accepted':
                    return False, "❌ Bu to'lov (tranzaksiya) allaqachon ishlatilgan!", photo_hash, transaction_id
                elif status == 'pending':
                    return False, "⏳ Bu to'lov allaqachon tekshirilmoqda!", photo_hash, transaction_id
                else:
                    return False, "❌ Bu to'lov avval rad etilgan!", photo_hash, transaction_id

        detected_amount = result.get("amount")
        if not detected_amount:
            return False, "❌ Rasmdan summa aniqlanmadi. Aniqroq rasm yuboring.", photo_hash, transaction_id

        if detected_amount >= int(expected_amount * 0.95):
            return True, detected_amount, photo_hash, transaction_id
        else:
            return False, (
                f"❌ Summa yetarli emas.\n"
                f"Chekda: {detected_amount:,} so'm\n"
                f"Kerakli: {expected_amount:,} so'm"
            ), photo_hash, transaction_id

    except Exception as e:
        logging.error(f"check_receipt_photo xatolik: {e}")
        if photo_hash is None:
            photo_hash = hashlib.md5(str(file_id).encode()).hexdigest()
        return False, "❌ Chekni tekshirishda xatolik yuz berdi. Qaytadan urinib ko'ring.", photo_hash, None

class CreateSong(StatesGroup):
    waiting_for_type = State()      # 1-2 daq / 3 daq (faqat oddiy foydalanuvchi uchun)
    waiting_for_topic = State()     # mavzu tugmalari
    waiting_for_custom_topic = State()   # "O'z g'oyam" tanlansa, matn kutiladi
    waiting_for_voice = State()     # ovoz turi
    waiting_for_has_text = State()  # matn bor/yo'q
    waiting_for_text = State()      # foydalanuvchi o'z matnini yozadi
    waiting_for_ai_details = State()  # AI yozishi uchun qoshimcha tafsilot (kim haqida, nima uchun)
    waiting_for_genre = State()
    waiting_for_custom_genre = State()   # "O'z janrim" tanlansa
    waiting_for_ai_approval = State()    # AI yozgan matnni foydalanuvchi tasdiqlaydimi

class DepositState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_receipt = State()

class AdminActions(StatesGroup):
    waiting_for_broadcast_choice = State()
    waiting_for_user_id_m = State()
    waiting_for_message = State()
    waiting_for_user_id_p = State()
    waiting_for_money = State()
    waiting_for_sample_title = State()
    waiting_for_sample_desc = State()
    waiting_for_sample_file = State()
    waiting_for_promo_code = State()
    waiting_for_promo_amount = State()

# === KLAVIATURALAR ===
def get_main_menu(user_id):
    buttons = [
        [KeyboardButton(text="🎵 Qo'shiq yaratish"), KeyboardButton(text="🎼 Qo'shiq namunaviy")],
        [KeyboardButton(text="📊 Balans"), KeyboardButton(text="💳 Pul kiritish")],
        [KeyboardButton(text="⭐ Tariflar"), KeyboardButton(text="🤖 AI Yordamchi")],
        [KeyboardButton(text="👨‍💼 Admin")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton(text="🔐 Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_admin_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💰 Pul berish"), KeyboardButton(text="✉️ Xabar yuborish")],
        [KeyboardButton(text="📈 Statistika"), KeyboardButton(text="🎵 Namuna qo'shish")],
        [KeyboardButton(text="🗑 Namuna o'chirish"), KeyboardButton(text="📋 Baza")],
        [KeyboardButton(text="🎁 Promokod qo'shish")],
        [KeyboardButton(text="⬅️ Bosh menyu")]
    ], resize_keyboard=True)

def get_song_type_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=f"⚡ 1-2 daqiqalik — {SONG_PRICE_SHORT:,} so'm")],
        [KeyboardButton(text=f"🎶 3 daqiqalik — {SONG_PRICE_FULL:,} so'm")]
    ], resize_keyboard=True)

def get_tariff_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐ Plus — {PLUS_PRICE:,} so'm / {PLUS_DURATION_DAYS} kun", callback_data="buy_plus")],
        [InlineKeyboardButton(text=f"💎 Pro — {PRO_PRICE:,} so'm / {PRO_DURATION_DAYS} kun", callback_data="buy_pro")]
    ])

def get_extra_song_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💰 Hoziroq sotib olish — {SONG_PRICE_SHORT:,} so'm", callback_data="extra_song_buy")],
        [InlineKeyboardButton(text="⏳ Kutaman", callback_data="extra_song_wait")]
    ])

def get_song_topic_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎂 Tug'ilgan kun", callback_data="topic_birthday"),
         InlineKeyboardButton(text="💖 Sevgi izhori", callback_data="topic_love")],
        [InlineKeyboardButton(text="🥳 Bazm / kecha", callback_data="topic_party"),
         InlineKeyboardButton(text="💍 To'y", callback_data="topic_wedding")],
        [InlineKeyboardButton(text="🤝 Dalda berish", callback_data="topic_support"),
         InlineKeyboardButton(text="🤪 Hazil", callback_data="topic_joke")],
        [InlineKeyboardButton(text="💼 Korporativ", callback_data="topic_corporate"),
         InlineKeyboardButton(text="🎧 Ko'ngil uchun", callback_data="topic_fun")],
        [InlineKeyboardButton(text="✍️ O'z g'oyam", callback_data="topic_custom")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="song_cancel")]
    ])

def get_voice_type_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👦 Erkak ovozi", callback_data="voice_male"),
         InlineKeyboardButton(text="👩 Ayol ovozi", callback_data="voice_female")],
        [InlineKeyboardButton(text="👫 Duet", callback_data="voice_duet"),
         InlineKeyboardButton(text="🎸 Instrumental", callback_data="voice_instrumental")],
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="song_back_topic"),
         InlineKeyboardButton(text="❌ Bekor qilish", callback_data="song_cancel")]
    ])

def get_has_text_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Ha, menda matn bor", callback_data="text_yes")],
        [InlineKeyboardButton(text="🤖 Yo'q, AI yozib bersin", callback_data="text_ai")],
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="song_back_voice"),
         InlineKeyboardButton(text="❌ Bekor qilish", callback_data="song_cancel")]
    ])

def get_genre_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 AI qaroriga ko'ra", callback_data="genre_ai")],
        [InlineKeyboardButton(text="🎤 Pop", callback_data="genre_pop"),
         InlineKeyboardButton(text="🎸 Rok", callback_data="genre_rock")],
        [InlineKeyboardButton(text="🎧 Rep", callback_data="genre_rap"),
         InlineKeyboardButton(text="💃 90-yillar Disko", callback_data="genre_disco")],
        [InlineKeyboardButton(text="🎻 Klassik", callback_data="genre_classic"),
         InlineKeyboardButton(text="🎷 Jaz", callback_data="genre_jazz")],
        [InlineKeyboardButton(text="🪕 Akustik", callback_data="genre_acoustic"),
         InlineKeyboardButton(text="🎵 O'z janrim", callback_data="genre_custom")],
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="song_back_text"),
         InlineKeyboardButton(text="❌ Bekor qilish", callback_data="song_cancel")]
    ])

def get_ai_text_approval_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ma'qul, davom etamiz", callback_data="ai_text_approve")],
        [InlineKeyboardButton(text="🔄 Qayta yozdirish", callback_data="ai_text_retry")],
        [InlineKeyboardButton(text="✍️ O'zim yozaman", callback_data="ai_text_own")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="song_cancel")]
    ])

TOPIC_LABELS = {
    "topic_birthday": "🎂 Tug'ilgan kun",
    "topic_love": "💖 Sevgi izhori",
    "topic_party": "🥳 Bazm / kecha",
    "topic_wedding": "💍 To'y",
    "topic_support": "🤝 Dalda berish",
    "topic_joke": "🤪 Hazil",
    "topic_corporate": "💼 Korporativ",
    "topic_fun": "🎧 Ko'ngil uchun",
    "topic_custom": "✍️ O'z g'oyam",
}
VOICE_LABELS = {
    "voice_male": "👦 Erkak ovozi",
    "voice_female": "👩 Ayol ovozi",
    "voice_duet": "👫 Duet",
    "voice_instrumental": "🎸 Instrumental",
}
GENRE_LABELS = {
    "genre_ai": "🤖 AI qaroriga ko'ra",
    "genre_pop": "🎤 Pop",
    "genre_rock": "🎸 Rok",
    "genre_rap": "🎧 Rep",
    "genre_disco": "💃 90-yillar Disko",
    "genre_classic": "🎻 Klassik",
    "genre_jazz": "🎷 Jaz",
    "genre_acoustic": "🪕 Akustik",
    "genre_custom": "🎵 O'z janrim",
}

async def call_groq_text(messages: list, max_tokens: int = 4096) -> str:
    """Groq matn chaqiruvi. reasoning_effort='none' bilan Qwen ichki fikrlashini
    butunlay ochirib qoyadi (aks holda uzun <think> jarayoni token limitini yeb,
    javobni bosh qoldirishi mumkin). Eski groq SDK bu parametrlarni tanimasa,
    ularsiz qayta uradi. Har doim <think>...</think> qoldigini tozalaydi (ehtiyot uchun)."""
    kwargs_variants = [
        {"reasoning_effort": "none", "reasoning_format": "hidden"},
        {"reasoning_format": "hidden"},
        {},
    ]
    response = None
    last_err = None
    for extra_kwargs in kwargs_variants:
        try:
            response = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model=GROQ_TEXT_MODEL,
                messages=messages,
                max_completion_tokens=max_tokens,
                **extra_kwargs,
            )
            break
        except TypeError as e:
            last_err = e
            logging.warning(f"Groq parametr qollab-quvvatlanmadi ({extra_kwargs}), keyingi variant sinalmoqda: {e}")
            continue
    if response is None:
        raise last_err or RuntimeError("Groq chaqiruvi muvaffaqiyatsiz")
    text = response.choices[0].message.content or ""
    # Ehtiyot chorasi: agar baribir <think> blok kelib qolsa, uni olib tashlaymiz
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    if not text:
        finish_reason = getattr(response.choices[0], "finish_reason", "noma'lum")
        raw_len = len(response.choices[0].message.content or "")
        logging.error(f"Groq bosh javob qaytardi. finish_reason={finish_reason}, xom_matn_uzunligi={raw_len}")
    return text

def strip_markdown_symbols(text: str) -> str:
    """AI ba'zan qoidaga qaramay Markdown belgi qoldirib ketishi mumkin — ehtiyot
    uchun kod darajasida ham tozalaymiz (Telegram'da xom ** va * korinmasin)."""
    if not text:
        return text
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)   # **qalin** -> qalin
    text = re.sub(r'(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)', r'\1', text)  # *kursiv* -> kursiv
    text = re.sub(r'^\s*[\*•]\s+', '- ', text, flags=re.MULTILINE)      # * royxat -> - royxat
    text = re.sub(r'`([^`]+)`', r'\1', text)        # `kod` -> kod
    text = re.sub(r'^\s*#{1,6}\s*', '', text, flags=re.MULTILINE)       # # sarlavha -> sarlavha
    return text

def remove_apostrophe_letters(text: str) -> str:
    """AI yozgan qo'shiq matnida o' va g' harflarini oddiy o/g bilan almashtiradi."""
    if not text:
        return text
    replacements = {
        "o'": "o", "O'": "O", "g'": "g", "G'": "G",
        "oʻ": "o", "Oʻ": "O", "gʻ": "g", "Gʻ": "G",
        "o‘": "o", "O‘": "O", "g‘": "g", "G‘": "G",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

async def generate_song_lyrics(topic: str, voice_type: str, genre: str, extra_info: str = "") -> str:
    """Groq (Llama/GPT-OSS) orqali qo'shiq matnini yozadi. o' va g' harflarisiz."""
    if not GROQ_AVAILABLE or not groq_client:
        return "Matnni AI yoza olmadi (API kaliti sozlanmagan). Iltimos, admin bilan bog'laning."
    try:
        prompt = (
            f"O'zbek tilida qo'shiq matni yoz.\n"
            f"Mavzu: {topic}\n"
            f"Ovoz turi: {voice_type}\n"
            f"Janr: {genre}\n"
        )
        if extra_info:
            prompt += f"Qo'shimcha tafsilotlar (kim haqida, nima uchun): {extra_info}\n"
        prompt += (
            "\nTalablar:\n"
            "- Faqat qo'shiq matnini yoz (band, chorus tuzilishida)\n"
            "- Hech qanday izoh yoki qo'shimcha matn yozma, faqat qo'shiq so'zlari\n"
            "- 'o' va 'g' harflarining apostrofli shakllarini ISHLATMA — "
            "ya'ni \"bo'ldi\" o'rniga \"boldi\", \"to'g'ri\" o'rniga \"togri\" deb yoz"
        )
        lyrics = await call_groq_text([{"role": "user", "content": prompt}])
        if not lyrics:
            logging.error("AI qo'shiq matni: bo'sh javob qaytdi")
            return "Matnni AI yoza olmadi (bosh javob). Iltimos, qayta urinib koring yoki admin bilan bog'laning."
        return remove_apostrophe_letters(lyrics)
    except Exception as e:
        logging.error(f"AI qo'shiq matni yaratishda xatolik: {e}")
        return "Matnni AI yoza olmadi. Iltimos, o'zingiz matn yozib qoldiring yoki admin bilan bog'laning."
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💰 Pul berish"), KeyboardButton(text="✉️ Xabar yuborish")],
        [KeyboardButton(text="📈 Statistika"), KeyboardButton(text="🎵 Namuna qo'shish")],
        [KeyboardButton(text="🗑 Namuna o'chirish"), KeyboardButton(text="📋 Baza")],
        [KeyboardButton(text="🎁 Promokod qo'shish")],
        [KeyboardButton(text="⬅️ Bosh menyu")]
    ], resize_keyboard=True)

def get_subscribe_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Kanalga obuna bo'lish", url=CHANNEL_LINK)],
        [InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_sub")]
    ])

def get_broadcast_choice_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Hammaga yuborish", callback_data="broadcast_all")],
        [InlineKeyboardButton(text="👤 1 kishiga yuborish", callback_data="broadcast_one")]
    ])

def get_deposit_actions_keyboard(deposit_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"dep_ok_{deposit_id}"),
            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"dep_no_{deposit_id}")
        ]
    ])

def get_fraud_flag_keyboard(deposit_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Noqonuniy", callback_data=f"dep_fraud_{deposit_id}")]
    ])

# === YORDAMCHI FUNKSIYALAR ===
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except Exception as e:
        logging.warning(f"Kanal tekshirishda xatolik: {e}")
        return False

async def check_and_notify(message: Message, state: FSMContext) -> bool:
    if await is_subscribed(message.from_user.id):
        return True
    await state.clear()
    await message.answer(
        "⛔ Botdan foydalanish uchun avval kanalimizga obuna bo'ling!\n\n"
        "Obuna bo'lgach, <b>✅ Obuna bo'ldim</b> tugmasini bosing.",
        parse_mode="HTML", reply_markup=get_subscribe_keyboard()
    )
    return False

async def send_start_message(chat_id, user_id, fullname, username):
    is_new = db_register_user(user_id, fullname, username)
    text = (
        f"👋 Xush kelibsiz, <b>{fullname}</b>!\n\n"
        "🤖 <b>Men – Sun'iy Intellekt asosida ishlaydigan eng ilg'or musiqa botiman!</b>\n\n"
        "✨ <b>Mening imkoniyatlarim:</b>\n"
        "📝 Har qanday mavzuda mukammal va ma'noli <b>qo'shiq matnlari</b> yarata olaman.\n"
        "👤 Istalgan <b>ismlarga atab</b> maxsus va kreativ treklar tayyorlab beraman!\n"
        "🎵 Pop, Rep, Bass va boshqa janrlarda professional kuylar bastalayman.\n\n"
        f"📌 Narxlar:\n⚡ 30 soniyalik — {SONG_PRICE_SHORT:,} so'm\n"
        f"🎶 2-3 daqiqalik — {SONG_PRICE_FULL:,} so'm\n\n"
    )
    if is_new:
        text += "🎉 Xush kelibsiz! Qo'shiq buyurtma berish uchun avval balansingizni to'ldiring.\n\n👇 Quyidagi menyudan foydalanish:"
    else:
        text += "Quyidagi menyu orqali bot imkoniyatlaridan to'liq foydalanishingiz mumkin 👇"
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=get_main_menu(user_id))

# === HANDLERLAR ===
@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery, state: FSMContext):
    if await is_subscribed(callback.from_user.id):
        await state.clear()
        try:
            await callback.message.delete()
        except Exception:
            pass
        await send_start_message(callback.message.chat.id, callback.from_user.id, callback.from_user.full_name, callback.from_user.username)
    else:
        await callback.answer("❌ Siz hali kanalga obuna bo'lmagansiz!", show_alert=True)

@dp.message(F.text == "/start")
async def start_cmd(message: Message, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    await state.clear()
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            f"👋 Xush kelibsiz, <b>{message.from_user.full_name}</b>!\n\n"
            "⛔ Botdan foydalanish uchun avval kanalimizga obuna bo'lishingiz kerak!\n\n"
            "👇 Quyidagi tugmani bosib obuna bo'ling:",
            parse_mode="HTML", reply_markup=get_subscribe_keyboard()
        )
        return
    await send_start_message(message.chat.id, message.from_user.id, message.from_user.full_name, message.from_user.username)

# --- PROMOKOD (mahfiy: notogri kodga bot hech narsa demaydi) ---
@dp.message(StateFilter(None), F.text.regexp(r'^[A-Za-z0-9_\-]{3,30}$'))
async def promo_code_handler(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        return

    amount, reason = db_use_promo_code(message.text.strip(), message.from_user.id)
    if amount is None:
        return  # Notogri promokod yoki kunlik limit - hech narsa demaymiz

    if not await check_and_notify(message, state):
        return
    user_data = db_get_user(message.from_user.id)
    if not user_data:
        db_register_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    db_add_balance(message.from_user.id, amount)
    new_balance = db_get_user(message.from_user.id)[0]
    await message.answer(
        f"✅ <b>Promokod muvaffaqiyatli ishlatildi!</b>\n\n"
        f"💰 Balansingizga <b>{amount:,} so'm</b> qo'shildi.\n"
        f"💳 Joriy balans: <b>{new_balance:,} so'm</b>",
        parse_mode="HTML", reply_markup=get_main_menu(message.from_user.id)
    )

@dp.message(F.text == "📊 Balans")
async def balance_cmd(message: Message, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    if not await check_and_notify(message, state): return
    await state.clear()
    user_data = db_get_user(message.from_user.id)
    balance = user_data[0] if user_data else 0
    pending = user_data[5] if user_data else 0
    text = f"💰 Sizning balansingiz: <b>{balance:,} so'm</b>"
    if pending > 0:
        text += f"\n⏳ Kutilayotgan to'lov: {pending:,} so'm"
    text += f"\n\n📌 Narxlar:\n⚡ 1-2 daqiqalik — {SONG_PRICE_SHORT:,} so'm\n🎶 3 daqiqalik — {SONG_PRICE_FULL:,} so'm"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "💳 Pul kiritish")
async def deposit_cmd(message: Message, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    if not await check_and_notify(message, state): return
    await state.clear()
    await message.answer(
        "💳 <b>Balansni to'ldirish</b>\n\nQancha summa kiritmoqchisiz?\n"
        f"Minimal: {SONG_PRICE_SHORT:,} so'm\n\nSummani faqat raqamlarda kiriting (masalan: 5000):",
        parse_mode="HTML", reply_markup=get_main_menu(message.from_user.id)
    )
    await state.set_state(DepositState.waiting_for_amount)

def format_tariff_status_text(status: dict) -> str:
    if status["tariff"] == "none" or status["phase"] == "none":
        return "⭐ Sizda faol tarif yo'q."
    name = "Plus ⭐" if status["tariff"] == "plus" else "Pro 💎"
    if status["phase"] == "active":
        exp = status["expires"].strftime("%d.%m.%Y %H:%M") if status["expires"] else "-"
        return (
            f"{name} tarifi faol\n"
            f"🎵 Qolgan qo'shiqlar: {status['songs_left']} ta\n"
            f"⏳ Tarif muddati: {exp} gacha"
        )
    if status["phase"] == "bonus":
        bonus_until = status["bonus_until"].strftime("%d.%m.%Y") if status["bonus_until"] else "-"
        avail = "✅ Bugun mavjud" if status["bonus_available_today"] else "❌ Bugun ishlatilgan"
        return (
            f"{name} tarifi asosiy muddati tugagan, hozir <b>bonus davri</b>da.\n"
            f"🎁 Bonus qo'shiq: {avail}\n"
            f"⏳ Bonus muddati: {bonus_until} gacha"
        )
    return "⭐ Sizda faol tarif yo'q."

@dp.message(F.text == "⭐ Tariflar")
async def tariffs_cmd(message: Message, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    if not await check_and_notify(message, state): return
    await state.clear()
    status = get_tariff_status(message.from_user.id)
    text = (
        "⭐ <b>Tariflar</b>\n\n"
        f"⭐ <b>Plus</b> — {PLUS_PRICE:,} so'm / {PLUS_DURATION_DAYS} kun\n"
        f"   • {PLUS_SONGS} ta qo'shiq (1-2 daqiqalik)\n"
        f"   • Har bir qo'shiq orasida {PLUS_COOLDOWN_HOURS} soat kutish kerak\n"
        f"   • Tugagach, {PLUS_BONUS_DAYS} kun davomida kuniga {PLUS_BONUS_PER_DAY} ta 30 soniyalik bonus qo'shiq\n\n"
        f"💎 <b>Pro</b> — {PRO_PRICE:,} so'm / {PRO_DURATION_DAYS} kun\n"
        f"   • {PRO_SONGS} ta qo'shiq (1-2 daqiqalik)\n"
        f"   • Har bir qo'shiq orasida {PRO_COOLDOWN_HOURS} soat kutish kerak\n"
        f"   • Tugagach, {PRO_BONUS_DAYS} kun davomida kuniga {PRO_BONUS_PER_DAY} ta 1-2 daqiqalik bonus qo'shiq\n\n"
        f"ℹ️ Kutish vaqtida ham, balansingizda pul bo'lsa, oddiy narxda "
        f"({SONG_PRICE_SHORT:,} so'm) darhol yana qo'shiq buyurtma qilishingiz mumkin.\n\n"
        f"📍 <b>Joriy holat:</b>\n{format_tariff_status_text(status)}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_tariff_menu())

@dp.callback_query(F.data.in_(["buy_plus", "buy_pro"]))
async def buy_tariff_callback(callback: CallbackQuery, state: FSMContext):
    tariff = "plus" if callback.data == "buy_plus" else "pro"
    price = PLUS_PRICE if tariff == "plus" else PRO_PRICE
    name = "Plus ⭐" if tariff == "plus" else "Pro 💎"
    user_data = db_get_user(callback.from_user.id)
    balance = user_data[0] if user_data else 0
    if balance < price:
        await callback.answer(f"⚠️ Balansingiz yetarli emas. Kerakli: {price:,} so'm", show_alert=True)
        return
    status = get_tariff_status(callback.from_user.id)
    if status["phase"] in ("active", "bonus"):
        await callback.answer("⚠️ Sizda allaqachon faol tarif bor!", show_alert=True)
        return
    db_deduct_balance(callback.from_user.id, price)
    db_activate_tariff(callback.from_user.id, tariff)
    try:
        await callback.message.edit_text(
            f"✅ <b>{name} tarifi faollashtirildi!</b>\n\n"
            f"Endi qo'shiq yaratish uchun «🎵 Qo'shiq yaratish» tugmasini bosing.",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("✅ Tarif faollashtirildi!")

@dp.callback_query(F.data == "extra_song_wait")
async def extra_song_wait_callback(callback: CallbackQuery):
    try:
        await callback.message.edit_text("⏳ Yaxshi, kutasiz. Vaqti kelganda «🎵 Qo'shiq yaratish» tugmasini qayta bosing.")
    except Exception:
        pass
    await callback.answer()

@dp.callback_query(F.data == "extra_song_buy")
async def extra_song_buy_callback(callback: CallbackQuery, state: FSMContext):
    price = SONG_PRICE_SHORT
    user_data = db_get_user(callback.from_user.id)
    balance = user_data[0] if user_data else 0
    if balance < price:
        await callback.answer(f"⚠️ Balansingiz yetarli emas. Kerakli: {price:,} so'm", show_alert=True)
        return
    await state.update_data(use_tariff=None, use_bonus=False, song_type="1-2 daqiqalik", song_price=price)
    try:
        await callback.message.edit_text(
            f"✅ Oddiy narxda ({price:,} so'm) qo'shiq buyurtma qilinmoqda."
        )
    except Exception:
        pass
    await callback.message.answer(
        "🎵 Qo'shiq nima haqida bo'ladi?\nQuyidagilardan birini tanlang yoki o'z g'oyangizni yozing:",
        reply_markup=get_song_topic_menu()
    )
    await state.set_state(CreateSong.waiting_for_topic)
    await callback.answer()

@dp.message(F.text == "📋 Baza")
async def baza_cmd(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
    cursor.execute("""
        SELECT user_id, fullname, username, balance, total_paid, tariff, is_blocked
        FROM users ORDER BY user_id
    """)
    rows = cursor.fetchall()
    if not rows:
        await message.answer("📋 Baza bo'sh.")
        return
    try:
        from openpyxl import Workbook
        import tempfile, os
        wb = Workbook()
        ws = wb.active
        ws.title = "Foydalanuvchilar"
        ws.append(["User ID", "Ism", "Username", "Balans", "Jami to'lagan", "Tarif", "Bloklagan"])
        for r in rows:
            user_id, fullname, username, balance, total_paid, tariff, is_blocked = r
            ws.append([
                user_id, fullname or "", f"@{username}" if username else "",
                balance or 0, total_paid or 0, tariff or "none",
                "Ha" if is_blocked else "Yo'q"
            ])
        file_path = os.path.join(tempfile.gettempdir(), f"baza_{message.from_user.id}.xlsx")
        wb.save(file_path)
        from aiogram.types import FSInputFile
        await message.answer_document(
            FSInputFile(file_path, filename="baza.xlsx"),
            caption=f"📋 Jami: {len(rows)} ta foydalanuvchi"
        )
        try:
            os.remove(file_path)
        except Exception:
            pass
    except ImportError:
        lines = ["📋 <b>Foydalanuvchilar bazasi</b>\n"]
        for r in rows:
            user_id, fullname, username, balance, total_paid, tariff, is_blocked = r
            uname = f"@{username}" if username else "—"
            blocked = "🚫" if is_blocked else "✅"
            lines.append(f"{blocked} <code>{user_id}</code> | {fullname} | {uname} | {balance or 0:,} so'm | {tariff or 'none'}")
        text = "\n".join(lines)
        for i in range(0, len(text), 3800):
            await message.answer(text[i:i+3800], parse_mode="HTML")
    except Exception as e:
        logging.error(f"Baza export xatolik: {e}")
        await message.answer(f"❌ Bazani yuklashda xatolik chiqdi: {e}")

MENU_BUTTONS = ["🎵 Qo'shiq yaratish", "🎼 Qo'shiq namunaviy", "📊 Balans",
                "💳 Pul kiritish", "⭐ Tariflar", "🤖 AI Yordamchi", "👨‍💼 Admin", "🔐 Admin Panel",
                "📋 Baza", "🗑 Namuna o'chirish", "🎁 Promokod qo'shish", "⬅️ Bosh menyu"]

async def handle_menu_button(message: Message, state: FSMContext):
    t = message.text
    if t == "🎵 Qo'shiq yaratish": await create_song_start(message, state)
    elif t == "🎼 Qo'shiq namunaviy": await song_samples_cmd(message, state)
    elif t == "📊 Balans": await balance_cmd(message, state)
    elif t == "💳 Pul kiritish": await deposit_cmd(message, state)
    elif t == "⭐ Tariflar": await tariffs_cmd(message, state)
    elif t == "🤖 AI Yordamchi": await ai_assistant_start(message, state)
    elif t == "👨‍💼 Admin": await admin_contact_cmd(message, state)
    elif t == "🔐 Admin Panel": await admin_panel_cmd(message)
    elif t == "📋 Baza": await baza_cmd(message)
    elif t == "🗑 Namuna o'chirish": await sample_delete_start(message, state)
    elif t == "🎁 Promokod qo'shish": await promo_add_start(message, state)
    else: await back_cmd(message, state)

# === AI YORDAMCHI (faqat bot haqidagi savollarga javob beradi) ===
class AiHelpState(StatesGroup):
    waiting_for_question = State()

BOT_INFO_CONTEXT_USER = f"""
Sen "Qo'shiq yaratish" Telegram botining AI yordamchisisan. Faqat shu bot haqidagi
savollarga javob berasan: narxlar, tariflar, qo'shiq yaratish jarayoni, balans,
pul kiritish, va bot nima uchun ishlamayotgani haqida.

BOT HAQIDA MA'LUMOT:
- Oddiy narxlar: 1-2 daqiqalik qo'shiq — {SONG_PRICE_SHORT:,} so'm, 3 daqiqalik — {SONG_PRICE_FULL:,} so'm
- Plus tarifi: {PLUS_PRICE:,} so'm / {PLUS_DURATION_DAYS} kun — {PLUS_SONGS} ta qo'shiq (1-2 daqiqalik),
  har biri orasida {PLUS_COOLDOWN_HOURS} soat kutish kerak, tugagach {PLUS_BONUS_DAYS} kun davomida
  kuniga {PLUS_BONUS_PER_DAY} ta 30 soniyalik bonus qo'shiq
- Pro tarifi: {PRO_PRICE:,} so'm / {PRO_DURATION_DAYS} kun — {PRO_SONGS} ta qo'shiq (1-2 daqiqalik),
  har biri orasida {PRO_COOLDOWN_HOURS} soat kutish kerak, tugagach {PRO_BONUS_DAYS} kun davomida
  kuniga {PRO_BONUS_PER_DAY} ta bonus qo'shiq
- Tarif kutish vaqtida ham, balansda pul bo'lsa, oddiy narxda ({SONG_PRICE_SHORT:,} so'm) darhol
  qo'shimcha qo'shiq buyurtma qilish mumkin
- Pul kiritish: foydalanuvchi summani yozadi, chek rasmini yuboradi, tizim AI orqali avtomatik tekshiradi
- Qo'shiq yaratish: mavzu tanlanadi, ovoz turi (erkak/ayol/duet/instrumental), matn bor/yo'q
  (yo'q bo'lsa, kim haqida va nima sabab ekani so'raladi, keyin AI o'zi yozadi), keyin musiqa uslubi tanlanadi
- Buyurtma qabul qilingach 24 soat ichida tayyor qo'shiq yuboriladi
- Admin bilan bog'lanish uchun: {ADMIN_USERNAME}

QOIDALAR:
- Faqat shu bot haqidagi savollarga javob ber. Agar savol botga aloqasi bo'lmasa
  (masalan ob-havo, matematika, umumiy bilim), muloyimlik bilan "Men faqat ushbu bot
  haqidagi savollarga yordam bera olaman" deb ayt.
- Qisqa va aniq javob ber, o'zbek tilida yoz.
- 'o' va 'g' harflarining apostrofli shakllarini ishlatma (bo'ldi emas boldi, to'g'ri emas togri).
- MUHIM FORMAT QOIDASI: hech qanday Markdown belgisi ishlatma — ** (qalin), * (yulduzcha),
  _ (kursiv), # (sarlavha), ` (kod) kabi belgilarni umuman yozma. Ular Telegram'da xunuk
  korinadi. Ro'yxat kerak bolsa oddiy chiziqcha "-" yoki emoji bilan yoz, hech qanday
  yulduzcha ishlatma. Faqat sof matn yoz.
- MUHIM: promokod tizimi haqida hech qanday malumot berma, tasdiqlama yoki inkor etma —
  agar foydalanuvchi promokod haqida sorasa, "Bunday funksiya haqida malumotim yoq" kabi
  neytral javob ber. Bu mavzuni hech qachon ochma.
- Agar foydalanuvchi botning biror qismi ISHLAMAYOTGANI haqida shikoyat qilsa (masalan
  "bot ishlamayapti", "pul yechildi lekin qoshiq kelmadi", "xato chiqyapti" va h.k.),
  avval oddiy tushuntirish/yechim taklif qil, so'ng javobingning OXIRIGA aniq shu satrni qo'sh:
  [ESCALATE: qisqa sabab] — bu ichki belgi, foydalanuvchi buni ko'rmaydi, tizim buni ADMINGA
  xabar yuborish kerakligini bilish uchun ishlatadi. Agar muammo yengil yoki tushunarli
  bo'lsa (masalan shunchaki savol), [ESCALATE] qo'shma.
"""

BOT_INFO_CONTEXT_ADMIN = f"""
Sen "Qo'shiq yaratish" Telegram botining AI yordamchisisan. Suhbatlashayotgan odam —
botning ADMINI, shuning uchun botning barcha ichki funksiyalari, admin panel imkoniyatlari
va tizim qanday ishlashi haqida to'liq va batafsil javob berishing mumkin.

BOT HAQIDA TO'LIQ MA'LUMOT:

FOYDALANUVCHI TOMONI:
- Oddiy narxlar: 1-2 daqiqalik qo'shiq — {SONG_PRICE_SHORT:,} so'm, 3 daqiqalik — {SONG_PRICE_FULL:,} so'm
- Plus tarifi: {PLUS_PRICE:,} so'm / {PLUS_DURATION_DAYS} kun — {PLUS_SONGS} ta qo'shiq (1-2 daqiqalik),
  har biri orasida {PLUS_COOLDOWN_HOURS} soat kutish, tugagach {PLUS_BONUS_DAYS} kun kuniga
  {PLUS_BONUS_PER_DAY} ta 30 soniyalik bonus qo'shiq
- Pro tarifi: {PRO_PRICE:,} so'm / {PRO_DURATION_DAYS} kun — {PRO_SONGS} ta qo'shiq (1-2 daqiqalik),
  har biri orasida {PRO_COOLDOWN_HOURS} soat kutish, tugagach {PRO_BONUS_DAYS} kun kuniga
  {PRO_BONUS_PER_DAY} ta bonus qo'shiq
- Tarif kutish vaqtida ham oddiy narxda ({SONG_PRICE_SHORT:,} so'm) darhol qoshimcha qoshiq olish mumkin
- Qo'shiq yaratish jarayoni: mavzu → ovoz turi (erkak/ayol/duet/instrumental) → matn bor/yoq
  (yoq bolsa, kim haqida va nima sabab ekani sorlanadi, keyin Google Gemini ozbekcha matn
  yozadi, o' va g' harflarisiz) → musiqa uslubi → buyurtma adminga ketadi

PUL KIRITISH VA CHEKNI TEKSHIRISH:
- Foydalanuvchi summa yozadi, chek rasm/PDF yuboradi
- Google Gemini AI rasmni tahlil qiladi: haqiqiy chekmi, summasi, tranzaksiya ID
- Bir xil chek ikki xil korinishda (masalan screenshot va PDF) yuborilsa ham, tranzaksiya ID
  orqali aniqlanadi va rad etiladi
- Togri va yangi chek bolsa balans AVTOMATIK toldiriladi, adminga chek rasmi + "Noqonuniy"
  tugmasi bilan xabar boradi
- Admin "Noqonuniy" bossa, foydalanuvchi balansidan chek summasi qadar pul yechiladi va
  ogohlantirish xabari ketadi
- Takroriy chek yuborilsa foydalanuvchiga rad javobi va ADMINGA ham xabar boradi

ADMIN PANEL IMKONIYATLARI (faqat admin foydalanadi):
- Pul berish: istalgan foydalanuvchiga qolda balans qoshish
- Xabar yuborish: barcha foydalanuvchilarga broadcast, bloklaganlar avtomatik aniqlanadi
- Statistika: azolar soni, jami tushum, baza fayli holati va hajmi
- Namuna qoshish / Namuna ochirish: qoshiq namunalarini boshqarish
- Baza: barcha foydalanuvchilarni .xlsx fayl qilib yuborish (ID, balans, tarif, bloklaganmi)
- Promokod qoshish: admin kod matni va summa kiritadi, foydalanuvchi shu kodni oddiy xabar
  sifatida yuborsa balansi avtomatik toladi; notogri kodga bot hech narsa demaydi (sukut),
  togri kod bir marta ishlatiladi va keyin butunlay ochib ketadi

TEXNIK:
- Malumotlar SQLite (music_bot.db) faylida saqlanadi, DB_PATH orqali sozlanadi
- Kerakli API kalit: GROQ_API_KEY (Groq — chekni tekshirish, qo'shiq matni yozish, AI Yordamchi)
- Railway kabi platformalarda baza fayli yoqolmasligi uchun Volume (persistent disk) ulanishi kerak

QOIDALAR:
- Admin uchun batafsil, texnik javob berishing mumkin
- 'o' va 'g' harflarining apostrofli shakllarini ishlatma (bo'ldi emas boldi, to'g'ri emas togri)
- MUHIM FORMAT QOIDASI: hech qanday Markdown belgisi ishlatma — ** (qalin), * (yulduzcha),
  _ (kursiv), # (sarlavha), ` (kod) kabi belgilarni umuman yozma. Ular Telegram'da xunuk
  korinadi. Ro'yxat kerak bolsa oddiy chiziqcha "-" yoki emoji bilan yoz. Faqat sof matn yoz.
- Bu kontekst faqat adminga korinadi, boshqa hech kimga bu malumotlarni tarqatma
"""

def strip_escalate_tag(text: str):
    """AI javobidan [ESCALATE: ...] belgisini ajratib oladi."""
    match = re.search(r'\[ESCALATE:?\s*(.*?)\]', text, re.IGNORECASE | re.DOTALL)
    if match:
        reason = match.group(1).strip()
        clean_text = re.sub(r'\[ESCALATE:?\s*.*?\]', '', text, flags=re.IGNORECASE | re.DOTALL).strip()
        return clean_text, reason
    return text, None

async def ask_ai_assistant(question: str, is_admin: bool = False) -> tuple:
    """Groq AI orqali bot haqidagi savolga javob beradi. (javob_matni, escalate_sababi)"""
    if not GROQ_AVAILABLE or not groq_client:
        return "AI yordamchi hozircha ishlamayapti. Admin bilan bog'laning: " + ADMIN_USERNAME, None
    try:
        system_context = BOT_INFO_CONTEXT_ADMIN if is_admin else BOT_INFO_CONTEXT_USER
        raw_text = await call_groq_text([
            {"role": "system", "content": system_context},
            {"role": "user", "content": question},
        ])
        if not raw_text:
            logging.error("AI Yordamchi: bosh javob qaytdi")
            return "Kechirasiz, javob bera olmadim (bosh javob). Qayta urinib koring yoki admin bilan bog'laning: " + ADMIN_USERNAME, None
        clean_text, escalate_reason = strip_escalate_tag(raw_text)
        clean_text = remove_apostrophe_letters(strip_markdown_symbols(clean_text)).strip()
        if not clean_text:
            clean_text = "Kechirasiz, javob bera olmadim. Admin bilan bog'laning: " + ADMIN_USERNAME
        return clean_text, escalate_reason
    except Exception as e:
        logging.error(f"AI Yordamchi xatolik: {e}")
        try:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ <b>Groq AI xatolik (AI Yordamchi)</b>\n\n<code>{str(e)[:800]}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return "Kechirasiz, hozir javob bera olmadim. Admin bilan bog'laning: " + ADMIN_USERNAME, None

@dp.message(F.text == "🤖 AI Yordamchi")
async def ai_assistant_start(message: Message, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    if not await check_and_notify(message, state): return
    await state.clear()
    await message.answer(
        "🤖 <b>AI Yordamchi</b>\n\n"
        "Men botning narxlari, tariflari, qo'shiq yaratish jarayoni yoki texnik muammolar "
        "haqidagi savollaringizga yordam beraman.\n\n"
        "Savolingizni yozing:",
        parse_mode="HTML"
    )
    await state.set_state(AiHelpState.waiting_for_question)

@dp.message(AiHelpState.waiting_for_question)
async def ai_assistant_answer(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        await state.clear()
        await handle_menu_button(message, state)
        return
    if not message.text:
        await message.answer("Iltimos, savolingizni matn ko'rinishida yozing:")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    is_admin = message.from_user.id == ADMIN_ID
    answer, escalate_reason = await ask_ai_assistant(message.text, is_admin=is_admin)

    await message.answer(answer, reply_markup=get_main_menu(message.from_user.id))

    if escalate_reason:
        try:
            user_info = f"@{message.from_user.username}" if message.from_user.username else "username yo'q"
            await bot.send_message(
                ADMIN_ID,
                f"🚨 <b>AI YORDAMCHI: JIDDIY MUAMMO</b>\n\n"
                f"👤 {message.from_user.full_name}\n"
                f"🔗 {user_info}\n"
                f"🆔 ID: <code>{message.from_user.id}</code>\n\n"
                f"❓ Savol: {message.text}\n\n"
                f"⚠️ Sabab: {escalate_reason}",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"AI escalate xabar yuborishda xato: {e}")

    # Suhbatni davom ettirish uchun state'da qoldiramiz, foydalanuvchi menyu tugmasini bossa chiqadi
    await state.set_state(AiHelpState.waiting_for_question)



@dp.message(DepositState.waiting_for_amount)
async def process_deposit_amount(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        await state.clear()
        await handle_menu_button(message, state)
        return
    if not message.text or not message.text.strip().isdigit():
        await message.answer("❌ Iltimos, summani faqat raqamlarda kiriting (masalan: 5000):")
        return
    amount = int(message.text.strip())
    if amount < SONG_PRICE_SHORT:
        await message.answer(f"❌ Minimal summa {SONG_PRICE_SHORT:,} so'm. Qayta kiriting:")
        return
    await state.update_data(deposit_amount=amount)
    await message.answer(
        "💳 <b>To'lov qilish uchun:</b>\n\n"
        "Karta raqami: <code>6262570040359129</code>\n\n"
        f"💰 Summa: <b>{amount:,} so'm</b>\n\n"
        f"🆔 Telegram ID: <code>{message.from_user.id}</code>\n\n"
        "✅ To'lovni amalga oshirgach, <b>chek rasmini (screenshot)</b> yuboring.\n"
        "⚠️ Chek avtomatik tekshiriladi!",
        parse_mode="HTML", reply_markup=get_main_menu(message.from_user.id)
    )
    await state.set_state(DepositState.waiting_for_receipt)

@dp.message(DepositState.waiting_for_receipt)
async def process_receipt(message: Message, state: FSMContext):
    if message.text and message.text in MENU_BUTTONS:
        await state.clear()
        await handle_menu_button(message, state)
        return

    if not message.photo:
        await message.answer("❌ Iltimos, chekni <b>rasm (screenshot)</b> ko'rinishida yuboring!", parse_mode="HTML")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    data = await state.get_data()
    expected_amount = data.get('deposit_amount', SONG_PRICE_SHORT)
    deposit_count = db_get_user_deposit_count(message.from_user.id)
    file_id = message.photo[-1].file_id
    db_set_pending_deposit(message.from_user.id, expected_amount)

    await message.answer("⏳ Chek tekshirilmoqda...")

    success, amount_or_msg, photo_hash, transaction_id = await check_receipt_photo(file_id, expected_amount)

    # --- Takroriy chek (avval yuborilgan, xoh bir xil rasm, xoh bir xil tranzaksiya) ---
    is_duplicate = (not success) and isinstance(amount_or_msg, str) and "allaqachon" in amount_or_msg
    if is_duplicate:
        db_clear_pending_deposit(message.from_user.id)
        await message.answer(
            f"{amount_or_msg}\n\n"
            "Bu chek avval balansga qo'shilgan yoki tekshirilgan. Qayta ishlatib bo'lmaydi.\n"
            f"Savol bo'lsa admin bilan bog'laning: {ADMIN_USERNAME}",
            reply_markup=get_main_menu(message.from_user.id)
        )
        try:
            user_info = f"@{message.from_user.username}" if message.from_user.username else "username yo'q"
            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=file_id,
                caption=(
                    f"🔁 <b>TAKRORIY CHEK YUBORILDI</b>\n\n"
                    f"👤 {message.from_user.full_name}\n"
                    f"🔗 {user_info}\n"
                    f"🆔 ID: <code>{message.from_user.id}</code>\n"
                    f"💰 Kutilgan summa: {expected_amount:,} so'm\n"
                    f"⚠️ Sabab: {amount_or_msg}\n\n"
                    f"ℹ️ Foydalanuvchiga avtomatik rad javobi berildi, balans o'zgarmadi."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Admin ga takroriy chek xabarini yuborishda xatolik: {e}")
        await state.clear()
        return

    if success:
        actual_amount = amount_or_msg  
        deposit_id = db_add_deposit(message.from_user.id, actual_amount, file_id, photo_hash, transaction_id)
        if deposit_id:
            db_update_deposit_status(deposit_id, 'accepted')
        db_add_balance(message.from_user.id, actual_amount)
        db_clear_pending_deposit(message.from_user.id)
        new_balance = db_get_user(message.from_user.id)[0]
        extra = ""
        if actual_amount > expected_amount:
            extra = f"\n💎 Ortiqcha to'lov ({actual_amount - expected_amount:,} so'm) ham balansingizga qo'shildi!"
        await message.answer(
            f"✅ Chek tasdiqlandi! Summa: <b>{actual_amount:,} so'm</b>{extra}\n\n"
            f"💳 Joriy balans: <b>{new_balance:,} so'm</b>\n\n"
            "🎵 Endi qo'shiq buyurtma berishingiz mumkin!",
            parse_mode="HTML", reply_markup=get_main_menu(message.from_user.id)
        )
        try:
            user_info = f"@{message.from_user.username}" if message.from_user.username else "username yo'q"
            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=file_id,
                caption=(
                    f"💳 <b>AVTOMATIK TO'LOV TASDIQLANDI</b>\n\n"
                    f"👤 {message.from_user.full_name}\n"
                    f"🔗 {user_info}\n"
                    f"🆔 ID: <code>{message.from_user.id}</code>\n"
                    f"💰 Summa: {actual_amount:,} so'm\n"
                    f"🤖 OCR orqali avtomatik tasdiqlandi\n\n"
                    f"⚠️ Agar bu chek soxta/noqonuniy bo'lsa, quyidagi tugmani bosing:"
                ),
                parse_mode="HTML",
                reply_markup=get_fraud_flag_keyboard(deposit_id)
            )
        except Exception as e:
            logging.error(f"Admin xabar yuborishda xato: {e}")

    else:
        # Har qanday muammo yoki API xatoligi bo'lganda ham avtomatik shu yerga o'tadi
        error_msg = amount_or_msg  
        deposit_id = db_add_deposit(message.from_user.id, expected_amount, file_id, photo_hash, transaction_id)
        user_info = f"@{message.from_user.username}" if message.from_user.username else "username yo'q"
        
        caption = (
            f"💳 <b>YANGI CHEK KELDI (AVTOMATIK TEKSHIRILMADI)</b>\n\n"
            f"👤 Foydalanuvchi: {message.from_user.full_name}\n"
            f"🔗 Lichkasi: {user_info}\n"
            f"🆔 ID: <code>{message.from_user.id}</code>\n"
            f"💰 Kutilgan summa: {expected_amount:,} so'm\n"
            f"📊 Jami depositlar: {deposit_count} ta\n"
            f"⚠️ {error_msg}\n\n"
            f"✅ <b>Qo'lda tekshirish kerak!</b>"
        )
        try:
            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=get_deposit_actions_keyboard(deposit_id)
            )
        except Exception as e:
            logging.error(f"Admin ga yuborishda xatolik: {e}")
            
        await message.answer(
            f"⚠️ Chek avtomatik tasdiqlanmadi.\n\n"
            f"📝 Sabab: {error_msg}\n\n"
            f"👨‍💼 Chekingiz admin tomonidan tekshiriladi.\n"
            f"⏳ Bu jarayon 24 soatgacha vaqt olishi mumkin.\n"
            f"🔑 Chek ID: {deposit_id}\n\n"
            f"Agar tezroq tasdiqlash kerak bo'lsa, admin bilan bog'laning: {ADMIN_USERNAME}",
            reply_markup=get_main_menu(message.from_user.id)
        )

    await state.clear()

# === ADMIN DEPOSIT TASDIQLASH ===
@dp.callback_query(F.data.startswith("dep_ok_"))
async def deposit_accept(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Siz admin emassiz!", show_alert=True)
        return
    deposit_id = int(callback.data.split("_")[2])
    cursor.execute("SELECT user_id, amount, photo_hash, status FROM deposits WHERE id = ?", (deposit_id,))
    deposit = cursor.fetchone()
    if not deposit:
        await callback.message.edit_caption("❌ Bu chek topilmadi!")
        return
    user_id, amount, photo_hash, status = deposit
    if status != 'pending':
        await callback.message.edit_caption(f"⚠️ Bu chek allaqachon {status} holatida!")
        return
    db_add_balance(user_id, amount)
    db_update_deposit_status(deposit_id, 'accepted')
    db_clear_pending_deposit(user_id)
    new_balance = db_get_user(user_id)[0]
    deposit_count = db_get_user_deposit_count(user_id)
    await callback.message.edit_caption(
        f"✅ TO'LOV TASDIQLANDI\n\n"
        f"👤 User ID: {user_id}\n"
        f"💰 Summa: {amount:,} so'm\n"
        f"💳 Yangi balans: {new_balance:,} so'm\n"
        f"📊 Jami depositlar: {deposit_count} ta"
    )
    await safe_send_message(
        user_id,
        f"✅ Sizning <b>{amount:,} so'm</b> lik to'lovingiz tasdiqlandi!\n"
        f"💳 Joriy balans: <b>{new_balance:,} so'm</b>\n\n"
        "🎵 Endi qo'shiq buyurtma berishingiz mumkin!",
        parse_mode="HTML"
    )
    await callback.answer("✅ Tasdiqlandi!")

@dp.callback_query(F.data.startswith("dep_no_"))
async def deposit_reject(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Siz admin emassiz!", show_alert=True)
        return
    deposit_id = int(callback.data.split("_")[2])
    cursor.execute("SELECT user_id, amount, status FROM deposits WHERE id = ?", (deposit_id,))
    deposit = cursor.fetchone()
    if not deposit:
        await callback.message.edit_caption("❌ Bu chek topilmadi!")
        return
    user_id, amount, status = deposit
    if status != 'pending':
        await callback.message.edit_caption(f"⚠️ Bu chek allaqachon {status} holatida!")
        return
    db_update_deposit_status(deposit_id, 'rejected')
    db_clear_pending_deposit(user_id)
    await callback.message.edit_caption(
        f"❌ TO'LOV RAD ETILDI\n\n👤 User ID: {user_id}\n💰 Summa: {amount:,} so'm"
    )
    await safe_send_message(
        user_id,
        f"❌ Sizning {amount:,} so'm lik to'lovingiz rad etildi.\n"
        f"Sabab uchun admin bilan bog'laning: {ADMIN_USERNAME}"
    )
    await callback.answer("❌ Rad etildi!")

@dp.callback_query(F.data.startswith("dep_fraud_"))
async def deposit_fraud(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Siz admin emassiz!", show_alert=True)
        return
    deposit_id = int(callback.data.split("_")[2])
    cursor.execute("SELECT user_id, amount, status FROM deposits WHERE id = ?", (deposit_id,))
    deposit = cursor.fetchone()
    if not deposit:
        await callback.answer("❌ Bu chek topilmadi!", show_alert=True)
        return
    user_id, amount, status = deposit

    # Balansdan chek summasi qadar yechamiz (agar balans yetarli bo'lmasa, 0 gacha)
    user_data = db_get_user(user_id)
    current_balance = user_data[0] if user_data else 0
    deduct_amount = min(amount, current_balance)
    db_deduct_balance(user_id, deduct_amount)
    db_update_deposit_status(deposit_id, 'fraud')
    new_balance = db_get_user(user_id)[0]

    try:
        await callback.message.edit_caption(
            caption=(
                f"🚫 <b>SOXTA CHEK DEB BELGILANDI</b>\n\n"
                f"👤 User ID: {user_id}\n"
                f"💰 Chek summasi: {amount:,} so'm\n"
                f"➖ Balansdan yechildi: {deduct_amount:,} so'm\n"
                f"💳 Yangi balans: {new_balance:,} so'm"
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await safe_send_message(
        user_id,
        "⚠️ <b>Diqqat!</b>\n\n"
        "Siz qonunni buzdingiz — soxtalashgan chek bilan aldamoqchi bo'ldingiz.\n"
        f"Shu sabab balansingizdan <b>{deduct_amount:,} so'm</b> yechildi.\n\n"
        "Takroriy holatlarda hisobingiz butunlay bloklanishi mumkin.",
        parse_mode="HTML"
    )
    await callback.answer("🚫 Soxta chek deb belgilandi, balans yechildi!")

@dp.message(F.text == "👨‍💼 Admin")
async def admin_contact_cmd(message: Message, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    if not await check_and_notify(message, state): return
    await state.clear()
    await message.answer(
        f"👨‍💻 Admin bilan bog'lanish: <a href='https://t.me/Javoh_123'>{ADMIN_USERNAME}</a>\n\n"
        "Savollaringiz bo'lsa, bemalol yozishingiz mumkin.",
        parse_mode="HTML"
    )

@dp.message(F.text == "⬅️ Bosh menyu")
async def back_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bosh menyudasiz.", reply_markup=get_main_menu(message.from_user.id))

# === QO'SHIQ NAMUNALARI ===
@dp.message(F.text == "🎼 Qo'shiq namunaviy")
async def song_samples_cmd(message: Message, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    if not await check_and_notify(message, state): return
    await state.clear()
    samples = db_get_samples()
    if not samples:
        await message.answer(
            "🎼 <b>Qo'shiq namunaviy</b>\n\nHozircha namuna qo'shiqlar yo'q.\nAdmin tez orada qo'shadi! 🎵",
            parse_mode="HTML", reply_markup=get_main_menu(message.from_user.id)
        )
        return
    await message.answer(f"🎼 <b>Qo'shiq namunaviy ({len(samples)} ta)</b>", parse_mode="HTML")
    for sample in samples:
        _, title, description, file_id = sample
        if file_id:
            try:
                await bot.send_audio(chat_id=message.chat.id, audio=file_id,
                                     caption=f"<b>{title}</b>\n{description}", parse_mode="HTML")
            except Exception:
                await message.answer(f"🎵 <b>{title}</b>\n{description}", parse_mode="HTML")
        else:
            await message.answer(f"🎵 <b>{title}</b>\n{description}\n\n<i>(Audio hali qo'shilmagan)</i>", parse_mode="HTML")
    await message.answer(
        f"🎵 O'zingizga qo'shiq buyurtma berish uchun <b>«🎵 Qo'shiq yaratish»</b> tugmasini bosing!\n\n"
        f"📌 Narxlar:\n⚡ 1-2 daqiqalik — {SONG_PRICE_SHORT:,} so'm\n🎶 3 daqiqalik — {SONG_PRICE_FULL:,} so'm",
        parse_mode="HTML", reply_markup=get_main_menu(message.from_user.id)
    )

# === QO'SHIQ YARATISH ===
def format_cooldown(td: timedelta) -> str:
    total_sec = int(td.total_seconds())
    h, rem = divmod(total_sec, 3600)
    m = rem // 60
    if h > 0:
        return f"{h} soat {m} daqiqa"
    return f"{m} daqiqa"

@dp.message(F.text == "🎵 Qo'shiq yaratish")
async def create_song_start(message: Message, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    if not await check_and_notify(message, state): return
    await state.clear()

    status = get_tariff_status(message.from_user.id)

    # --- Tarif faol (Plus yoki Pro) ---
    if status["phase"] == "active":
        if not status["cooldown_ok"]:
            name = "Plus ⭐" if status["tariff"] == "plus" else "Pro 💎"
            price = SONG_PRICE_SHORT
            user_data = db_get_user(message.from_user.id)
            balance = user_data[0] if user_data else 0
            text = (
                f"⏳ <b>Kutish vaqti</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"{name} tarifda navbatdagi bepul qo'shiqni buyurtma qilish uchun "
                f"yana <b>{format_cooldown(status['cooldown_remaining'])}</b> kutishingiz kerak.\n\n"
            )
            if balance >= price:
                text += (
                    f"💰 Balansingizda <b>{balance:,} so'm</b> bor — kutmasdan, "
                    f"oddiy narxda ({price:,} so'm) hoziroq yana bitta qo'shiq buyurtma qilishingiz mumkin."
                )
                await message.answer(text, parse_mode="HTML", reply_markup=get_extra_song_menu())
            else:
                text += "💳 Balansingizni to'ldirib, oddiy narxda darhol qo'shiq buyurtma qilishingiz mumkin."
                await message.answer(text, parse_mode="HTML")
            return
        name = "Plus ⭐" if status["tariff"] == "plus" else "Pro 💎"
        await state.update_data(use_tariff=status["tariff"], use_bonus=False)
        await message.answer(
            f"🎵 <b>{name} tarifi orqali qo'shiq yaratish</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🎼 Qolgan qo'shiqlar: <b>{status['songs_left']} ta</b>",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
        )
        await message.answer(
            "📌 <b>Qo'shiq nima haqida bo'ladi?</b>\nQuyidagilardan birini tanlang yoki o'z g'oyangizni yozing:",
            parse_mode="HTML", reply_markup=get_song_topic_menu()
        )
        await state.set_state(CreateSong.waiting_for_topic)
        return

    # --- Bonus davri (tarif tugagan, bonus faol) ---
    if status["phase"] == "bonus":
        if not status["bonus_available_today"]:
            bonus_until = status["bonus_until"].strftime("%d.%m.%Y") if status["bonus_until"] else "-"
            await message.answer(
                f"🎁 <b>Bugungi bonus ishlatilgan</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"Ertaga qayta urinib ko'ring.\n"
                f"⏳ Bonus muddati: {bonus_until} gacha",
                parse_mode="HTML"
            )
            return
        name = "Plus ⭐" if status["tariff"] == "plus" else "Pro 💎"
        song_kind = "30 soniyalik" if status["tariff"] == "plus" else "1-2 daqiqalik"
        await state.update_data(use_tariff=status["tariff"], use_bonus=True)
        await message.answer(
            f"🎁 <b>{name} bonus qo'shig'i</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🎶 Turi: {song_kind}\n"
            f"💰 Narxi: Bepul",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
        )
        await message.answer(
            "📌 <b>Qo'shiq nima haqida bo'ladi?</b>\nQuyidagilardan birini tanlang yoki o'z g'oyangizni yozing:",
            parse_mode="HTML", reply_markup=get_song_topic_menu()
        )
        await state.set_state(CreateSong.waiting_for_topic)
        return

    # --- Tarifsiz / oddiy foydalanuvchi ---
    user_data = db_get_user(message.from_user.id)
    balance = user_data[0] if user_data else 0
    if balance < SONG_PRICE_SHORT:
        await message.answer(
            f"⚠️ <b>Balansingiz yetarli emas</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Balansingiz: <b>{balance:,} so'm</b>\n\n"
            f"📌 <b>Narxlar:</b>\n"
            f"⚡ 1-2 daqiqalik — {SONG_PRICE_SHORT:,} so'm\n"
            f"🎶 3 daqiqalik — {SONG_PRICE_FULL:,} so'm\n\n"
            "💳 Avval <b>Pul kiritish</b> orqali balansingizni to'ldiring, "
            "yoki ⭐ <b>Tariflar</b> bo'limidan tarif tanlang.",
            parse_mode="HTML"
        )
        return
    await state.update_data(use_tariff=None, use_bonus=False)
    await message.answer(
        f"🎵 <b>Qo'shiq turini tanlang</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"⚡ 1-2 daqiqalik — {SONG_PRICE_SHORT:,} so'm\n"
        f"🎶 3 daqiqalik — {SONG_PRICE_FULL:,} so'm\n\n"
        f"💰 Balansingiz: <b>{balance:,} so'm</b>",
        parse_mode="HTML", reply_markup=get_song_type_menu()
    )
    await state.set_state(CreateSong.waiting_for_type)

@dp.message(CreateSong.waiting_for_type)
async def process_song_type(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=get_main_menu(message.from_user.id))
        return
    if message.text == f"⚡ 1-2 daqiqalik — {SONG_PRICE_SHORT:,} so'm":
        price, song_type = SONG_PRICE_SHORT, "1-2 daqiqalik"
    elif message.text == f"🎶 3 daqiqalik — {SONG_PRICE_FULL:,} so'm":
        price, song_type = SONG_PRICE_FULL, "3 daqiqalik"
    else:
        await message.answer("Iltimos, quyidagi tugmalardan birini tanlang:", reply_markup=get_song_type_menu())
        return
    user_data = db_get_user(message.from_user.id)
    balance = user_data[0] if user_data else 0
    if balance < price:
        await message.answer(
            f"⚠️ <b>Balansingiz yetarli emas</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Kerakli: {price:,} so'm\n"
            f"💳 Sizda: {balance:,} so'm",
            parse_mode="HTML", reply_markup=get_main_menu(message.from_user.id)
        )
        await state.clear()
        return
    await state.update_data(song_type=song_type, song_price=price)
    await message.answer(
        f"✅ <b>{song_type}</b> tanlandi — {price:,} so'm",
        parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
    )
    await message.answer(
        "📌 <b>Qo'shiq nima haqida bo'ladi?</b>\nQuyidagilardan birini tanlang yoki o'z g'oyangizni yozing:",
        parse_mode="HTML", reply_markup=get_song_topic_menu()
    )
    await state.set_state(CreateSong.waiting_for_topic)

# --- MAVZU TANLASH ---
@dp.callback_query(CreateSong.waiting_for_topic, F.data == "song_cancel")
async def song_topic_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("❌ Bekor qilindi.")
    except Exception:
        pass
    await callback.message.answer("Bosh menyudasiz.", reply_markup=get_main_menu(callback.from_user.id))
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_topic, F.data == "topic_custom")
async def song_topic_custom(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text("✍️ <b>Qo'shiq kimga atalgan yoki nima haqida bo'lishi kerak?</b>\nYozib qoldiring:", parse_mode="HTML")
    except Exception:
        pass
    await state.set_state(CreateSong.waiting_for_custom_topic)
    await callback.answer()

@dp.message(CreateSong.waiting_for_custom_topic)
async def song_topic_custom_text(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=get_main_menu(message.from_user.id))
        return
    if not message.text:
        await message.answer("Iltimos, matn ko'rinishida yuboring:")
        return
    await state.update_data(topic_label=message.text, topic_text=message.text)
    await message.answer(f"✅ <b>Mavzu:</b> {message.text}", parse_mode="HTML")
    await message.answer("🎙 <b>Vokal turini tanlang:</b>", parse_mode="HTML", reply_markup=get_voice_type_menu())
    await state.set_state(CreateSong.waiting_for_voice)

@dp.callback_query(CreateSong.waiting_for_topic, F.data.startswith("topic_"))
async def song_topic_choice(callback: CallbackQuery, state: FSMContext):
    label = TOPIC_LABELS.get(callback.data, callback.data)
    await state.update_data(topic_label=label, topic_text=label)
    try:
        await callback.message.edit_text(f"✅ <b>Mavzu:</b> {label}", parse_mode="HTML")
    except Exception:
        pass
    await callback.message.answer("🎙 <b>Vokal turini tanlang:</b>", parse_mode="HTML", reply_markup=get_voice_type_menu())
    await state.set_state(CreateSong.waiting_for_voice)
    await callback.answer()

# --- OVOZ TURI TANLASH ---
@dp.callback_query(CreateSong.waiting_for_voice, F.data == "song_cancel")
async def song_voice_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("❌ Bekor qilindi.")
    except Exception:
        pass
    await callback.message.answer("Bosh menyudasiz.", reply_markup=get_main_menu(callback.from_user.id))
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_voice, F.data == "song_back_topic")
async def song_voice_back(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text(
            "📌 <b>Qo'shiq nima haqida bo'ladi?</b>\nQuyidagilardan birini tanlang yoki o'z g'oyangizni yozing:",
            parse_mode="HTML", reply_markup=get_song_topic_menu()
        )
    except Exception:
        pass
    await state.set_state(CreateSong.waiting_for_topic)
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_voice, F.data.startswith("voice_"))
async def song_voice_choice(callback: CallbackQuery, state: FSMContext):
    label = VOICE_LABELS.get(callback.data, callback.data)
    await state.update_data(voice_label=label)
    try:
        await callback.message.edit_text(f"✅ <b>Vokal:</b> {label}", parse_mode="HTML")
    except Exception:
        pass
    await callback.message.answer(
        "📝 <b>Sizda qo'shiq uchun tayyor matn bormi?</b>\n\n"
        "Agar yo'q bo'lsa — xavotir olmang, men o'zim yozib beraman 😊",
        parse_mode="HTML", reply_markup=get_has_text_menu()
    )
    await state.set_state(CreateSong.waiting_for_has_text)
    await callback.answer()

# --- MATN BOR/YO'Q ---
@dp.callback_query(CreateSong.waiting_for_has_text, F.data == "song_cancel")
async def song_hastext_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("❌ Bekor qilindi.")
    except Exception:
        pass
    await callback.message.answer("Bosh menyudasiz.", reply_markup=get_main_menu(callback.from_user.id))
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_has_text, F.data == "song_back_voice")
async def song_hastext_back(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text("🎙 <b>Vokal turini tanlang:</b>", parse_mode="HTML", reply_markup=get_voice_type_menu())
    except Exception:
        pass
    await state.set_state(CreateSong.waiting_for_voice)
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_has_text, F.data == "text_yes")
async def song_hastext_yes(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text("📝 <b>Qo'shiq matnini yozib yuboring:</b>", parse_mode="HTML")
    except Exception:
        pass
    await state.set_state(CreateSong.waiting_for_text)
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_has_text, F.data == "text_ai")
async def song_hastext_ai(callback: CallbackQuery, state: FSMContext):
    await state.update_data(song_text=None, ai_generate=True)
    try:
        await callback.message.edit_text(
            "🤖 <b>Yaxshi, matnni AI yozadi!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Yana bir narsa: qo'shiq <b>kim haqida</b> va <b>nima sabab</b> yozilyapti? "
            "(masalan: ismi, nimasi bilan aziz, nima munosabat bilan)\n\n"
            "✏️ Qisqacha yozib bering — bu AI matnni aniqroq yozishga yordam beradi.",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await state.set_state(CreateSong.waiting_for_ai_details)
    await callback.answer()

@dp.message(CreateSong.waiting_for_ai_details)
async def process_ai_details(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=get_main_menu(message.from_user.id))
        return
    if not message.text:
        await message.answer("Iltimos, matn ko'rinishida yozing:")
        return
    await state.update_data(ai_extra_info=message.text)
    await message.answer("✅ <b>Qabul qilindi!</b>", parse_mode="HTML")
    await message.answer("🎶 <b>Musiqa uslubini tanlang:</b>", parse_mode="HTML", reply_markup=get_genre_menu())
    await state.set_state(CreateSong.waiting_for_genre)

@dp.message(CreateSong.waiting_for_text)
async def process_song_text(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=get_main_menu(message.from_user.id))
        return
    if not message.text:
        await message.answer("Iltimos, matn ko'rinishida yuboring:")
        return
    await state.update_data(song_text=message.text, ai_generate=False)
    await message.answer("✅ <b>Matningiz qabul qilindi!</b>", parse_mode="HTML")
    await message.answer("🎶 <b>Musiqa uslubini tanlang:</b>", parse_mode="HTML", reply_markup=get_genre_menu())
    await state.set_state(CreateSong.waiting_for_genre)

# --- JANR TANLASH VA YAKUNIY BUYURTMA ---
@dp.callback_query(CreateSong.waiting_for_genre, F.data == "song_cancel")
async def song_genre_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("❌ Bekor qilindi.")
    except Exception:
        pass
    await callback.message.answer("Bosh menyudasiz.", reply_markup=get_main_menu(callback.from_user.id))
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_genre, F.data == "song_back_text")
async def song_genre_back(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text(
            "📝 <b>Sizda qo'shiq uchun tayyor matn bormi?</b>\n\nAgar yo'q bo'lsa — xavotir olmang, men o'zim yozib beraman 😊",
            parse_mode="HTML", reply_markup=get_has_text_menu()
        )
    except Exception:
        pass
    await state.set_state(CreateSong.waiting_for_has_text)
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_genre, F.data == "genre_custom")
async def song_genre_custom(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text("🎵 <b>O'zingizning janringizni yozing:</b>", parse_mode="HTML")
    except Exception:
        pass
    await state.set_state(CreateSong.waiting_for_custom_genre)
    await callback.answer()

@dp.message(CreateSong.waiting_for_custom_genre)
async def song_genre_custom_text(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=get_main_menu(message.from_user.id))
        return
    if not message.text:
        await message.answer("Iltimos, matn ko'rinishida yuboring:")
        return
    await state.update_data(genre_label=message.text)
    await proceed_after_genre(message, state, user_override=None)

@dp.callback_query(CreateSong.waiting_for_genre, F.data.startswith("genre_"))
async def song_genre_choice(callback: CallbackQuery, state: FSMContext):
    label = GENRE_LABELS.get(callback.data, callback.data)
    try:
        await callback.message.edit_text(f"✅ <b>Uslub:</b> {label}", parse_mode="HTML")
    except Exception:
        pass
    await state.update_data(genre_label=label)
    await proceed_after_genre(callback.message, state, user_override=callback.from_user)
    await callback.answer()

async def proceed_after_genre(message: Message, state: FSMContext, user_override=None):
    """
    Janr tanlangandan keyin: agar AI matn yozishi kerak bo'lsa - yozadi va
    foydalanuvchidan tasdiqlashni so'raydi. Aks holda to'g'ridan-to'g'ri buyurtmani yakunlaydi.
    """
    user = user_override or message.from_user
    data = await state.get_data()
    ai_generate = data.get('ai_generate', False)
    song_text = data.get('song_text')

    if ai_generate or not song_text:
        topic_label = data.get('topic_label', data.get('topic_text', ''))
        voice_label = data.get('voice_label', '-')
        genre_label = data.get('genre_label', '-')
        ai_extra_info = data.get('ai_extra_info', '')

        thinking_msg = await message.answer("🤖 <b>AI qo'shiq matnini yozmoqda...</b>\n⏳ Biroz kuting", parse_mode="HTML")
        lyrics = await generate_song_lyrics(topic_label, voice_label, genre_label, extra_info=ai_extra_info)
        await state.update_data(ai_written_text=lyrics)

        try:
            await thinking_msg.delete()
        except Exception:
            pass

        await message.answer(
            "✨ <b>Sizning qo'shig'ingiz uchun matn tayyor!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"{lyrics}\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Bu matn sizga ma'qulmi?",
            parse_mode="HTML",
            reply_markup=get_ai_text_approval_menu()
        )
        await state.set_state(CreateSong.waiting_for_ai_approval)
        return

    await finalize_song_order(message, state, user_override=user)

@dp.callback_query(CreateSong.waiting_for_ai_approval, F.data == "ai_text_approve")
async def ai_text_approve(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lyrics = data.get('ai_written_text', '')
    await state.update_data(song_text=lyrics)
    try:
        await callback.message.edit_text(
            "✅ <b>Matn tasdiqlandi!</b>\n⏳ Buyurtmangiz qabul qilinmoqda...",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await finalize_song_order(callback.message, state, user_override=callback.from_user)
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_ai_approval, F.data == "ai_text_retry")
async def ai_text_retry(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    topic_label = data.get('topic_label', data.get('topic_text', ''))
    voice_label = data.get('voice_label', '-')
    genre_label = data.get('genre_label', '-')
    ai_extra_info = data.get('ai_extra_info', '')

    try:
        await callback.message.edit_text("🤖 <b>AI qo'shiq matnini qayta yozmoqda...</b>\n⏳ Biroz kuting", parse_mode="HTML")
    except Exception:
        pass

    lyrics = await generate_song_lyrics(topic_label, voice_label, genre_label, extra_info=ai_extra_info)
    await state.update_data(ai_written_text=lyrics)

    await callback.message.answer(
        "✨ <b>Yangi variant tayyor!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{lyrics}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Bu matn sizga ma'qulmi?",
        parse_mode="HTML",
        reply_markup=get_ai_text_approval_menu()
    )
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_ai_approval, F.data == "ai_text_own")
async def ai_text_write_own(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text("📝 <b>Qo'shiq matnini o'zingiz yozib yuboring:</b>", parse_mode="HTML")
    except Exception:
        pass
    await state.update_data(ai_generate=False)
    await state.set_state(CreateSong.waiting_for_text)
    await callback.answer()

@dp.callback_query(CreateSong.waiting_for_ai_approval, F.data == "song_cancel")
async def ai_text_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("❌ Bekor qilindi.")
    except Exception:
        pass
    await callback.message.answer("Bosh menyudasiz.", reply_markup=get_main_menu(callback.from_user.id))
    await callback.answer()

async def finalize_song_order(message: Message, state: FSMContext, user_override=None):
    user = user_override or message.from_user
    data = await state.get_data()
    topic_text = data.get('topic_text', '')
    topic_label = data.get('topic_label', topic_text)
    voice_label = data.get('voice_label', '-')
    genre_label = data.get('genre_label', '-')
    use_tariff = data.get('use_tariff')       # None | 'plus' | 'pro'
    use_bonus = data.get('use_bonus', False)
    song_text = data.get('song_text', '')

    tariff_label = ""
    price_label = ""

    if use_tariff:
        status = get_tariff_status(user.id)
        if use_bonus:
            if status["phase"] != "bonus" or not status["bonus_available_today"]:
                await message.answer(
                    "⚠️ <b>Bonus muddati tugagan</b>\nBugungi bonus qo'shig'ingiz allaqachon ishlatilgan.",
                    parse_mode="HTML", reply_markup=get_main_menu(user.id)
                )
                await state.clear()
                return
            song_type = "30 soniyalik (bonus)" if use_tariff == "plus" else "1-2 daqiqalik (bonus)"
            today_str = datetime.now().strftime("%Y-%m-%d")
            db_use_bonus_song(user.id, today_str)
        else:
            if status["phase"] != "active" or status["songs_left"] <= 0:
                await message.answer(
                    "⚠️ <b>Tarif bo'yicha qo'shiqlar tugagan</b>",
                    parse_mode="HTML", reply_markup=get_main_menu(user.id)
                )
                await state.clear()
                return
            if not status["cooldown_ok"]:
                await message.answer(
                    f"⏳ <b>Kutish vaqti</b>\nYana {format_cooldown(status['cooldown_remaining'])} kutishingiz kerak.",
                    parse_mode="HTML", reply_markup=get_main_menu(user.id)
                )
                await state.clear()
                return
            song_type = "1-2 daqiqalik"
            db_decrement_tariff_song(user.id)
        tariff_label = "⭐ Plus" if use_tariff == "plus" else "💎 Pro"
        price_label = "🎁 Bepul (bonus)" if use_bonus else "✅ Tarif hisobidan"
    else:
        song_type = data.get('song_type', '1-2 daqiqalik')
        price = data.get('song_price', SONG_PRICE_SHORT)
        user_data = db_get_user(user.id)
        if not user_data or user_data[0] < price:
            await message.answer(
                "⚠️ <b>Balansingiz yetarli emas</b>",
                parse_mode="HTML", reply_markup=get_main_menu(user.id)
            )
            await state.clear()
            return
        db_deduct_balance(user.id, price)
        tariff_label = "Oddiy"
        price_label = f"{price:,} so'm"

    user_info = f"@{user.username}" if user.username else "username yo'q"
    admin_msg = (
        f"🎤 <b>YANGI BUYURTMA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>{user.full_name}</b>\n"
        f"🔗 {user_info}\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"📦 <b>Tarif:</b> {tariff_label}\n"
        f"⏱ <b>Turi:</b> {song_type}\n"
        f"💰 <b>{price_label}</b>\n"
        f"🎙 <b>Vokal:</b> {voice_label}\n"
        f"🎶 <b>Uslub:</b> {genre_label}\n"
        f"📌 <b>Mavzu:</b> {topic_label}\n\n"
        f"📝 <b>Matn:</b>\n{song_text}"
    )
    try:
        await bot.send_message(ADMIN_ID, admin_msg, parse_mode="HTML")

        extra_info = ""
        if use_tariff and not use_bonus:
            new_status = get_tariff_status(user.id)
            extra_info = f"\n🎼 Tarif bo'yicha qolgan qo'shiqlar: <b>{new_status['songs_left']} ta</b>"
        elif not use_tariff:
            remaining_balance = db_get_user(user.id)[0]
            extra_info = f"\n💰 Joriy balans: <b>{remaining_balance:,} so'm</b>"

        await message.answer(
            f"🎉 <b>Buyurtmangiz qabul qilindi!</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 Tarif: {tariff_label}\n"
            f"⏳ Qo'shiq <b>24 soat</b> ichida tayyor bo'lib, sizga yuboriladi.\n"
            f"{extra_info}",
            parse_mode="HTML",
            reply_markup=get_main_menu(user.id)
        )
    except Exception as e:
        logging.error(f"Admin ga yuborishda xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi.", reply_markup=get_main_menu(user.id))
    await state.clear()

# === ADMIN PANEL ===
@dp.message(F.text == "🔐 Admin Panel")
async def admin_panel_cmd(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("🔐 <b>Boshqaruv paneli</b>", parse_mode="HTML", reply_markup=get_admin_menu())

@dp.message(F.text == "📈 Statistika")
async def stats_cmd(message: Message):
    if message.from_user.id != ADMIN_ID: return
    active_count, total, blocked_count = db_get_stats()
    samples = db_get_samples()
    db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    await message.answer(
        f"📈 <b>Bot Statistikasi:</b>\n\n"
        f"👥 Faol a'zolar: {active_count or 0} ta\n"
        f"🚫 Bloklaganlar: {blocked_count or 0} ta\n"
        f"💰 Jami kiritilgan pul: {total or 0:,} so'm\n"
        f"🎵 Namuna qo'shiqlar: {len(samples)} ta\n\n"
        f"🗄 <b>Baza fayli:</b>\n<code>{DB_PATH}</code>\n"
        f"📦 Hajmi: {db_size:,} bayt",
        parse_mode="HTML"
    )

@dp.message(F.text == "💰 Pul berish")
async def give_money_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("Foydalanuvchi ID raqamini kiriting:")
    await state.set_state(AdminActions.waiting_for_user_id_p)

@dp.message(AdminActions.waiting_for_user_id_p)
async def give_money_id(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("ID faqat raqamlardan iborat bo'lishi kerak:")
        return
    await state.update_data(target_id=message.text)
    await message.answer("Summani kiriting:")
    await state.set_state(AdminActions.waiting_for_money)

@dp.message(AdminActions.waiting_for_money)
async def give_money_final(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
        data = await state.get_data()
        target_id = int(data['target_id'])
        db_add_balance(target_id, amount)
        await message.answer("✅ Pul muvaffaqiyatli qo'shildi.")
        await safe_send_message(target_id, f"🎉 Balansingizga admin tomonidan {amount:,} so'm qo'shildi!")
    except ValueError:
        await message.answer("❌ Summa faqat raqam bo'lishi kerak.")
    finally:
        await state.clear()

@dp.message(F.text == "✉️ Xabar yuborish")
async def send_msg_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.set_state(AdminActions.waiting_for_broadcast_choice)
    await message.answer("📨 <b>Kimga yubormoqchisiz?</b>", parse_mode="HTML", reply_markup=get_broadcast_choice_keyboard())

@dp.callback_query(F.data == "broadcast_all")
async def broadcast_all_choice(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.update_data(broadcast_type="all")
    await state.set_state(AdminActions.waiting_for_message)
    await callback.message.edit_text("📢 <b>Hammaga yuborish</b>\n\nXabar, qo'shiq yoki faylni yuboring:", parse_mode="HTML")

@dp.callback_query(F.data == "broadcast_one")
async def broadcast_one_choice(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.update_data(broadcast_type="one")
    await state.set_state(AdminActions.waiting_for_user_id_m)
    await callback.message.edit_text("👤 <b>1 kishiga yuborish</b>\n\nTelegram ID sini kiriting:", parse_mode="HTML")

@dp.message(AdminActions.waiting_for_user_id_m)
async def send_msg_id(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("❌ ID faqat raqam bo'lishi kerak:")
        return
    await state.update_data(target_id=message.text)
    await message.answer("📝 Xabar yoki faylni yuboring:")
    await state.set_state(AdminActions.waiting_for_message)

@dp.message(AdminActions.waiting_for_message)
async def send_msg_final(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("broadcast_type") == "all":
        user_ids = db_get_all_user_ids()
        success = failed = blocked = 0
        await message.answer(f"⏳ {len(user_ids)} ta foydalanuvchiga yuborilmoqda...")
        for uid in user_ids:
            try:
                await message.copy_to(chat_id=uid)
                success += 1
                db_set_blocked(uid, False)
                await asyncio.sleep(0.05)
            except TelegramForbiddenError:
                failed += 1
                blocked += 1
                db_set_blocked(uid, True)
            except Exception:
                failed += 1
        await message.answer(
            f"✅ Yakunlandi!\n✔️ Muvaffaqiyatli: {success} ta\n❌ Yuborilmadi: {failed} ta\n🚫 Bloklaganlar: {blocked} ta",
            reply_markup=get_admin_menu()
        )
    else:
        try:
            await message.copy_to(chat_id=int(data.get("target_id", 0)))
            await message.answer("✅ Yuborildi!", reply_markup=get_admin_menu())
        except Exception as e:
            await message.answer(f"❌ Xatolik: {e}", reply_markup=get_admin_menu())
    await state.clear()

@dp.message(F.text == "🎵 Namuna qo'shish")
async def add_sample_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    samples = db_get_samples()
    info = ""
    if samples:
        info = "📋 <b>Mavjud namunalar:</b>\n"
        for s in samples:
            info += f"  • [{s[0]}] {s[1]}\n"
        info += "\n"
    await message.answer(f"{info}➕ <b>Yangi namuna nomi:</b>", parse_mode="HTML")
    await state.set_state(AdminActions.waiting_for_sample_title)

@dp.message(AdminActions.waiting_for_sample_title)
async def add_sample_title(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Nom kiriting:")
        return
    await state.update_data(sample_title=message.text)
    await message.answer("📝 <b>Tavsif yozing:</b>", parse_mode="HTML")
    await state.set_state(AdminActions.waiting_for_sample_desc)

@dp.message(AdminActions.waiting_for_sample_desc)
async def add_sample_desc(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Tavsif kiriting:")
        return
    await state.update_data(sample_desc=message.text)
    await message.answer("🎵 <b>Audio faylini yuboring</b> yoki /skip yozing:", parse_mode="HTML")
    await state.set_state(AdminActions.waiting_for_sample_file)

@dp.message(AdminActions.waiting_for_sample_file)
async def add_sample_file(message: Message, state: FSMContext):
    data = await state.get_data()
    title = data.get("sample_title", "Nomsiz")
    desc = data.get("sample_desc", "")
    file_id = None
    if message.audio: file_id = message.audio.file_id
    elif message.voice: file_id = message.voice.file_id
    elif message.document: file_id = message.document.file_id
    elif message.text == "/skip": file_id = None
    else:
        await message.answer("Audio fayl yuboring yoki /skip yozing:")
        return
    db_add_sample(title, desc, file_id)
    await message.answer(f"✅ Namuna qo'shildi!\n\n🎵 <b>{title}</b>\n{desc}", parse_mode="HTML", reply_markup=get_admin_menu())
    await state.clear()

# === NAMUNA O'CHIRISH ===
@dp.message(F.text == "🗑 Namuna o'chirish")
async def sample_delete_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    try:
        samples = db_get_samples()
    except Exception as e:
        logging.error(f"Namunalarni olishda xatolik: {e}")
        await message.answer(f"❌ Xatolik: {e}", reply_markup=get_admin_menu())
        return
    if not samples:
        await message.answer("🗑 Hozircha namuna qo'shiqlar yo'q.", reply_markup=get_admin_menu())
        return
    buttons = []
    for sample in samples:
        sample_id, title, description, file_id = sample
        buttons.append([InlineKeyboardButton(text=f"🗑 {title}", callback_data=f"sample_del_{sample_id}")])
    await message.answer(
        "🗑 <b>Qaysi namunani o'chirmoqchisiz?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@dp.callback_query(F.data.startswith("sample_del_"))
async def sample_delete_confirm(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Siz admin emassiz!", show_alert=True)
        return
    try:
        sample_id = int(callback.data.split("_")[2])
        deleted = db_delete_sample(sample_id)
        if deleted:
            try:
                await callback.message.edit_text("✅ Namuna o'chirildi.")
            except Exception:
                pass
            await callback.answer("✅ O'chirildi!")
        else:
            await callback.answer("❌ Namuna topilmadi (allaqachon o'chirilgan bo'lishi mumkin).", show_alert=True)
    except Exception as e:
        logging.error(f"Namuna ochirishda xatolik: {e}")
        await callback.answer(f"❌ Xatolik: {e}", show_alert=True)

# === PROMOKOD QO'SHISH (faqat admin) ===
@dp.message(F.text == "🎁 Promokod qo'shish")
async def promo_add_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    await message.answer(
        "🎁 <b>Yangi promokod</b>\n\n"
        "Promokod matnini yuboring (harflar/raqamlar, bo'shliqsiz, masalan: <code>BONUS2026</code>):",
        parse_mode="HTML"
    )
    await state.set_state(AdminActions.waiting_for_promo_code)

@dp.message(AdminActions.waiting_for_promo_code)
async def promo_add_code(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    code = (message.text or "").strip()
    if not code or not re.match(r'^[A-Za-z0-9_\-]{3,30}$', code):
        await message.answer("❌ Notogri format. Faqat harf/raqam (3-30 belgi), bo'shliqsiz yuboring:")
        return
    await state.update_data(promo_code=code)
    await message.answer(f"💰 <b>{code}</b> promokodi uchun qancha summa (so'm) qo'shilsin?", parse_mode="HTML")
    await state.set_state(AdminActions.waiting_for_promo_amount)

@dp.message(AdminActions.waiting_for_promo_amount)
async def promo_add_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = int((message.text or "").replace(" ", "").replace(",", ""))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Iltimos, musbat son kiriting (masalan: 20000):")
        return
    data = await state.get_data()
    code = data.get("promo_code")
    try:
        ok = db_add_promo_code(code, amount)
    except Exception as e:
        logging.error(f"Promokod qoshishda xatolik: {e}")
        await message.answer(f"❌ Xatolik: {e}", reply_markup=get_admin_menu())
        await state.clear()
        return
    if ok:
        await message.answer(
            f"✅ Promokod yaratildi!\n\n🎁 Kod: <code>{code}</code>\n💰 Summa: {amount:,} so'm\n\n"
            "Foydalanuvchi shu kodni botga xabar sifatida yuborsa, balansi avtomatik to'ldiriladi "
            "(bir marta ishlatiladi, keyin o'chadi).",
            parse_mode="HTML", reply_markup=get_admin_menu()
        )
    else:
        await message.answer(
            f"❌ Bu kod (<code>{code}</code>) allaqachon mavjud. Boshqa kod tanlang.",
            parse_mode="HTML", reply_markup=get_admin_menu()
        )
    await state.clear()

async def main():
    # Bot ishga tushganda admin ga baza holatini xabar qilamiz —
    # shunda har safar yangilashda statistika saqlanganini darhol bilasiz.
    try:
        active_count, total, blocked_count = db_get_stats()
        status_text = "🆕 YANGI (bo'sh)" if _db_is_new else "✅ ESKI (ma'lumotlar saqlangan)"
        await bot.send_message(
            ADMIN_ID,
            f"🤖 <b>Bot ishga tushdi</b>\n\n"
            f"🗄 Baza holati: {status_text}\n"
            f"📍 Yo'li: <code>{DB_PATH}</code>\n"
            f"👥 Faol a'zolar: {active_count or 0} ta\n"
            f"🚫 Bloklaganlar: {blocked_count or 0} ta\n"
            f"💰 Jami pul: {total or 0:,} so'm",
            parse_mode="HTML"
        )
        if _db_is_new and active_count == 0:
            await bot.send_message(
                ADMIN_ID,
                "⚠️ <b>Diqqat!</b> Baza yangi (bo'sh) holatda yaratildi.\n"
                "Agar avval foydalanuvchilar bo'lgan bo'lsa, ular yo'qolgan bo'lishi mumkin.\n\n"
                "Buni oldini olish uchun <code>.env</code> faylida <code>DB_PATH</code>ni "
                "server/hostingdagi <b>doimiy (persistent) disk</b> yo'liga o'rnating "
                "(masalan Railway/Render'da ulangan Volume ichiga).",
                parse_mode="HTML"
            )
    except Exception as e:
        logging.error(f"Bot ishga tushganda xabar yuborishda xatolik: {e}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
