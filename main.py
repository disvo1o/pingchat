import asyncio
import logging
import os
import random
from contextlib import asynccontextmanager

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pingchat")

BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
TURN_TIMEOUT = int(os.getenv("TURN_TIMEOUT_SECONDS", "30"))

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
pool: asyncpg.Pool | None = None
pending: dict[int, dict] = {}
active_locks: dict[int, asyncio.Lock] = {}

SHOTS = {
    "topspin": ("🔥 Топспин", 0.66, 0.10),
    "block": ("🛡 Блок", 0.88, -0.08),
    "chop": ("↘️ Подрезка", 0.78, -0.02),
    "smash": ("⚡ Смэш", 0.48, 0.25),
}


def display_name(user) -> str:
    return user.full_name or user.username or str(user.id)


async def ensure_player(user):
    async with pool.acquire() as conn:
        await conn.execute(
            """insert into players(telegram_id, username, full_name)
               values($1,$2,$3)
               on conflict(telegram_id) do update set
               username=excluded.username, full_name=excluded.full_name,
               updated_at=now()""",
            user.id, user.username, display_name(user)
        )


def match_keyboard(match_id: int, player_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, (label, _, _) in SHOTS.items():
        kb.button(text=label, callback_data=f"shot:{match_id}:{key}")
    kb.adjust(2, 2)
    kb.row(InlineKeyboardButton(text="🏳 Сдаться", callback_data=f"resign:{match_id}"))
    return kb.as_markup()


def score_text(row, note: str = "") -> str:
    s1, s2 = row["score1"], row["score2"]
    turn_name = row["player1_name"] if row["current_turn"] == row["player1_id"] else row["player2_name"]
    text = (
        "🏓 <b>PING TABLET CHAT PONG</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🏓 <b>{row['player1_name']}</b>　 <b>{s1}</b> : <b>{s2}</b>　<b>{row['player2_name']}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )
    if note:
        text += f"{note}\n\n"
    text += f"🎯 Ход: <b>{turn_name}</b>\nВыбери удар:"
    return text


async def get_match(match_id: int):
    async with pool.acquire() as conn:
        return await conn.fetchrow("select * from matches where id=$1", match_id)


async def finish_match(row, winner_id: int, note: str):
    loser_id = row["player2_id"] if winner_id == row["player1_id"] else row["player1_id"]
    async with pool.acquire() as conn:
        await conn.execute(
            """update matches set status='finished', winner_id=$2, finished_at=now()
               where id=$1""", row["id"], winner_id
        )
        await conn.execute(
            """update players set games_played=games_played+1,
               wins=wins+case when telegram_id=$1 then 1 else 0 end,
               losses=losses+case when telegram_id=$2 then 1 else 0 end,
               points_scored=points_scored+case when telegram_id=$1 then $3 else $4 end,
               points_conceded=points_conceded+case when telegram_id=$1 then $4 else $3 end,
               updated_at=now() where telegram_id in ($1,$2)""",
            winner_id, loser_id, row["score1"], row["score2"]
        )
    winner = row["player1_name"] if winner_id == row["player1_id"] else row["player2_name"]
    await bot.edit_message_text(
        f"🏆 <b>PING TABLET CHAT PONG</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"<b>{row['player1_name']}</b>　{row['score1']} : {row['score2']}　<b>{row['player2_name']}</b>\n\n"
        f"🥇 Победитель: <b>{winner}</b>\n{note}",
        chat_id=row["chat_id"], message_id=row["message_id"], parse_mode="HTML"
    )


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "🏓 <b>PING TABLET CHAT PONG</b>\n"
        "Играй в настольный теннис прямо в групповом чате!\n\n"
        "Чтобы вызвать друга, ответь на его сообщение командой /pong.\n"
        "Также можно написать /pong @username.\n"
        "Статистика: /pong_stats",
        parse_mode="HTML"
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
            chat = await bot.get_chat(args[1])
            opponent = chat
        except Exception:
            await message.reply("Не смог найти игрока по username. Попробуй ответить командой /pong на его сообщение.")
            return

    if not opponent:
        await message.reply("Чтобы вызвать друга, ответь на его сообщение командой /pong или напиши /pong @username.")
        return
    if opponent.id == message.from_user.id or opponent.is_bot:
        await message.reply("Нужно вызвать другого человека, не себя и не бота 🙂")
        return

    await ensure_player(message.from_user)
    await ensure_player(opponent)
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "select exists(select 1 from matches where chat_id=$1 and status='active' and (player1_id=$2 or player2_id=$2))",
            message.chat.id, message.from_user.id
        )
        other_exists = await conn.fetchval(
            "select exists(select 1 from matches where chat_id=$1 and status='active' and (player1_id=$2 or player2_id=$2))",
            message.chat.id, opponent.id
        )
    if exists or other_exists:
        await message.reply("Один из игроков уже участвует в активном матче.")
        return

    token = random.randint(100000, 999999)
    pending[token] = {
        "chat_id": message.chat.id, "challenger": message.from_user,
        "opponent": opponent, "message_id": None
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏓 Принять вызов", callback_data=f"accept:{token}"),
        InlineKeyboardButton(text="🙅 Отказаться", callback_data=f"decline:{token}")
    ]])
    sent = await message.answer(
        f"🏓 <b>ВЫЗОВ НА МАТЧ!</b>\n\n"
        f"{display_name(message.from_user)} вызывает {display_name(opponent)}!\n"
        "Матч до 11 очков, разница минимум 2.\n\n"
        "Соперник, принимаешь?",
        reply_markup=kb, parse_mode="HTML"
    )
    pending[token]["message_id"] = sent.message_id


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
    async with pool.acquire() as conn:
        busy = await conn.fetchval(
            """select exists(select 1 from matches where chat_id=$1 and status='active'
               and (player1_id in ($2,$3) or player2_id in ($2,$3)))""",
            item["chat_id"], item["challenger"].id, item["opponent"].id
        )
        if busy:
            await call.answer("Один из игроков уже играет.", show_alert=True)
            return
        match_id = await conn.fetchval(
            """insert into matches(chat_id, player1_id, player2_id, player1_name, player2_name, current_turn)
               values($1,$2,$3,$4,$5,$2) returning id""",
            item["chat_id"], item["challenger"].id, item["opponent"].id,
            display_name(item["challenger"]), display_name(item["opponent"])
        )
        await conn.execute("update matches set message_id=$2 where id=$1", match_id, call.message.message_id)
        row = await conn.fetchrow("select * from matches where id=$1", match_id)
    pending.pop(token, None)
    await call.message.edit_text(score_text(row, "Матч начался! Первым подаёт игрок 1."), reply_markup=match_keyboard(match_id, row["current_turn"]), parse_mode="HTML")
    await call.answer("Матч начался! Удачи 🏓")


@dp.callback_query(F.data.startswith("shot:"))
async def shot(call: CallbackQuery):
    _, raw_id, shot = call.data.split(":")
    match_id = int(raw_id)
    lock = active_locks.setdefault(match_id, asyncio.Lock())
    async with lock:
        row = await get_match(match_id)
        if not row or row["status"] != "active":
            await call.answer("Матч уже завершён.")
            return
        if call.from_user.id != row["current_turn"]:
            await call.answer("Сейчас ход другого игрока.", show_alert=True)
            return

        label, base_accuracy, attack = SHOTS[shot]
        # The rally result is probabilistic, with a distinct risk/reward profile per shot.
        point_probability = max(0.12, min(0.88, 0.50 + (base_accuracy - 0.70) * 0.8 - attack * 0.35))
        scored = random.random() < point_probability
        scorer = call.from_user.id if scored else (row["player2_id"] if call.from_user.id == row["player1_id"] else row["player1_id"])
        s1 = row["score1"] + (1 if scorer == row["player1_id"] else 0)
        s2 = row["score2"] + (1 if scorer == row["player2_id"] else 0)
        total = s1 + s2
        next_turn = row["player2_id"] if call.from_user.id == row["player1_id"] else row["player1_id"]
        async with pool.acquire() as conn:
            await conn.execute(
                "update matches set score1=$2, score2=$3, current_turn=$4, last_shot=$5 where id=$1",
                match_id, s1, s2, next_turn, shot
            )
            row = await conn.fetchrow("select * from matches where id=$1", match_id)

        note = f"{label} — {'очко!' if scored else 'ошибка! Очко сопернику.'}"
        if max(s1, s2) >= 11 and abs(s1 - s2) >= 2:
            await finish_match(row, row["player1_id"] if s1 > s2 else row["player2_id"], note)
            await call.answer("Матч завершён!")
            return

        await call.message.edit_text(score_text(row, note), reply_markup=match_keyboard(match_id, next_turn), parse_mode="HTML")
        await call.answer("Розыгрыш завершён!")


@dp.callback_query(F.data.startswith("resign:"))
async def resign(call: CallbackQuery):
    match_id = int(call.data.split(":")[1])
    row = await get_match(match_id)
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
    async with pool.acquire() as conn:
        row = await conn.fetchrow("select * from players where telegram_id=$1", message.from_user.id)
    rate = round(row["wins"] / row["games_played"] * 100) if row["games_played"] else 0
    await message.reply(
        f"📊 <b>СТАТИСТИКА PONG</b>\n\n"
        f"Игрок: <b>{row['full_name']}</b>\n"
        f"Матчей: {row['games_played']}\n"
        f"Побед: {row['wins']}\nПоражений: {row['losses']}\n"
        f"Процент побед: {rate}%",
        parse_mode="HTML"
    )


async def main():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    log.info("PING TABLET CHAT PONG started")
    try:
        await dp.start_polling(bot)
    finally:
        await pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
