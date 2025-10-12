# ruff: noqa: E402

from .core.config_manager import Config
Config.load()

from datetime import datetime
from logging import Formatter
from pytz import timezone

from . import LOGGER, bot_loop
from .core.tg_client import TgClient


async def main():
    from asyncio import gather

    from .core.startup import (
        load_configurations,
        load_settings,
        save_settings,
        update_aria2_options,
        update_nzb_options,
        update_qb_options,
        update_variables,
    )

    # Load all bot settings from MongoDB / env
    await load_settings()

    # ─────────────────────────────────────────────
    # 🔧 Fix: Force integer conversion for all numeric config values
    # This prevents 'str' vs 'int' TypeError after MongoDB reload
    from .helper.ext_utils.bot_utils import safe_int

    int_keys = [
        "BOT_MAX_TASKS", "USER_MAX_TASKS",
        "QUEUE_ALL", "QUEUE_DOWNLOAD", "QUEUE_UPLOAD",
        "TORRENT_LIMIT", "MEGA_LIMIT", "GD_DL_LIMIT",
        "CLONE_LIMIT", "JD_LIMIT", "NZB_LIMIT", "RC_DL_LIMIT",
        "YTDLP_LIMIT", "PLAYLIST_LIMIT", "DIRECT_LIMIT",
        "LEECH_LIMIT", "ARCHIVE_LIMIT", "EXTRACT_LIMIT",
        "STORAGE_LIMIT", "USER_TIME_INTERVAL",
    ]

    for key in int_keys:
        val = getattr(Config, key, None)
        try:
            setattr(Config, key, int(val))
        except Exception:
            try:
                setattr(Config, key, safe_int(val))
            except Exception:
                setattr(Config, key, 0)
    # ─────────────────────────────────────────────

    # Apply timezone fix for log timestamps
    def changetz(*args):
        return datetime.now(timezone(Config.TIMEZONE)).timetuple()
    Formatter.converter = changetz

    # Start the main Telegram clients
    await gather(
        TgClient.start_bot(),
        TgClient.start_user(),
        TgClient.start_helper_bots(),
    )
    await gather(load_configurations(), update_variables())

    from .core.torrent_manager import TorrentManager
    await TorrentManager.initiate()
    await gather(
        update_qb_options(),
        update_aria2_options(),
        update_nzb_options(),
    )

    from .core.jdownloader_booter import jdownloader
    from .helper.ext_utils.files_utils import clean_all
    from .helper.ext_utils.telegraph_helper import telegraph
    from .helper.mirror_leech_utils.rclone_utils.serve import rclone_serve_booter
    from .modules import (
        get_packages_version,
        initiate_search_tools,
        restart_notification,
    )

    await gather(
        save_settings(),
        jdownloader.boot(),
        clean_all(),
        initiate_search_tools(),
        get_packages_version(),
        restart_notification(),
        telegraph.create_account(),
        rclone_serve_booter(),
    )


bot_loop.run_until_complete(main())

from .core.handlers import add_handlers
from .helper.ext_utils.bot_utils import create_help_buttons
from .helper.listeners.aria2_listener import add_aria2_callbacks

add_aria2_callbacks()
create_help_buttons()
add_handlers()

from .core.plugin_manager import get_plugin_manager
from .modules.plugin_manager import register_plugin_commands

plugin_manager = get_plugin_manager()
plugin_manager.bot = TgClient.bot
register_plugin_commands()

from pyrogram.filters import regex
from pyrogram.handlers import CallbackQueryHandler
from .helper.ext_utils.bot_utils import new_task
from .helper.telegram_helper.filters import CustomFilters
from .helper.telegram_helper.message_utils import (
    delete_message,
    edit_message,
    send_message,
)


@new_task
async def restart_sessions_confirm(_, query):
    data = query.data.split()
    message = query.message
    if data[1] == "confirm":
        reply_to = message.reply_to_message
        restart_message = await send_message(reply_to, "Restarting Session(s)...")
        await delete_message(message)
        await TgClient.reload()
        add_handlers()
        TgClient.bot.add_handler(
            CallbackQueryHandler(
                restart_sessions_confirm,
                filters=regex("^sessionrestart") & CustomFilters.sudo,
            )
        )
        await edit_message(restart_message, "Session(s) Restarted Successfully!")
    else:
        await delete_message(message)


TgClient.bot.add_handler(
    CallbackQueryHandler(
        restart_sessions_confirm,
        filters=regex("^sessionrestart") & CustomFilters.sudo,
    )
)

LOGGER.info("WZ Client(s) & Services Started !")
bot_loop.run_forever()
