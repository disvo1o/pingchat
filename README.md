# PING TABLET CHAT PONG

Telegram group mini-game for table tennis.

## MVP
- Start a match by replying to a friend's message with `/pong` or using `/pong @username`.
- The opponent accepts the challenge.
- Players take turns choosing a shot using inline buttons.
- First to 11 points, win by 2.
- One match message is edited after each rally to keep the group clean.
- Match history and player statistics are stored in Supabase.

## Stack
- Python 3.12+
- aiogram 3.22
- Supabase Postgres via `DATABASE_URL`
- Railway worker

## Environment variables
- `BOT_TOKEN` — Telegram bot token
- `DATABASE_URL` — Supabase Postgres connection string (use the pooler connection string if required)
- `TURN_TIMEOUT_SECONDS` — optional, default `30`

Run locally:
```bash
pip install -r requirements.txt
python main.py
```

Run `schema.sql` in the Supabase SQL editor before starting the bot.
