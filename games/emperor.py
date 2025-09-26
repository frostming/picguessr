import random
from typing import Any

import httpx
from telebot.types import BotCommand, Message

from .base import GameState, GuessGame, logger
from .manager import game_manager


class Emperor(GuessGame):
    TOTAL = 1019  # To be updated periodically

    def __init__(self) -> None:
        super().__init__()
        self._client = httpx.Client(base_url="https://xiaoce.fun/api/v0")

    def get_my_commands(self) -> list[BotCommand]:
        return [BotCommand("guess_emperor", "猜皇帝")]

    def _check_resp(self, resp: dict[str, Any]) -> str | None:
        if not resp.get("success"):
            return resp.get("errorMessage", "请求失败")
        return None

    def start_game(self, message: Message) -> None:
        parts = message.text.split(maxsplit=1) if message.text else []
        query = ""
        if len(parts) > 1:
            query = parts[1].strip()
        if query:
            resp = self._client.get("/scratch/game/get", params={"id": query})
            logger.info("Fetching quiz %s", resp.url)
            resp.raise_for_status()
            quiz = resp.json()
            if err := self._check_resp(quiz):
                game_manager.bot.reply_to(message, err)
                return
        else:
            random_pos = random.randint(0, self.TOTAL - 1)
            page = random_pos // 20 + 1
            offset = random_pos % 20
            resp = self._client.get(
                "/scratch/game/searchV2",
                params={
                    "keyword": "猜皇帝",
                    "order": "new",
                    "pageNum": page,
                    "pageSize": 20,
                },
            )
            resp.raise_for_status()
            logger.debug("Searching quizzes %s", resp.url)
            data = resp.json()
            if err := self._check_resp(data):
                game_manager.bot.reply_to(message, err)
                return
            quiz_id = data["data"][offset]["id"]
            resp = self._client.get("/scratch/game/get", params={"id": quiz_id})
            logger.info("Fetching quiz %s", resp.url)
            resp.raise_for_status()
            quiz = resp.json()
            if err := self._check_resp(quiz):
                game_manager.bot.reply_to(message, err)
                return

        game_manager.start_game(
            message.chat.id, self, {"quiz": quiz, "attempts": [], "next_hint": 1}
        )
        first_hint = quiz["data"]["data"]["data"][0]["hint"]
        total_hints = len(quiz["data"]["data"]["data"])
        game_manager.bot.reply_to(
            message,
            f"**{quiz['data']['name']}**\n\n{quiz['data']['desc']}\n\n提示 1/{total_hints}: {first_hint}",
            parse_mode="MarkdownV2",
        )

    def check_answer(self, message: Message, state: GameState) -> None:
        answers = [
            alt["answer"] for alt in state.state["quiz"]["data"]["data"]["alternatives"]
        ]
        guess = message.text or ""
        attempts = state.state["attempts"]
        if guess.strip() in answers:
            attempts.insert(0, f"✅ {guess.strip()}")
        else:
            attempts.insert(0, f"❌ {guess.strip()}")

        reply_message = "\n".join(attempts)
        all_hints = state.state["quiz"]["data"]["data"]["data"]
        total_hints = len(all_hints)
        if guess.strip() in answers:
            reply_message += "\n\n**回答正确！恭喜你！**"
            reply_message += "\n\n" + "\n".join(
                f"{i}. {a}" for i, a in enumerate(all_hints, start=1)
            )
            game_manager.bot.reply_to(message, reply_message, parse_mode="MarkdownV2")
            game_manager.record_win(message.from_user, message.chat.id)
            game_manager.clear_state(message.chat.id)
        else:
            next_hint = state.state["next_hint"]
            if next_hint >= len(all_hints):
                reply_message += "\n\n**正确答案：" + "/".join(answers) + "**"
                game_manager.bot.reply_to(
                    message, reply_message, parse_mode="MarkdownV2"
                )
                game_manager.clear_state(message.chat.id)
            else:
                reply_message += f"\n\n**提示 {next_hint + 1}/{total_hints}: {all_hints[next_hint]['hint']}**"
                state.state["next_hint"] = next_hint + 1
                game_manager.bot.reply_to(
                    message, reply_message, parse_mode="MarkdownV2"
                )
