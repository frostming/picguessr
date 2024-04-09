from __future__ import annotations

import abc
import copy
import json
import logging
import os
import random
import re
import sqlite3
import sys
import textwrap
from collections import Counter
from typing import Any, Iterable, TypedDict

from openai import AzureOpenAI, OpenAI
from telebot import TeleBot
from telebot.formatting import escape_markdown
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

ABSENT = "⬛"
PRESENT = "🟨"
CORRECT = "🟩"
DATA_DIR = os.getenv("DATA_DIR", "data")


class GameState(TypedDict):
    answer: str
    remain_guesses: int
    revealed: list[str]
    min_unrevealed: int
    game: "GuessGame"
    context: dict[str, Any]


class GameManager:
    def __init__(self) -> None:
        self.is_debug = False
        self._states: dict[str, GameState] = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        self._db = os.path.join(DATA_DIR, "game.db")
        self._init_db()

    def set_debug(self, is_debug: bool) -> None:
        self.is_debug = is_debug

    def _init_db(self):
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                textwrap.dedent("""
                CREATE TABLE IF NOT EXISTS scores (
                    userid INTEGER PRIMARY KEY,
                    name TEXT,
                    score INTEGER DEFAULT 0
                )
                """)
            )
            conn.commit()

    def start_game(self, chat_id: int, state: GameState) -> GameState:
        logger.info("Starting a new game in chat %d", chat_id)
        self._states[chat_id] = state
        return state

    def get_state(self, chat_id: int) -> GameState | None:
        return self._states.get(chat_id)

    def clear_state(self, chat_id: int) -> None:
        self._states.pop(chat_id, None)

    def record_win(self, user: User) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scores (userid, name, score) VALUES (?, ?, COALESCE((SELECT score FROM scores WHERE userid = ?), 0) + 1)",
                (user.id, user.full_name, user.id),
            )
            conn.commit()

    def get_scores(self) -> Iterable[tuple[int, str, int]]:
        with sqlite3.connect(self._db) as conn:
            cur = conn.execute("SELECT userid, name, score FROM scores")
            return cur.fetchall()


def evaluate_guess(guess: str, answer: str) -> str:
    result = [""] * len(answer)
    counter = Counter(answer)
    for i, l in enumerate(answer):
        if i >= len(guess):
            result[i] = ABSENT
        elif guess[i] == l:
            result[i] = CORRECT
            counter[l] -= 1
    for i, l in enumerate(guess):
        if i >= len(result) or result[i]:
            continue
        elif counter.get(l, 0) > 0:
            result[i] = PRESENT
            counter[l] -= 1
        else:
            result[i] = ABSENT
    return "".join(result)


game_manager = GameManager()
bot = TeleBot(token=os.environ["BOT_TOKEN"])
me = bot.get_me()


def handle_exception(f):
    import inspect

    def wrapper(*args, **kwargs):
        parameters = inspect.signature(f).parameters
        if "self" in parameters:
            message = args[1]
        else:
            message = args[0]
        try:
            logger.debug(
                "New message from %s chat: %s", message.chat.type, message.chat.id
            )
            return f(*args, **kwargs)
        except Exception as e:
            logger.exception(e)
            bot.reply_to(message, "出现了一些问题，请查看服务端日志。")

    return wrapper


class GuessGame(abc.ABC):
    def __init__(self, openai_client: OpenAI) -> None:
        self.openai_client = openai_client
        self.config = copy.deepcopy(DEFAULT_CONFIG)

    def render_answer(self, state: GameState) -> str:
        return state["answer"]

    @abc.abstractmethod
    def add_to_bot(self, bot: TeleBot) -> None:
        pass

    @abc.abstractmethod
    def get_my_commands(self) -> list[BotCommand]:
        pass

    @abc.abstractmethod
    def check_answer(self, guess: str, state: GameState) -> tuple[bool, str]:
        pass


class GuessIdiom(GuessGame):
    IDIOM_DATABASE_URL = (
        "https://cdn.jsdelivr.net/gh/cheeaun/chengyu-wordle/data/THUOCL_chengyu.txt"
    )
    IDIOM_FILE = os.path.join(DATA_DIR, "idioms.txt")

    def __init__(self, openai_client: OpenAI) -> None:
        super().__init__(openai_client)
        self.idioms = self._load_idioms()
        self.config["min_unrevealed"] = 1

    def check_answer(self, guess: str, state: GameState) -> tuple[bool, str]:
        return guess == state["answer"], evaluate_guess(guess, state["answer"])

    @handle_exception
    def start_game(self, message: Message) -> None:
        game_state = game_manager.get_state(message.chat.id)
        if game_state:
            # TODO: per chat state
            bot.reply_to(message, "已经有一个游戏正在进行中")
            return
        idiom = random.choice(self.idioms)
        game_state = game_manager.start_game(
            message.chat.id,
            {
                "answer": idiom,
                "remain_guesses": self.config["max_guesses"],
                "revealed": [""] * len(idiom),
                "min_unrevealed": self.config["min_unrevealed"],
                "game": self,
                "context": {},
            },
        )
        prepare = bot.reply_to(message, "正在准备游戏，请稍等...")
        try:
            image_url = self.generate_image(idiom)
            bot.send_photo(
                message.chat.id,
                image_url,
                caption=f"猜猜这是什么成语？你有 {game_state['remain_guesses']} 次机会。",
            )
        except Exception as e:
            game_manager.clear_state(message.chat.id)
            if "content_policy_violation" in str(e):
                bot.reply_to(
                    message, "生成图片失败，可能是因为内容不符合政策，请重试。"
                )
            else:
                raise
        else:
            bot.delete_message(prepare.chat.id, prepare.message_id)

    def _load_idioms(self) -> list[str]:
        if not os.path.exists(self.IDIOM_FILE):
            import httpx

            logger.info("Downloading idioms database from THUOCL...")
            with httpx.Client() as client:
                with client.stream("GET", self.IDIOM_DATABASE_URL) as response:
                    response.raise_for_status()
                    with open(self.IDIOM_FILE, "wb") as f:
                        for chunk in response.iter_bytes(8192):
                            f.write(chunk)

        with open(self.IDIOM_FILE) as f:
            return [line.split()[0] for line in f if line.strip()]

    def make_image_prompt(self, word: str) -> str:
        prompt = f"Explain the chinese idiom {word} to plain text"
        response = self.openai_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            **self.config["chat"],
        )
        logger.debug(
            "Image prompt for %s: %s", word, response.choices[0].message.content
        )
        return response.choices[0].message.content

    def generate_image(self, word: str) -> str:
        result = self.openai_client.images.generate(
            prompt=self.make_image_prompt(word), model=self.config["model"], n=1
        )
        if not result.data or not result.data[0].url:
            raise RuntimeError("Failed to generate image")
        return result.data[0].url

    def add_to_bot(self, bot: TeleBot) -> None:
        bot.register_message_handler(
            self.start_game,
            commands=["guess"],
            chat_types=["supergroup"] if not game_manager.is_debug else None,
        )

    def get_my_commands(self) -> list[BotCommand]:
        return [BotCommand("guess", "开始猜成语")]


class GuessPoem(GuessGame):
    POEM_URL = "https://gist.githubusercontent.com/frostming/a7e46994c40a348808a9b3fc28297e2e/raw/gushiwen.json"
    POEM_FILE = os.path.join(DATA_DIR, "gushiwen.json")
    PUNCTUATION = "，。！？,.!?；;"

    def __init__(self, openai_client: OpenAI) -> None:
        super().__init__(openai_client)
        self.poems, self.sep = self._load_poems()
        self.total = len(self.poems)
        self.config["min_unrevealed"] = 5

    def render_answer(self, state: GameState) -> str:
        return f'{state["answer"]}\n出自[{state["context"]["origin"]}]({state["context"]["url"]})'

    def _load_poems(self) -> tuple[list[dict], int]:
        if not os.path.exists(self.POEM_FILE):
            import httpx

            logger.info("Downloading poems database from Gist...")
            with httpx.Client() as client:
                with client.stream("GET", self.POEM_URL) as response:
                    response.raise_for_status()
                    with open(self.POEM_FILE, "wb") as f:
                        for chunk in response.iter_bytes(8192):
                            f.write(chunk)

        with open(self.POEM_FILE) as f:
            data = json.load(f)
            return data["data"], data["sep"]

    def normalize(self, text: str) -> str:
        return re.sub(rf"[{self.PUNCTUATION}\s]\s*", " ", text).strip()

    def check_answer(self, guess: str, state: GameState) -> tuple[bool, str]:
        guess = self.normalize(guess)
        golden = self.normalize(state["answer"])
        check = list(evaluate_guess(guess, golden))
        for i, c in enumerate(state["answer"]):
            if c in self.PUNCTUATION:
                if i < len(check):
                    check[i] = c
                else:
                    check.append(c)
        return guess == golden, "".join(check)

    @handle_exception
    def start_game(self, message: Message) -> None:
        game_state = game_manager.get_state(message.chat.id)
        if game_state:
            bot.reply_to(message, "已经有一个游戏正在进行中")
            return
        prepare = bot.reply_to(message, "正在准备游戏，请稍等...")
        if "hard" in message.text.lower():
            rang = (self.sep, self.total)
        else:
            rang = (0, self.sep)
        idx = random.randrange(*rang)
        logger.debug("Selecting poem %d", idx)
        poem = self.poems[idx]
        line = poem["sentence"]
        game_state = game_manager.start_game(
            message.chat.id,
            {
                "answer": line,
                "remain_guesses": self.config["max_guesses"],
                "revealed": ["" if c not in self.PUNCTUATION else c for c in line],
                "min_unrevealed": self.config["min_unrevealed"],
                "game": self,
                "context": poem,
            },
        )
        try:
            image_url = self.generate_image(line)
            bot.send_photo(
                message.chat.id,
                image_url,
                caption=f"猜猜这是哪句古诗？你有 {game_state['remain_guesses']} 次机会。",
            )
        except Exception:
            game_manager.clear_state(message.chat.id)
            raise
        else:
            bot.delete_message(prepare.chat.id, prepare.message_id)

    def make_image_prompt(self, sentence: str) -> str:
        prompt = f"Describe this sentence from chinese poem in plain text, it should be fit as a Dall-E image generate prompt: {sentence}"
        response = self.openai_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            **self.config["chat"],
        )
        logger.debug(
            "Image prompt for %s: %s", sentence, response.choices[0].message.content
        )
        return response.choices[0].message.content

    def generate_image(self, sentence: str) -> str:
        result = self.openai_client.images.generate(
            prompt=self.make_image_prompt(sentence) + " in Chinese comic style",
            model=self.config["model"],
            n=1,
        )
        if not result.data or not result.data[0].url:
            raise RuntimeError("Failed to generate image")
        return result.data[0].url

    def add_to_bot(self, bot: TeleBot) -> None:
        bot.register_message_handler(
            self.start_game,
            commands=["guess_p"],
            chat_types=["supergroup"] if not game_manager.is_debug else None,
        )

    def get_my_commands(self) -> list[BotCommand]:
        return [BotCommand("guess_p", "开始猜古诗")]


@handle_exception
def show_score(message: Message):
    board = sorted(game_manager.get_scores(), key=lambda x: x[-1], reverse=True)
    if not board:
        bot.reply_to(message, "暂无记录")
        return

    scores = "\n".join(f"{escape_markdown(name)}: {score}" for _, name, score in board)
    bot.reply_to(message, f"当前排行榜：\n{scores}", parse_mode="MarkdownV2")


@handle_exception
def check_guess(message: Message):
    game_state = game_manager.get_state(message.chat.id)
    if not game_state:
        bot.reply_to(message, "没有游戏正在进行中, 请使用 /guess 或 /guess_p 开始游戏")
        return

    if message.text == "提示":
        to_reveal = [i for i, c in enumerate(game_state["revealed"]) if not c]
        if len(to_reveal) <= game_state["min_unrevealed"]:
            bot.reply_to(message, "已经没有更多提示了")
        else:
            pos = random.choice(to_reveal)
            revealed = game_state["answer"][pos]
            game_state["revealed"][pos] = revealed
            bot.reply_to(message, "".join(c or ABSENT for c in game_state["revealed"]))
        return

    answer = game_state["game"].render_answer(game_state)
    if message.text == "答案":
        bot.reply_to(message, f"答案是 {answer}", parse_mode="MarkdownV2")
        game_manager.clear_state(message.chat.id)
        return

    success, check = game_state["game"].check_answer(message.text, game_state)

    if success:
        bot.reply_to(
            message,
            f"{check}\n太棒了，你是怎么知道的？{answer}",
            parse_mode="MarkdownV2",
        )
        game_manager.record_win(message.from_user)
        game_manager.clear_state(message.chat.id)
    else:
        game_state["remain_guesses"] -= 1
        if game_state["remain_guesses"]:
            bot.reply_to(
                message,
                f"{check}\n猜错啦！还剩 {game_state['remain_guesses']} 次机会。",
                parse_mode="MarkdownV2",
            )
        else:
            bot.reply_to(
                message,
                f"{check}\n没猜到吧，答案是 {answer}。",
                parse_mode="MarkdownV2",
            )
            game_manager.clear_state(message.chat.id)


def setup_logger(is_debug: bool):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if is_debug else logging.INFO)


def main():
    is_debug = "-d" in sys.argv
    setup_logger(is_debug)
    game_manager.set_debug(is_debug)

    bot.register_message_handler(
        show_score,
        commands=["score"],
        chat_types=["supergroup"] if not is_debug else None,
    )
    bot.register_message_handler(
        check_guess,
        func=lambda message: message.reply_to_message is not None
        and message.reply_to_message.from_user.id == me.id,
        chat_types=["supergroup"] if not is_debug else None,
    )
    openai_client = AzureOpenAI(  # TODO: suppoprt vanilla OpenAI
        api_version="2024-02-01",
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
    )
    my_commands: list[BotCommand] = [BotCommand("score", "查看排行榜")]
    for game in [GuessIdiom(openai_client), GuessPoem(openai_client)]:
        game.add_to_bot(bot)
        my_commands.extend(game.get_my_commands())
    bot.set_my_commands(my_commands)

    logger.info("Bot started.")
    bot.infinity_polling()


if __name__ == "__main__":
    main()
