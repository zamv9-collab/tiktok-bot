import asyncio
import logging
import os
import concurrent.futures
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Переменная окружения {key} не задана")
    return value


bot_token = _require("BOT_TOKEN")
CHANNEL_ID = _require("CHANNEL_ID")
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "https://t.me/+lupGFr7wi3ZkZDBi")
PORT = int(os.environ.get("BOT_PORT", 5000))

ydl_opts = {
    'format': 'best',
    'outtmpl': '/tmp/%(title)s.%(ext)s',
    'quiet': True,
}


def download_sync(url):
    ydl_opts_copy = ydl_opts.copy()
    with yt_dlp.YoutubeDL(ydl_opts_copy) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info), info


async def check_subscription(bot, user_id):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'creator', 'administrator']
    except Exception:
        return False


async def upload_to_telegram(bot, chat_id, filepath, caption):
    api_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    try:
        async with aiohttp.ClientSession() as session:
            with open(filepath, 'rb') as f:
                file_data = f.read()
            form = aiohttp.FormData()
            form.add_field('chat_id', str(chat_id))
            form.add_field('caption', caption)
            form.add_field('video', file_data, filename=os.path.basename(filepath))
            async with session.post(api_url, data=form, timeout=aiohttp.ClientTimeout(total=1200)) as resp:
                return (await resp.json()).get('ok', False)
    except Exception:
        return False


async def cmd_start(message, bot):
    name = message.from_user.first_name or "друг"
    text = f"👋 Привет, {name}!\n\nПодпишись на канал: {CHANNEL_LINK}\n\nПотом отправь ссылку на TikTok видео."
    await message.answer(text)


async def download_video(message, bot):
    user_id = message.from_user.id
    if not await check_subscription(bot, user_id):
        await message.answer(f"Подпишись на канал: {CHANNEL_LINK}")
        return
    url = message.text.strip()
    if 'tiktok.com' not in url:
        await message.answer("Только TikTok!")
        return
    status_msg = await message.answer("Скачиваю...")
    try:
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            filename, info = await loop.run_in_executor(pool, download_sync, url)
        file_size = os.path.getsize(filename) / (1024 * 1024)
        await status_msg.edit_text(f"Отправляю {file_size:.1f}MB...")
        success = await upload_to_telegram(bot, message.chat.id, filename, f"Видео: {info.get('title')}")
        if success:
            await status_msg.delete()
        else:
            await status_msg.edit_text("Ошибка отправки")
        os.remove(filename)
    except Exception as e:
        await status_msg.edit_text(f"Ошибка: {str(e)[:50]}")


async def healthcheck(request):
    return web.Response(text="OK")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", healthcheck)
    app.router.add_get("/health", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Keepalive сервер на порту {PORT}")


async def run_bot():
    retry_delay = 5
    while True:
        bot = Bot(token=bot_token)
        dp = Dispatcher()
        dp.message.register(cmd_start, Command("start"))
        dp.message.register(download_video)
        try:
            logger.info("Бот запущен")
            await dp.start_polling(bot)
        except Exception as e:
            logger.error(f"Бот упал: {e}. Перезапуск через {retry_delay}с...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)
        finally:
            await bot.session.close()


async def main():
    await asyncio.gather(
        run_web_server(),
        run_bot(),
    )


asyncio.run(main())
