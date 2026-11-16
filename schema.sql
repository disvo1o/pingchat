create table if not exists players (
  telegram_id bigint primary key,
  username text,
  full_name text not null,
  games_played integer not null default 0,
  wins integer not null default 0,
  losses integer not null default 0,
  points_scored integer not null default 0,
  points_conceded integer not null default 0,
  updated_at timestamptz not null default now()
);

create table if not exists matches (
  id bigserial primary key,
  chat_id bigint not null,
  message_id bigint,
  player1_id bigint not null references players(telegram_id),
  player2_id bigint not null references players(telegram_id),
  player1_name text not null,
  player2_name text not null,
  score1 integer not null default 0,
  score2 integer not null default 0,
  current_turn bigint not null,
  last_shot text,
  status text not null default 'active',
  winner_id bigint,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);

create index if not exists matches_chat_status_idx on matches(chat_id, status);
create index if not exists matches_created_idx on matches(created_at desc);
