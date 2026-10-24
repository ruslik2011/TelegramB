import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------- конфиг ----------
load_dotenv("secret.env")
TOKEN = os.getenv("TT")


_allowed_raw = os.getenv("ALLOWED_USERS", "").strip()
ALLOWED_USERS = {int(x) for x in _allowed_raw.split(",") if x.strip()} if _allowed_raw else set()

def is_allowed(user_id: int) -> bool:
    return not ALLOWED_USERS or user_id in ALLOWED_USERS

# ---------- БД ----------
DB_PATH = "music.db"

def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                file_unique_id TEXT NOT NULL,
                file_type TEXT NOT NULL,
                title TEXT,
                performer TEXT,
                duration INTEGER,
                file_name TEXT,
                added_at TEXT NOT NULL
            )
        """)
        con.commit()
def add_track(user_id, file_id, file_unique_id, file_type,
              title, performer, duration, file_name) -> bool:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("""
            SELECT 1 FROM tracks
            WHERE user_id = ?
              AND COALESCE(file_name,'')  = COALESCE(?, '')
              AND COALESCE(title,'')      = COALESCE(?, '')
              AND COALESCE(performer,'')  = COALESCE(?, '')
            LIMIT 1
        """, (user_id, file_name, title, performer))
        if cur.fetchone():
            return False

        con.execute("""
            INSERT INTO tracks
            (user_id, file_id, file_unique_id, file_type, title, performer,
             duration, file_name, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, file_id, file_unique_id, file_type, title, performer,
              duration, file_name, datetime.utcnow().isoformat()))
        con.commit()
        return True

def count_tracks(user_id) -> int:
    with sqlite3.connect(DB_PATH) as con:
        return con.execute(
            "SELECT COUNT(*) FROM tracks WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

def search_tracks(user_id, query, limit=20):
    q = f"%{query.lower()}%"
    with sqlite3.connect(DB_PATH) as con:
        return con.execute("""
            SELECT file_id, file_type, title, performer
            FROM tracks
            WHERE user_id = ?
              AND (LOWER(COALESCE(title,''))     LIKE ?
                OR LOWER(COALESCE(performer,'')) LIKE ?
                OR LOWER(COALESCE(file_name,'')) LIKE ?)
            ORDER BY added_at DESC
            LIMIT ?
        """, (user_id, q, q, q, limit)).fetchall()

def get_random(user_id):
    with sqlite3.connect(DB_PATH) as con:
        return con.execute("""
            SELECT file_id, file_type FROM tracks
            WHERE user_id = ? ORDER BY RANDOM() LIMIT 1
        """, (user_id,)).fetchone()

def get_recent(user_id, limit=10):
    with sqlite3.connect(DB_PATH) as con:
        return con.execute("""
            SELECT file_id, file_type, title, performer FROM tracks
            WHERE user_id = ? ORDER BY added_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()

# ---------- отправка по типу ----------
async def send_by_type(update: Update, file_type: str, file_id: str, caption=None):
    msg = update.effective_message
    if file_type == "audio":
        await msg.reply_audio(file_id, caption=caption)
    elif file_type == "video":
        await msg.reply_video(file_id, caption=caption)
    else:
        await msg.reply_document(file_id, caption=caption)

# ---------- команды ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    keyboard = [
        [
            InlineKeyboardButton("📊 Всего треков", callback_data="count"),
            InlineKeyboardButton("🎲 Случайный", callback_data="random"),
        ],
        [
            InlineKeyboardButton("🕒 Последние", callback_data="recent"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🎧 <b>Хранитель музыки</b>\n\n"
        "Кидай мне mp3 / mp4 / аудио / видео — я сохраню в твоей библиотеке.\n\n"
        "<b>Команды:</b>\n"
        "/count — сколько треков\n"
        "/find &lt;запрос&gt; — поиск\n"
        "/recent — последние 10\n"
        "/random — случайный трек",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "Просто пришли аудио, голосовое, видео или документ — я сохраню.\n"
        "Файлы физически лежат в Telegram, я запоминаю только file_id."
    )

async def cmd_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    n = count_tracks(update.effective_user.id)
    await update.message.reply_text(f"📚 В библиотеке: <b>{n}</b> трек(ов).", parse_mode="HTML")

async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /find название или исполнитель")
        return
    query = " ".join(context.args).strip()
    rows = search_tracks(update.effective_user.id, query)
    if not rows:
        await update.message.reply_text("Ничего не найдено 🤷")
        return
    await update.message.reply_text(f"🔎 Найдено: {len(rows)}. Отправляю...")
    for file_id, file_type, title, performer in rows:
        caption = " — ".join(x for x in (performer, title) if x) or None
        await send_by_type(update, file_type, file_id, caption)

async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    rows = get_recent(update.effective_user.id, 10)
    if not rows:
        await update.message.reply_text("Библиотека пуста. Пришли первый трек!")
        return
    for file_id, file_type, title, performer in rows:
        caption = " — ".join(x for x in (performer, title) if x) or None
        await send_by_type(update, file_type, file_id, caption)

async def cmd_random(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    row = get_random(update.effective_user.id)
    if not row:
        await update.message.reply_text("Библиотека пуста.")
        return
    file_id, file_type = row
    await send_by_type(update, file_type, file_id, "🎲 Случайный трек")

# ---------- приём файлов ----------
async def reply_saved(update: Update, ok: bool, name: str | None):
    if ok:
        n = count_tracks(update.effective_user.id)
        await update.message.reply_text(
            f"✅ Сохранено: <b>{name or 'без названия'}</b>\nВсего: {n}",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text("⚠️ Такой файл уже есть в библиотеке.")

async def on_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    a = update.message.audio
    ok = add_track(
        update.effective_user.id, a.file_id, a.file_unique_id, "audio",
        a.title, a.performer, a.duration, a.file_name,
    )
    await reply_saved(update, ok, a.title or a.file_name)

async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    v = update.message.video
    ok = add_track(
        update.effective_user.id, v.file_id, v.file_unique_id, "video",
        v.file_name, None, v.duration, v.file_name,
    )
    await reply_saved(update, ok, v.file_name)

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    d = update.message.document
    name = (d.file_name or "").lower()
    mime = (d.mime_type or "").lower()
    if not (mime.startswith("audio/") or mime.startswith("video/")
            or name.endswith((".mp3", ".mp4", ".m4a", ".ogg", ".wav", ".flac"))):
        await update.message.reply_text("Это не похоже на аудио/видео 🤔")
        return
    file_type = "video" if (mime.startswith("video/") or name.endswith(".mp4")) else "audio"
    ok = add_track(
        update.effective_user.id, d.file_id, d.file_unique_id, file_type,
        d.file_name, None, None, d.file_name,
    )
    await reply_saved(update, ok, d.file_name)

# ---------- кнопки ----------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_allowed(uid):
        return

    if q.data == "count":
        n = count_tracks(uid)
        await q.message.reply_text(f"📚 В библиотеке: <b>{n}</b> трек(ов).", parse_mode="HTML")
    elif q.data == "random":
        row = get_random(uid)
        if not row:
            await q.message.reply_text("Библиотека пуста.")
            return
        file_id, file_type = row
        if file_type == "audio":
            await q.message.reply_audio(file_id, caption="🎲 Случайный трек")
        elif file_type == "video":
            await q.message.reply_video(file_id, caption="🎲 Случайный трек")
        else:
            await q.message.reply_document(file_id, caption="🎲 Случайный трек")
    elif q.data == "recent":
        rows = get_recent(uid, 10)
        if not rows:
            await q.message.reply_text("Библиотека пуста.")
            return
        for file_id, file_type, title, performer in rows:
            caption = " — ".join(x for x in (performer, title) if x) or None
            if file_type == "audio":
                await q.message.reply_audio(file_id, caption=caption)
            elif file_type == "video":
                await q.message.reply_video(file_id, caption=caption)
            else:
                await q.message.reply_document(file_id, caption=caption)

# ---------- сборка приложения ----------
def build_app():
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("count", cmd_count))
    application.add_handler(CommandHandler("find", cmd_find))
    application.add_handler(CommandHandler("recent", cmd_recent))
    application.add_handler(CommandHandler("random", cmd_random))

    application.add_handler(MessageHandler(filters.AUDIO, on_audio))
    application.add_handler(MessageHandler(filters.VIDEO, on_video))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))

    application.add_handler(CallbackQueryHandler(on_callback))

    return application


if __name__ == "__main__":
    init_db()
    app = build_app()
    app.run_polling()