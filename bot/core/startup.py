from asyncio import create_subprocess_exec, create_subprocess_shell
from importlib import import_module
from os import environ, getenv, path as ospath

from aiofiles import open as aiopen
from aiofiles.os import makedirs, remove, path as aiopath
from aioshutil import rmtree
from sabnzbdapi.exception import APIResponseError

from .. import (
    LOGGER,
    aria2_options,
    auth_chats,
    drives_ids,
    drives_names,
    index_urls,
    shortener_dict,
    var_list,
    user_data,
    excluded_extensions,
    nzb_options,
    qbit_options,
    rss_dict,
    sabnzbd_client,
    sudo_users,
)
from ..helper.ext_utils.db_handler import database
from .config_manager import Config, BinConfig
from .tg_client import TgClient
from .torrent_manager import TorrentManager


# ====================== TYPE FIXER ============================
def _sanitize_types(cfg: dict):
    """Convert MongoDB string values into proper Python types."""
    for key, value in list(cfg.items()):
        if isinstance(value, str):
            v = value.strip().lower()
            if v.isdigit():
                cfg[key] = int(v)
            elif v.replace(".", "", 1).isdigit():
                try:
                    cfg[key] = float(v)
                except Exception:
                    pass
            elif v in ("true", "false"):
                cfg[key] = v == "true"
    return cfg
# ===============================================================


async def update_qb_options():
    if not qbit_options:
        if not TorrentManager.qbittorrent:
            LOGGER.warning("qBittorrent is not initialized. Skipping update.")
            return
        opt = await TorrentManager.qbittorrent.app.preferences()
        qbit_options.update(opt)
        del qbit_options["listen_port"]
        for k in list(qbit_options.keys()):
            if k.startswith("rss"):
                del qbit_options[k]
        qbit_options["web_ui_password"] = "admin"
        await TorrentManager.qbittorrent.app.set_preferences({"web_ui_password": "admin"})
    else:
        await TorrentManager.qbittorrent.app.set_preferences(qbit_options)


async def update_aria2_options():
    if not aria2_options:
        op = await TorrentManager.aria2.getGlobalOption()
        aria2_options.update(op)
    else:
        await TorrentManager.aria2.changeGlobalOption(aria2_options)


async def update_nzb_options():
    if Config.USENET_SERVERS:
        try:
            no = (await sabnzbd_client.get_config())["config"]["misc"]
            nzb_options.update(no)
        except (APIResponseError, Exception) as e:
            LOGGER.error(f"Error in NZB Options: {e}")


async def load_settings():
    if not Config.DATABASE_URL:
        return
    for p in ["thumbnails", "tokens", "rclone"]:
        if await aiopath.exists(p):
            await rmtree(p, ignore_errors=True)
    await database.connect()

    if database.db is not None:
        BOT_ID = Config.BOT_TOKEN.split(":", 1)[0]
        try:
            settings = import_module("config")
            config_file = {
                key: value.strip() if isinstance(value, str) else value
                for key, value in vars(settings).items()
                if not key.startswith("__")
            }
        except ModuleNotFoundError:
            config_file = {}

        config_file.update(
            {
                key: value.strip() if isinstance(value, str) else value
                for key, value in environ.items()
                if key in var_list
            }
        )

        old_config = await database.db.settings.deployConfig.find_one({"_id": BOT_ID}, {"_id": 0})

        # --------------------- FIXED MERGING LOGIC ----------------------
        if old_config and old_config != config_file:
            LOGGER.info("Saving.. Deploy Config imported from Bot")
            await database.db.settings.deployConfig.replace_one({"_id": BOT_ID}, config_file, upsert=True)
            config_dict = (
                await database.db.settings.config.find_one({"_id": BOT_ID}, {"_id": 0}) or {}
            )
            # Merge both configs — keep DB values but fill missing keys from file
            merged_dict = {**config_file, **config_dict}
            _sanitize_types(merged_dict)

            # Ensure numeric defaults are present
            for key in ["BOT_MAX_TASKS", "QUEUE_ALL", "QUEUE_DOWNLOAD", "QUEUE_UPLOAD"]:
                if key not in merged_dict:
                    merged_dict[key] = 0

            Config.load_dict(merged_dict)
            await database.db.settings.config.replace_one({"_id": BOT_ID}, merged_dict, upsert=True)
        else:
            LOGGER.info("Updating.. Saved Config imported from MongoDB")
            config_dict = await database.db.settings.config.find_one({"_id": BOT_ID}, {"_id": 0}) or {}

            # Fill missing numeric defaults if not present
            for key in ["BOT_MAX_TASKS", "QUEUE_ALL", "QUEUE_DOWNLOAD", "QUEUE_UPLOAD"]:
                if key not in config_dict:
                    config_dict[key] = 0

            _sanitize_types(config_dict)
            Config.load_dict(config_dict)
            await database.db.settings.config.replace_one({"_id": BOT_ID}, config_dict, upsert=True)
        # ---------------------------------------------------------------

        # Restore files from DB
        if pf_dict := await database.db.settings.files.find_one({"_id": BOT_ID}, {"_id": 0}):
            for key, value in pf_dict.items():
                if value:
                    file_ = key.replace("__", ".")
                    async with aiopen(file_, "wb+") as f:
                        await f.write(value)

        if a2c_options := await database.db.settings.aria2c.find_one({"_id": BOT_ID}, {"_id": 0}):
            aria2_options.update(a2c_options)

        if not Config.DISABLE_TORRENTS:
            if qbit_opt := await database.db.settings.qbittorrent.find_one({"_id": BOT_ID}, {"_id": 0}):
                qbit_options.update(qbit_opt)

        if nzb_opt := await database.db.settings.nzb.find_one({"_id": BOT_ID}, {"_id": 0}):
            if await aiopath.exists("sabnzbd/SABnzbd.ini.bak"):
                await remove("sabnzbd/SABnzbd.ini.bak")
            ((key, value),) = nzb_opt.items()
            file_ = key.replace("__", ".")
            async with aiopen(f"sabnzbd/{file_}", "wb+") as f:
                await f.write(value)
            LOGGER.info("Loaded.. Sabnzbd Data from MongoDB")

        # Load users data
        if await database.db.users[BOT_ID].find_one():
            rows = database.db.users[BOT_ID].find({})
            async for row in rows:
                uid = row["_id"]
                del row["_id"]
                paths = {
                    "THUMBNAIL": f"thumbnails/{uid}.jpg",
                    "RCLONE_CONFIG": f"rclone/{uid}.conf",
                    "TOKEN_PICKLE": f"tokens/{uid}.pickle",
                    "USER_COOKIE_FILE": f"cookies/{uid}/cookies.txt",
                }

                async def save_file(file_path, content):
                    dir_path = ospath.dirname(file_path)
                    if not await aiopath.exists(dir_path):
                        await makedirs(dir_path)
                    if file_path.startswith("cookies/") and file_path.endswith(".txt"):
                        async with aiopen(file_path, "wb") as f:
                            if isinstance(content, str):
                                content = content.encode("utf-8")
                            await f.write(content)
                    else:
                        async with aiopen(file_path, "wb+") as f:
                            if isinstance(content, str):
                                content = content.encode("utf-8")
                            await f.write(content)

                for key, path in paths.items():
                    if row.get(key):
                        await save_file(path, row[key])
                        row[key] = path
                user_data[uid] = row
            LOGGER.info("Users Data has been imported from MongoDB")

        # Load RSS
        if await database.db.rss[BOT_ID].find_one():
            rows = database.db.rss[BOT_ID].find({})
            async for row in rows:
                user_id = row["_id"]
                del row["_id"]
                rss_dict[user_id] = row
            LOGGER.info("RSS data has been imported from MongoDB")


async def save_settings():
    if database.db is None:
        return
    config_file = Config.get_all()
    await database.db.settings.config.update_one({"_id": TgClient.ID}, {"$set": config_file}, upsert=True)
    if await database.db.settings.aria2c.find_one({"_id": TgClient.ID}) is None:
        await database.db.settings.aria2c.update_one({"_id": TgClient.ID}, {"$set": aria2_options}, upsert=True)
    if await database.db.settings.qbittorrent.find_one({"_id": TgClient.ID}) is None:
        await database.save_qbit_settings()
    if await database.db.settings.nzb.find_one({"_id": TgClient.ID}) is None:
        async with aiopen("sabnzbd/SABnzbd.ini", "rb+") as pf:
            nzb_conf = await pf.read()
        await database.db.settings.nzb.update_one(
            {"_id": TgClient.ID}, {"$set": {"SABnzbd__ini": nzb_conf}}, upsert=True
        )


async def update_variables():
    if (
        Config.LEECH_SPLIT_SIZE > TgClient.MAX_SPLIT_SIZE
        or Config.LEECH_SPLIT_SIZE == 2097152000
        or not Config.LEECH_SPLIT_SIZE
    ):
        Config.LEECH_SPLIT_SIZE = TgClient.MAX_SPLIT_SIZE

    Config.HYBRID_LEECH = bool(Config.HYBRID_LEECH and TgClient.IS_PREMIUM_USER)
    Config.USER_TRANSMISSION = bool(Config.USER_TRANSMISSION and TgClient.IS_PREMIUM_USER)

    if Config.AUTHORIZED_CHATS:
        aid = Config.AUTHORIZED_CHATS.split()
        for id_ in aid:
            chat_id, *thread_ids = id_.split("|")
            chat_id = int(chat_id.strip())
            if thread_ids:
                thread_ids = list(map(lambda x: int(x.strip()), thread_ids))
                auth_chats[chat_id] = thread_ids
            else:
                auth_chats[chat_id] = []

    if Config.SUDO_USERS:
        aid = Config.SUDO_USERS.split()
        for id_ in aid:
            sudo_users.append(int(id_.strip()))

    if Config.EXCLUDED_EXTENSIONS:
        fx = Config.EXCLUDED_EXTENSIONS.split()
        for x in fx:
            x = x.lstrip(".")
            excluded_extensions.append(x.strip().lower())

    if Config.GDRIVE_ID:
        drives_names.append("Main")
        drives_ids.append(Config.GDRIVE_ID)
        index_urls.append(Config.INDEX_URL)


async def load_configurations():
    if not await aiopath.exists(".netrc"):
        async with aiopen(".netrc", "w"):
            pass

    await (
        await create_subprocess_shell(
            f"chmod 600 .netrc && cp .netrc /root/.netrc && chmod +x setpkgs.sh && ./setpkgs.sh {BinConfig.ARIA2_NAME} {BinConfig.SABNZBD_NAME}"
        )
    ).wait()

    PORT = getenv("PORT", "") or Config.BASE_URL_PORT
    if PORT:
        await create_subprocess_shell(
            f"gunicorn -k uvicorn.workers.UvicornWorker -w 1 web.wserver:app --bind 0.0.0.0:{PORT}"
        )
        await create_subprocess_shell("python3 cron_boot.py")

    if await aiopath.exists("cfg.zip"):
        if await aiopath.exists("/JDownloader/cfg"):
            await rmtree("/JDownloader/cfg", ignore_errors=True)
        await (await create_subprocess_exec("7z", "x", "cfg.zip", "-o/JDownloader")).wait()

    if await aiopath.exists("accounts.zip"):
        if await aiopath.exists("accounts"):
            await rmtree("accounts")
        await (
            await create_subprocess_exec("7z", "x", "-o.", "-aoa", "accounts.zip", "accounts/*.json")
        ).wait()
        await (await create_subprocess_exec("chmod", "-R", "777", "accounts")).wait()
        await remove("accounts.zip")

    if not await aiopath.exists("accounts"):
        Config.USE_SERVICE_ACCOUNTS = False

    await TorrentManager.initiate()

    if Config.DISABLE_TORRENTS:
        LOGGER.info("Torrents are disabled. Skipping qBittorrent initialization.")
    else:
        try:
            await TorrentManager.qbittorrent.app.set_preferences(qbit_options)
        except Exception as e:
            LOGGER.error(f"Failed to configure qBittorrent: {e}")
        
