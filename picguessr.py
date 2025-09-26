from __future__ import annotations

import logging
import os
import sys

from telebot.formatting import escape_markdown
from telebot.types import BotCommand, Message

from games import ALL_GAMES
from games.base import handle_exception, logger
from games.manager import game_manager

bot = game_manager.bot
me = bot.get_me()


@handle_exception
def show_score(message: Message):
    board = sorted(
        game_manager.get_scores(message.chat.id), key=lambda x: x[-1], reverse=True
    )
    if not board:
        bot.reply_to(message, "暂无记录")
        return
    current_user = message.from_user.id
    current_record = next(
        (
            (i, score)
            for i, (uid, _, score) in enumerate(board, start=1)
            if uid == current_user
        ),
        None,
    )
    if current_record:
        your_status = f"\n\n你的得分：{current_record[1]}, 排名：{current_record[0]}"
    else:
        your_status = ""
    scores = "\n".join(
        f"{i:2d}\. {escape_markdown(name)}: {score}"
        for i, (_, name, score) in enumerate(board[:20], start=1)
    )
    bot.reply_to(
        message, f"当前排行榜：\n{scores}{your_status}", parse_mode="MarkdownV2"
    )


@handle_exception
def check_guess(message: Message):
    with game_manager.get_state(message.chat.id) as state:
        if not state:
            bot.reply_to(
                message,
                "当前没有正在进行的游戏，发送 /<game_command> 开始一个新游戏吧！",
            )
            return
        state.game.check_answer(message, state)


def setup_logger(is_debug: bool):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if is_debug else logging.INFO)


def main():
    is_debug = "-d" in sys.argv or "DEBUG" in os.environ
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

    my_commands: list[BotCommand] = [BotCommand("score", "查看排行榜")]
    for game in ALL_GAMES:
        game.add_to_bot(bot)
        my_commands.extend(game.get_my_commands())
    bot.set_my_commands(my_commands)

    logger.info("Bot started.")
    bot.infinity_polling()


if __name__ == "__main__":
    main()
