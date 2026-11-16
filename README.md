# PING TABLET CHAT PONG

Telegram group mini-game for table tennis.

## MVP
- Challenge a friend by replying to their message with /pong.
- The opponent accepts or declines.
- Players choose shots with inline buttons.
- First to 11 points, win by 2.
- The bot edits one match message instead of spamming the chat.
- Player statistics are stored in SQLite.

## Stack
- Python 3.12+
- aiogram 3.22
- SQLite (aiosqlite)
- Railway worker with a persistent Volume

## Railway variables
- BOT_TOKEN — token from @BotFather
- DB_PATH — /data/pingpong.db (when the Volume is mounted at /data)

The bot creates its tables automatically. No Supabase account, DATABASE_URL, or manual SQL setup is needed.

## Railway setup
1. Deploy this repository from GitHub.
2. Add BOT_TOKEN.
3. Add a Volume with Mount Path /data.
4. Set DB_PATH to /data/pingpong.db.
5. Deploy.

Run locally:
```bash
pip install -r requirements.txt
DB_PATH=pingpong.db BOT_TOKEN=your-token python main.py
```
