import logging
import os
import random
import sys
import textwrap
from collections import Counter

from openai import AzureOpenAI
from telebot import TeleBot
from telebot.types import BotCommand, Message, User

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {  # TODO: allow to change
    "max_guesses": 5,
    "chat": {
        "model": "gpt-35-turbo",
        "temperature": 0.5,
    },
    "model": "dall-e",
}
IDIOM_DATABASE_URL = (
    "https://cdn.jsdelivr.net/gh/cheeaun/chengyu-wordle/data/THUOCL_chengyu.txt"
)
ABSENT = "⬛"
PRESENT = "🟨"
CORRECT = "🟩"
DATA_DIR = os.getenv("DATA_DIR", "data")
IDIOM_FILE = os.path.join(DATA_DIR, "idioms.txt")

openai_client = AzureOpenAI(  # TODO: suppoprt vanilla OpenAI
    api_version="2024-02-01",
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
)


class GameManager:
    def __init__(self) -> None:
        import sqlite3
        import weakref

        self._states = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        self._conn = sqlite3.connect(os.path.join(DATA_DIR, "game.db"))
        self._finalizer = weakref.finalize(self, self.close)
        self._init_db()

    def close(self):
        self._conn.close()

    def _init_db(self):
        self._conn.execute(
            textwrap.dedent("""
            CREATE TABLE IF NOT EXISTS scores (
                username TEXT PRIMARY KEY,
                score INTEGER DEFAULT 0
            )
            """)
        )
        self._conn.commit()

    def start_game(self, chat_id: int, idiom: str):
        self._states[chat_id] = {
            "idiom": idiom,
            "remain_guesses": DEFAULT_CONFIG["max_guesses"],
            "revealed": [],
        }
        return self._states[chat_id]

    def get_state(self, chat_id: int):
        return self._states.get(chat_id)

    def clear_state(self, chat_id: int):
        self._states.pop(chat_id, None)

    def record_win(self, user: User) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO scores (username, score) VALUES (?, COALESCE((SELECT score FROM scores WHERE username = ?), 0) + 1)",
            (user.username, user.username),
        )
        self._conn.commit()

    def get_scores(self) -> dict[str, int]:
        cur = self._conn.execute("SELECT username, score FROM scores")
        return dict(cur.fetchall())


game_manager = GameManager()
IDIOMS: list[dict] = []
app = TeleBot(token=os.environ["BOT_TOKEN"])
me = app.get_me()


def handle_exception(f):
    def wrapper(message, **kwargs):
        try:
            logger.debug(
                "New message from %s chat: %s", message.chat.type, message.chat.id
            )
            return f(message, **kwargs)
        except Exception as e:
            logger.exception(e)
            app.reply_to(message, "出现了一些问题，请查看服务端日志。")

    return wrapper


def check_answer(guess: str, solution: str) -> list[str]:
    result = [ABSENT] * len(solution)
    counter = Counter(solution)
    for i, l in enumerate(solution):
        if guess[i] == l:
            result[i] = CORRECT
            counter[l] -= 1
    for i, l in enumerate(guess):
        if result[i] > -1:
            continue
        elif counter.get(l, 0) > 0:
            result[i] = PRESENT
            counter[l] -= 1
        else:
            result[i] = ABSENT
    return result


@app.message_handler(commands=["guess"], chat_types=["supergroup"])
@handle_exception
def start_game(message: Message):
    game_state = game_manager.get_state(message.chat.id)
    if game_state:
        # TODO: per chat state
        app.reply_to(message, "已经有一个游戏正在进行中")
        return
    prepare = app.reply_to(message, "正在准备游戏，请稍等...")
    idiom = random.choice(IDIOMS)
    game_state = game_manager.start_game(message.chat.id, idiom)
    try:
        image_url = generate_image(idiom)
        app.send_photo(
            message.chat.id,
            image_url,
            caption=f"猜猜这是什么成语？你有 {game_state['remain_guesses']} 次机会。",
        )
    except Exception:
        game_manager.clear_state(message.chat.id)
        raise
    else:
        app.delete_message(prepare.chat.id, prepare.message_id)


@app.message_handler(commands=["score"], chat_types=["supergroup"])
@handle_exception
def show_score(message: Message):
    board = game_manager.get_scores()
    if not board:
        app.reply_to(message, "暂无记录")
        return

    scores = "\n".join(f"{username}: {score}" for username, score in board.items())
    app.reply_to(message, f"当前排行榜：\n{scores}")


@app.message_handler(
    func=lambda message: message.reply_to_message is not None
    and message.reply_to_message.from_user.id == me.id,
    chat_types=["supergroup"],
)
@handle_exception
def check_guess(message: Message):
    game_state = game_manager.get_state(message.chat.id)
    if not game_state:
        app.reply_to(message, "没有游戏正在进行中, 请使用 /guess 开始游戏")
        return

    if message.text == "提示":
        to_reveal = [
            i
            for i in range(len(game_state["idiom"]))
            if i not in game_state["revealed"]
        ]
        if len(to_reveal) <= 1:
            app.reply_to(message, "已经没有更多提示了")
        else:
            reveal = random.choice(to_reveal)
            game_state["revealed"].append(reveal)
            app.reply_to(
                message, f"第 {reveal + 1} 个字是 {game_state['idiom'][reveal]}"
            )
        return

    check = check_answer(message.text, game_state["idiom"])

    if message.text == game_state["idiom"]:
        app.reply_to(message, f"{check}\n太棒了，你是怎么知道的？")
        game_manager.record_win(message.from_user)
        game_manager.clear_state(message.chat.id)
    else:
        game_state["remain_guesses"] -= 1
        if game_state["remain_guesses"]:
            app.reply_to(
                message,
                f"{check}\n猜错啦！还剩 {game_state['remain_guesses']} 次机会。",
            )
        else:
            app.reply_to(
                message,
                f"{check}\n没猜到吧，答案是 {game_state['idiom']}。",
            )
            game_manager.clear_state(message.chat.id)


def _load_idioms():
    if not os.path.exists(IDIOM_FILE):
        import httpx

        logger.info("Downloading idioms database from THUOCL...")
        with httpx.Client() as client:
            with client.stream("GET", IDIOM_DATABASE_URL) as response:
                response.raise_for_status()
                with open(IDIOM_FILE, "wb") as f:
                    for chunk in response.iter_bytes(8192):
                        f.write(chunk)

    with open(IDIOM_FILE) as f:
        IDIOMS[:] = [line.split()[0] for line in f if line.strip()]


def make_image_prompt(word: str) -> str:
    prompt = f"Explain the chinese idiom {word} to plain text"
    response = openai_client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        **DEFAULT_CONFIG["chat"],
    )
    logger.debug("Image prompt for %s: %s", word, response.choices[0].message.content)
    return response.choices[0].message.content


def generate_image(word: str) -> str:
    result = openai_client.images.generate(
        prompt=make_image_prompt(word), model=DEFAULT_CONFIG["model"], n=1
    )
    if not result.data or not result.data[0].url:
        raise RuntimeError("Failed to generate image")
    return result.data[0].url


def setup_logger(is_debug: bool):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if is_debug else logging.INFO)


def main():
    setup_logger("-d" in sys.argv)
    _load_idioms()
    app.set_my_commands(
        [BotCommand("guess", "开始猜词游戏"), BotCommand("score", "查看排行榜")]
    )
    logger.info("Bot started.")
    app.infinity_polling()


if __name__ == "__main__":
    main()
