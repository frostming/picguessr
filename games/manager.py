import os
import sqlite3
import textwrap
from typing import Any, Iterable

from telebot import TeleBot
from telebot.types import User

from .base import G, GameState, logger

DATA_DIR = os.getenv("DATA_DIR", "data")


class GameManager:
    def __init__(self) -> None:
        self.is_debug = False
        self._states: dict[int, GameState] = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        self._db = os.path.join(DATA_DIR, "game.db")
        self._init_db()
        self.bot = TeleBot(token=os.environ["BOT_TOKEN"])

    def set_debug(self, is_debug: bool) -> None:
        self.is_debug = is_debug

    def _init_db(self):
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                textwrap.dedent("""
                CREATE TABLE IF NOT EXISTS scores (
                    userid INTEGER NOT NULL,
                    chatid INTEGER NOT NULL,
                    name TEXT,
                    score INTEGER DEFAULT 0,
                    PRIMARY KEY (userid, chatid)
                )
                """)
            )
            conn.commit()

    def start_game(
        self, chat_id: int, game: G, init_state: dict[str, Any]
    ) -> GameState[G]:
        logger.info("Starting a new game in chat %d", chat_id)
        self._states[chat_id] = state = GameState(game, init_state)
        return state

    def get_state(self, chat_id: int) -> GameState | None:
        return self._states.get(chat_id)

    def clear_state(self, chat_id: int) -> None:
        self._states.pop(chat_id, None)

    def record_win(self, user: User, chat_id: int) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scores (userid, chatid, name, score) VALUES (?, ?, ?, COALESCE((SELECT score FROM scores WHERE userid = ?), 0) + 1)",
                (user.id, chat_id, user.full_name, user.id),
            )
            conn.commit()

    def get_scores(self, chat_id: int) -> Iterable[tuple[int, str, int]]:
        with sqlite3.connect(self._db) as conn:
            cur = conn.execute(
                "SELECT userid, name, score FROM scores WHERE chatid = ?", (chat_id,)
            )
            return cur.fetchall()


game_manager = GameManager()
