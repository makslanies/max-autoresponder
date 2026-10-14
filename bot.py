"""Автоответчик от личного аккаунта в web.max.ru (https://web.max.ru).

Запуск:
    pip install -r requirements.txt
    playwright install chromium
    python bot.py            # ключи Cloudflare берутся из .env (см. config.yaml)

Первый запуск — с видимым окном: залогинься (QR или код из SMS).
Профиль сохраняется в user_data_dir, дальше бот стартует уже залогиненным.
"""

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path

import yaml
from playwright.async_api import async_playwright, Page

import dom_selectors as S
from llm import LLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("maxbot")


def load_selectors() -> type:
    """Локальные селекторы имеют приоритет над дефолтными."""
    local = Path("selectors.local.yaml")
    if local.exists():
        data = yaml.safe_load(local.read_text())
        for k, v in data.items():
            setattr(S, k, v)
        log.info("Загружены локальные селекторы: %s", ", ".join(data))
    return S


def load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text())


def load_dotenv(path: str = ".env"):
    """Минимальный загрузчик .env (формат KEY=VALUE, # — комментарий)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


class MaxBot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.llm = LLMClient(cfg["llm"])
        self.fallback = cfg["llm"].get("fallback_reply") or ""
        self.cooldown = cfg.get("cooldown_seconds", 120)
        self.max_len = cfg.get("max_reply_length", 2000)
        self.last_reply_at: dict[str, float] = {}
        self.seen: set[str] = set()          # хэши обработанных входящих
        self.caught_up: set[str] = set()     # список чатов, которые уже открывали
        self.live_chats: set[str] = set()    # чаты, где уже ответили в этой сессии
        self.no_write: set[str] = set()      # каналы и сервисы, куда писать нельзя
        self.defer_until: dict[str, float] = {}
        self.history: dict[str, list[dict]] = {}

    # ---------- утилиты ----------

    @staticmethod
    def _hash(*parts: str) -> str:
        return hashlib.sha1("|".join(parts).encode()).hexdigest()

    def is_allowed(self, title: str) -> bool:
        r = self.cfg["reply_to"]
        mode = r.get("mode", "all")
        if mode == "whitelist":
            return title in (r.get("whitelist") or [])
        if mode == "blacklist":
            return title not in (r.get("blacklist") or [])
        return True

    def in_cooldown(self, chat: str) -> bool:
        return time.time() - self.last_reply_at.get(chat, 0) < self.cooldown

    # ---------- основной цикл ----------

    async def ensure_logged_in(self, page: Page):
        for i in range(360):  # до 6 минут на логин
            for marker in S.LOGGED_IN_MARKERS:
                if await page.locator(marker).count() > 0:
                    return True
            if i and i % 15 == 0:
                log.info("Всё ещё жду вход. Сейчас открыто: %s", await page.title())
            await asyncio.sleep(1)
        raise RuntimeError("Не удалось обнаружить залогиненный интерфейс")

    async def ensure_chat_list_expanded(self, page: Page):
        """Узкая колонка аватарок не содержит имён чатов — растягиваем список."""
        if await page.locator(".aside--compact").count() == 0:
            return
        splitter = page.locator("button[aria-label='Изменить ширину']")
        box = await splitter.bounding_box() if await splitter.count() else None
        if not box:
            log.warning("Список чатов свёрнут, разделитель ширины не найден")
            return
        x = box["x"] + box["width"] / 2
        y = box["y"] + min(80, box["height"] / 2)
        await page.mouse.move(x, y)
        await page.mouse.down()
        await page.mouse.move(x + 320, y, steps=12)
        await page.mouse.up()
        await asyncio.sleep(0.4)
        log.info("Список чатов развёрнут")

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join(text.split())

    async def collect_unread_chats(self, page: Page) -> list[dict]:
        chats = []
        items = page.locator(S.CHAT_ITEM)
        for i in range(await items.count()):
            el = items.nth(i)
            title_el = el.locator(S.CHAT_TITLE).first
            title = self._norm(await title_el.inner_text()) if await title_el.count() else ""
            if not title:
                continue
            unread_el = el.locator(S.CHAT_UNREAD)
            has_unread = await unread_el.count() > 0
            preview_el = el.locator(S.CHAT_PREVIEW).first
            preview = self._norm(await preview_el.inner_text()) if await preview_el.count() else ""
            badge = self._norm(await unread_el.first.inner_text()) if has_unread else ""
            chats.append({
                "title": title,
                "unread": has_unread,
                "preview": preview,
                "badge": badge,
            })
        return chats

    async def current_chat_title(self, page: Page) -> str:
        el = page.locator(S.OPEN_CHAT).first
        if await el.count() == 0:
            return ""
        text = self._norm(await el.inner_text())
        prefix = "Окно чата с "
        return text[len(prefix):] if text.startswith(prefix) else text

    async def open_chat(self, page: Page, title: str) -> bool:
        """Открывает чат по точному имени и ждёт, пока шапка совпадёт."""
        items = page.locator(S.CHAT_ITEM)
        target = None
        for i in range(await items.count()):
            el = items.nth(i)
            title_el = el.locator(S.CHAT_TITLE).first
            if await title_el.count() and self._norm(await title_el.inner_text()) == title:
                target = el
                break
        if target is None:
            return False
        await target.scroll_into_view_if_needed()
        await target.click()
        deadline = time.time() + 8
        while time.time() < deadline:
            if await self.current_chat_title(page) == title:
                # шапка меняется раньше ленты — не читаем пустой чат в момент переключения
                try:
                    await page.locator(S.MESSAGE).first.wait_for(state="attached", timeout=3000)
                except Exception:
                    pass
                return await self.current_chat_title(page) == title
            await asyncio.sleep(0.2)
        return False

    async def read_chat_messages(self, page: Page, chat_title: str) -> list[dict]:
        msgs = []
        for el in await page.locator(S.MESSAGE).all():
            text_el = el.locator(S.MESSAGE_TEXT).first
            if not await text_el.count():
                continue
            text = (await text_el.inner_text()).strip()
            if not text:
                continue
            is_out = await el.locator(S.MESSAGE_OUT).count() > 0 if S.MESSAGE_OUT else False
            is_in = (await el.locator(S.MESSAGE_IN).count() > 0) if S.MESSAGE_IN else not is_out
            msgs.append({"role": "assistant" if is_out else "user",
                         "text": text, "is_in": is_in})
        return msgs

    async def can_write(self, page: Page) -> bool:
        """Писать можно только в диалог. В закрытом канале поля нет, у своего канала — «Пост»."""
        inp = page.locator(S.INPUT).first
        try:
            await inp.wait_for(state="visible", timeout=1500)
        except Exception:
            return False
        placeholder = (
            (await inp.get_attribute("aria-placeholder"))
            or (await inp.get_attribute("placeholder"))
            or ""
        ).strip()
        return placeholder in ("Сообщение", "Комментарий")

    async def send(self, page: Page, text: str, title: str) -> str:
        """ok — отправили, wrong_chat — открыт другой диалог, no_input — писать некуда."""
        if await self.current_chat_title(page) != title:
            return "wrong_chat"
        inp = page.locator(S.INPUT).first
        if await inp.count() == 0:
            return "no_input"
        try:
            await inp.click(timeout=5000)
        except Exception as e:
            if await self.current_chat_title(page) != title:
                return "wrong_chat"
            log.warning("Поле ввода в '%s' недоступно: %s", title, e)
            return "no_input"
        if await self.current_chat_title(page) != title:
            return "wrong_chat"
        # переносы строк внутри ответа LLM — Shift+Enter; Enter отправляет
        lines = text.split("\n")
        for i, line in enumerate(lines):
            await page.keyboard.type(line, delay=5)
            if i < len(lines) - 1:
                await page.keyboard.press("Shift+Enter")
        if await self.current_chat_title(page) != title:
            # черновик попал не в тот диалог — стираем и не отправляем
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            return "wrong_chat"
        await page.keyboard.press("Enter")
        return "ok"

    async def respond(self, page: Page, title: str, msgs: list[dict], last_in: dict, fp: str | None):
        h = self._hash(title, last_in["text"])
        if h in self.seen:
            if fp:
                self.caught_up.add(fp)
            return
        if not await self.can_write(page):
            log.info("В '%s' нельзя писать, пропускаю", title)
            self.no_write.add(title)
            self.seen.add(h)
            if fp:
                self.caught_up.add(fp)
            return

        history = [{"role": m["role"], "content": m["text"]} for m in msgs[-self.llm.max_history:]]
        try:
            answer = await self.llm.reply(history)
        except Exception as e:
            log.warning("Ошибка LLM: %s", e)
            answer = self.fallback
        if not answer:
            return

        answer = answer[: self.max_len]
        if await self.current_chat_title(page) != title:
            log.info("Чат переключили, возвращаюсь в '%s'", title)
            if not await self.open_chat(page, title):
                log.warning("Ответ для '%s' не отправлен: открыт другой чат", title)
                self.defer_until[title] = time.time() + 15
                return
        sent = await self.send(page, answer, title)
        if sent == "no_input":
            log.warning("В чате '%s' нет поля ввода, пропускаю", title)
            self.seen.add(h)
            if fp:
                self.caught_up.add(fp)
            return
        if sent != "ok":
            log.warning("Ответ для '%s' не отправлен: открыт другой чат", title)
            self.defer_until[title] = time.time() + 15
            return
        self.seen.add(h)
        if fp:
            self.caught_up.add(fp)
        self.live_chats.add(title)
        self.last_reply_at[title] = time.time()
        log.info("Ответ в '%s': %s", title, answer[:120])

    async def answer_open_chat(self, page: Page) -> bool:
        """Новое входящее в уже открытом диалоге не получает бейдж — смотрим ленту."""
        title = await self.current_chat_title(page)
        if not title or title not in self.live_chats or not self.is_allowed(title):
            return False
        if time.time() < self.defer_until.get(title, 0):
            return False

        msgs = await self.read_chat_messages(page, title)
        if await self.current_chat_title(page) != title or not msgs:
            return False
        last_in = next((m for m in reversed(msgs) if m["is_in"]), None)
        if not last_in or self._hash(title, last_in["text"]) in self.seen:
            return False

        log.info("Новое сообщение в открытом чате '%s'", title)
        await self.respond(page, title, msgs, last_in, fp=None)
        return True

    async def process_chat(self, page: Page, chat: dict):
        title = chat["title"]
        fp = self._hash(title, chat.get("preview", ""), chat.get("badge", ""))
        if not self.is_allowed(title) or title in self.no_write:
            self.caught_up.add(fp)
            return
        if self.in_cooldown(title) or time.time() < self.defer_until.get(title, 0):
            return

        log.info("Открываю непрочитанный чат '%s'", title)
        if not await self.open_chat(page, title):
            log.warning("Не открылся чат '%s' — ответ не пишу", title)
            self.defer_until[title] = time.time() + 15
            return
        if not await self.can_write(page):
            log.info("В '%s' нельзя писать, пропускаю", title)
            self.no_write.add(title)
            self.caught_up.add(fp)
            return

        msgs = await self.read_chat_messages(page, title)
        if await self.current_chat_title(page) != title:
            log.warning("Пока читал '%s', открылся другой чат — ответ не пишу", title)
            self.defer_until[title] = time.time() + 15
            return
        if not msgs:
            self.caught_up.add(fp)
            return

        last_in = next((m for m in reversed(msgs) if m["is_in"]), None)
        if not last_in:
            self.caught_up.add(fp)
            return
        await self.respond(page, title, msgs, last_in, fp)

    async def run(self):
        cfg = self.cfg
        profile = Path(cfg.get("user_data_dir", ".browser-profile")).resolve()
        profile.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=cfg.get("headless", False),
                channel="chrome",       # если Chrome не установлен — убери строку
                viewport={"width": 1440, "height": 900},
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://web.max.ru/", wait_until="domcontentloaded")
            log.info("Жду входа в аккаунт (при необходимости залогинься)...")
            await self.ensure_logged_in(page)
            await self.ensure_chat_list_expanded(page)
            await page.locator(S.CHAT_ITEM).first.wait_for(timeout=15000)
            log.info("Залогинен. Бот работает. Ctrl+C — остановить.")

            try:
                while True:
                    try:
                        # Сначала уже открытый диалог: новое сообщение там без бейджа.
                        if not await self.answer_open_chat(page):
                            for chat in await self.collect_unread_chats(page):
                                if not chat["unread"]:
                                    continue
                                fp = self._hash(chat["title"], chat["preview"], chat["badge"])
                                if fp in self.caught_up or chat["title"] in self.no_write:
                                    continue
                                await self.process_chat(page, chat)
                                break  # один чат за проход, список после клика меняется
                    except Exception as e:
                        log.warning("Ошибка итерации: %s", e)
                    await asyncio.sleep(cfg.get("poll_interval", 4))
            finally:
                await ctx.close()


def main():
    load_dotenv()
    load_selectors()
    bot = MaxBot(load_config())
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
