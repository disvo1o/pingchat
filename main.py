import asyncio
import html
import logging
import os
import random
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pingchat")
BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.getenv("DB_PATH", "/data/pingpong.db")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
db = None
db_lock = asyncio.Lock()
pending = {}
match_locks = {}
SHOTS = {
    "topspin": ("🔥 Топспин", 0.66, 0.10),
    "block": ("🛡 Блок", 0.88, -0.08),
    "chop": ("↘️ Подрезка", 0.78, -0.02),
    "smash": ("⚡ Смэш", 0.48, 0.25),
}


def display_name(user):
    name = getattr(user, "full_name", None) or getattr(user, "title", None) or getattr(user, "username", None) or str(user.id)
    return html.escape(name)


async def init_db():
    global db
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript("""
    CREATE TABLE IF NOT EXISTS players (
      telegram_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT NOT NULL,
      games_played INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
      losses INTEGER NOT NULL DEFAULT 0, points_scored INTEGER NOT NULL DEFAULT 0,
      points_conceded INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS matches (
      id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, message_id INTEGER,
      player1_id INTEGER NOT NULL REFERENCES players(telegram_id),
      player2_id INTEGER NOT NULL REFERENCES players(telegram_id),
      player1_name TEXT NOT NULL, player2_name TEXT NOT NULL,
      score1 INTEGER NOT NULL DEFAULT 0, score2 INTEGER NOT NULL DEFAULT 0,
      current_turn INTEGER NOT NULL, last_shot TEXT,
      status TEXT NOT NULL DEFAULT 'active', winner_id INTEGER,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT
    );
    CREATE INDEX IF NOT EXISTS matches_chat_status_idx ON matches(chat_id, status);
    CREATE INDEX IF NOT EXISTS matches_created_idx ON matches(created_at DESC);
    """)
    await db.commit()
    log.info("SQLite ready: %s", path)


async def ensure_player(user):
    async with db_lock:
        await db.execute(
            """INSERT INTO players(telegram_id, username, full_name) VALUES(?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username,
            full_name=excluded.full_name, updated_at=CURRENT_TIMESTAMP""",
            (user.id, getattr(user, "username", None), display_name(user))
        )
        await db.commit()


async def fetchone(sql, params=()):
    async with db_lock:
        async with db.execute(sql, params) as cur:
            return await cur.fetchone()


def match_keyboard(match_id):
    kb = InlineKeyboardBuilder()
    for key, (label, _, _) in SHOTS.items():
        kb.button(text=label, callback_data=f"shot:{match_id}:{key}")
    kb.adjust(2, 2)
    kb.row(InlineKeyboardButton(text="🏳 Сдаться", callback_data=f"resign:{match_id}"))
    return kb.as_markup()


def score_text(row, note=""):
    turn = row["player1_name"] if row["current_turn"] == row["player1_id"] else row["player2_name"]
    result = (
        "🏓 <b>PING TABLET CHAT PONG</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"<b>{row['player1_name']}</b>　<b>{row['score1']}</b> : "
        f"<b>{row['score2']}</b>　<b>{row['player2_name']}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )
    if note:
        result += f"{note}\n\n"
    return result + f"🎯 Ход: <b>{turn}</b>\nВыбери удар:"


async def finish_match(row, winner_id, note):
    loser_id = row["player2_id"] if winner_id == row["player1_id"] else row["player1_id"]
    winner_score = row["score1"] if winner_id == row["player1_id"] else row["score2"]
    loser_score = row["score2"] if winner_id == row["player1_id"] else row["score1"]
    async with db_lock:
        await db.execute(
            "UPDATE matches SET status='finished',winner_id=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (winner_id, row["id"])
        )
        await db.execute(
            """UPDATE players SET games_played=games_played+1,
            wins=wins+CASE WHEN telegram_id=? THEN 1 ELSE 0 END,
            losses=losses+CASE WHEN telegram_id=? THEN 1 ELSE 0 END,
            points_scored=points_scored+CASE WHEN telegram_id=? THEN ? ELSE ? END,
            points_conceded=points_conceded+CASE WHEN telegram_id=? THEN ? ELSE ? END,
            updated_at=CURRENT_TIMESTAMP WHERE telegram_id IN (?,?)""",
            (winner_id, loser_id, winner_id, winner_score, loser_score,
             winner_id, loser_score, winner_score, winner_id, loser_id)
        )
        await db.commit()
    winner = row["player1_name"] if winner_id == row["player1_id"] else row["player2_name"]
    await bot.edit_message_text(
        "🏆 <b>PING TABLET CHAT PONG</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"<b>{row['player1_name']}</b>　{row['score1']} : {row['score2']}　"
        f"<b>{row['player2_name']}</b>\n\n🥇 Победитель: <b>{winner}</b>\n{note}",
        chat_id=row["chat_id"], message_id=row["message_id"], parse_mode="HTML"
    )


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "🏓 <b>PING TABLET CHAT PONG</b>\n"
        "Играй в настольный теннис прямо в групповом чате!\n\n"
        "Чтобы вызвать друга, ответь на его сообщение командой /pong.\n"
        "Статистика: /pong_stats", parse_mode="HTML"
    )


@dp.message(Command("pong"))
async def challenge(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("Играть можно в общем чате. Добавь меня в группу PING TABLET 🏓")
        return
    if not message.from_user:
        return
    opponent = message.reply_to_message.from_user if message.reply_to_message else None
    args = (message.text or "").split(maxsplit=1)
    if not opponent and len(args) > 1 and args[1].startswith("@"):
        try:
            opponent = await bot.get_chat(args[1])
        except Exception:
            await message.reply("Не смог найти игрока. Попробуй ответить командой /pong на его сообщение.")
            return
    if not opponent:
        await message.reply("Ответь на сообщение друга командой /pong.")
        return
    if opponent.id == message.from_user.id or getattr(opponent, "is_bot", False):
        await message.reply("Нужно вызвать другого человека, не себя и не бота 🙂")
        return

    await ensure_player(message.from_user)
    await ensure_player(opponent)
    async with db_lock:
        async with db.execute(
            "SELECT 1 FROM matches WHERE chat_id=? AND status='active' AND (player1_id=? OR player2_id=?) LIMIT 1",
            (message.chat.id, message.from_user.id, message.from_user.id)
        ) as cur:
            busy1 = await cur.fetchone()
        async with db.execute(
            "SELECT 1 FROM matches WHERE chat_id=? AND status='active' AND (player1_id=? OR player2_id=?) LIMIT 1",
            (message.chat.id, opponent.id, opponent.id)
        ) as cur:
            busy2 = await cur.fetchone()
    if busy1 or busy2:
        await message.reply("Один из игроков уже участвует в активном матче.")
        return

    token = random.randint(100000, 999999)
    pending[token] = {"chat_id": message.chat.id, "challenger": message.from_user, "opponent": opponent}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏓 Принять вызов", callback_data=f"accept:{token}"),
        InlineKeyboardButton(text="🙅 Отказаться", callback_data=f"decline:{token}")
    ]])
    await message.answer(
        f"🏓 <b>ВЫЗОВ НА МАТЧ!</b>\n\n{display_name(message.from_user)} вызывает "
        f"{display_name(opponent)}!\nМатч до 11 очков, разница минимум 2.\n\nСоперник, принимаешь?",
        reply_markup=kb, parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("decline:"))
async def decline(call: CallbackQuery):
    token = int(call.data.split(":")[1])
    item = pending.get(token)
    if not item or call.from_user.id != item["opponent"].id:
        await call.answer("Этот вызов не для тебя.", show_alert=True)
        return
    pending.pop(token, None)
    await call.message.edit_text("Вызов отклонён.")
    await call.answer()


@dp.callback_query(F.data.startswith("accept:"))
async def accept(call: CallbackQuery):
    token = int(call.data.split(":")[1])
    item = pending.get(token)
    if not item or call.from_user.id != item["opponent"].id:
        await call.answer("Этот вызов не для тебя или он уже истёк.", show_alert=True)
        return
    await ensure_player(item["challenger"])
    await ensure_player(item["opponent"])
    async with db_lock:
        async with db.execute(
            """SELECT 1 FROM matches WHERE chat_id=? AND status='active'
            AND (player1_id IN (?,?) OR player2_id IN (?,?)) LIMIT 1""",
            (item["chat_id"], item["challenger"].id, item["opponent"].id,
             item["challenger"].id, item["opponent"].id)
        ) as cur:
            busy = await cur.fetchone()
        if busy:
            await call.answer("Один из игроков уже играет.", show_alert=True)
            return
        cur = await db.execute(
            """INSERT INTO matches(chat_id,player1_id,player2_id,player1_name,player2_name,current_turn)
            VALUES(?,?,?,?,?,?)""",
            (item["chat_id"], item["challenger"].id, item["opponent"].id,
             display_name(item["challenger"]), display_name(item["opponent"]), item["challenger"].id)
        )
        match_id = cur.lastrowid
        await db.execute("UPDATE matches SET message_id=? WHERE id=?", (call.message.message_id, match_id))
        await db.commit()
        async with db.execute("SELECT * FROM matches WHERE id=?", (match_id,)) as cur:
            row = await cur.fetchone()
    pending.pop(token, None)
    await call.message.edit_text(
        score_text(row, "Матч начался! Первым подаёт игрок 1."),
        reply_markup=match_keyboard(match_id), parse_mode="HTML"
    )
    await call.answer("Матч начался! Удачи 🏓")


@dp.callback_query(F.data.startswith("shot:"))
async def shot(call: CallbackQuery):
    _, raw_id, shot_key = call.data.split(":")
    match_id = int(raw_id)
    if shot_key not in SHOTS:
        await call.answer("Неизвестный удар.")
        return
    lock = match_locks.setdefault(match_id, asyncio.Lock())
    async with lock:
        row = await fetchone("SELECT * FROM matches WHERE id=?", (match_id,))
        if not row or row["status"] != "active":
            await call.answer("Матч уже завершён.")
            return
        if call.from_user.id != row["current_turn"]:
            await call.answer("Сейчас ход другого игрока.", show_alert=True)
            return
        label, accuracy, attack = SHOTS[shot_key]
        probability = max(0.12, min(0.88, 0.50 + (accuracy - 0.70) * 0.8 - attack * 0.35))
        scored = random.random() < probability
        scorer = call.from_user.id if scored else (
            row["player2_id"] if call.from_user.id == row["player1_id"] else row["player1_id"]
        )
        s1 = row["score1"] + (1 if scorer == row["player1_id"] else 0)
        s2 = row["score2"] + (1 if scorer == row["player2_id"] else 0)
        next_turn = row["player2_id"] if call.from_user.id == row["player1_id"] else row["player1_id"]
        async with db_lock:
            await db.execute(
                "UPDATE matches SET score1=?,score2=?,current_turn=?,last_shot=? WHERE id=?",
                (s1, s2, next_turn, shot_key, match_id)
            )
            await db.commit()
        row = await fetchone("SELECT * FROM matches WHERE id=?", (match_id,))
        note = f"{label} — {'очко!' if scored else 'ошибка! Очко сопернику.'}"
        if max(s1, s2) >= 11 and abs(s1 - s2) >= 2:
            await finish_match(row, row["player1_id"] if s1 > s2 else row["player2_id"], note)
            await call.answer("Матч завершён!")
            return
        await call.message.edit_text(
            score_text(row, note), reply_markup=match_keyboard(match_id), parse_mode="HTML"
        )
        await call.answer("Розыгрыш завершён!")


@dp.callback_query(F.data.startswith("resign:"))
async def resign(call: CallbackQuery):
    match_id = int(call.data.split(":")[1])
    lock = match_locks.setdefault(match_id, asyncio.Lock())
    async with lock:
        row = await fetchone("SELECT * FROM matches WHERE id=?", (match_id,))
        if not row or row["status"] != "active":
            await call.answer("Матч уже завершён.")
            return
        if call.from_user.id not in (row["player1_id"], row["player2_id"]):
            await call.answer("Только игрок может сдаться.", show_alert=True)
            return
        winner = row["player2_id"] if call.from_user.id == row["player1_id"] else row["player1_id"]
        await finish_match(row, winner, "Матч завершён досрочно.")
        await call.answer("Ты сдал матч.")


@dp.message(Command("pong_stats"))
async def stats(message: Message):
    if not message.from_user:
        return
    await ensure_player(message.from_user)
    row = await fetchone("SELECT * FROM players WHERE telegram_id=?", (message.from_user.id,))
    rate = round(row["wins"] / row["games_played"] * 100) if row["games_played"] else 0
    await message.reply(
        f"📊 <b>СТАТИСТИКА PONG</b>\n\nИгрок: <b>{row['full_name']}</b>\n"
        f"Матчей: {row['games_played']}\nПобед: {row['wins']}\n"
        f"Поражений: {row['losses']}\nПроцент побед: {rate}%",
        parse_mode="HTML"
    )


async def main():
    await init_db()
    log.info("PING TABLET CHAT PONG started")
    try:
        await dp.start_polling(bot)
    finally:
        if db is not None:
            await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
