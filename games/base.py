from __future__ import annotations

import abc
import logging
import weakref
from typing import Any, Generic, TypeVar

from telebot import TeleBot
from telebot.types import BotCommand, Message

G = TypeVar("G", bound="GuessGame")
logger = logging.getLogger("picguessr")


def handle_exception(f):
    import inspect

    def wrapper(*args, **kwargs):
        from .manager import game_manager

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
            game_manager.bot.reply_to(message, "出现了一些问题，请查看服务端日志。")

    return wrapper


class GuessGame(abc.ABC):
    def add_to_bot(self, bot: TeleBot) -> None:
        from .manager import game_manager

        commands = [c.command for c in self.get_my_commands()]
        bot.register_message_handler(
            self.start_game_handler,
            commands=commands,
            chat_types=["supergroup"] if not game_manager.is_debug else None,
        )

    @abc.abstractmethod
    def get_my_commands(self) -> list[BotCommand]:
        pass

    @abc.abstractmethod
    def check_answer(self, message: Message, state: GameState) -> None:
        pass

    @abc.abstractmethod
    def start_game(self, message: Message) -> None:
        pass

    @handle_exception
    def start_game_handler(self, message: Message) -> None:
        from .manager import game_manager

        state = game_manager.get_state(message.chat.id)
        if state:
            game_manager.bot.reply_to(message, "当前已有正在进行的游戏。")
            return
        prepare = game_manager.bot.reply_to(message, "正在准备游戏，请稍候...")
        try:
            self.start_game(message)
        except Exception:
            game_manager.clear_state(message.chat.id)
            raise
        else:
            game_manager.bot.delete_message(prepare.chat.id, prepare.message_id)


class GameState(Generic[G]):
    def __init__(self, game: G, state: dict[str, Any]) -> None:
        self.game: G = weakref.proxy(game)
        self.state = state
