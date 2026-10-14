"""Калибровщик селекторов для web.max.ru.

Запуск:
    python inspector.py          # открыть браузер и посмотреть логин-состояние
    python inspector.py dump     # после входа: вывести примеры DOM чатов и сообщений
    python inspector.py shot     # скриншот текущего состояния (help.png)

Инструкция: открой нужный чат, выполни `dump`, найди в выводе реальные
class-ы для карточек чатов и сообщений и перенеси их в selectors.local.yaml
(пример шаблона — в README.md).
"""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PROFILE = Path(".browser-profile").resolve()


async def main(mode: str):
    PROFILE.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://web.max.ru/", wait_until="domcontentloaded")
        print("Жду, пока залогинишься в MAX (10 минут). После входа нажми Enter здесь...")
        await asyncio.get_event_loop().run_in_executor(None, input)

        if mode == "dump":
            data = await page.evaluate(DUMP_JS)
            out = Path("dom-dump.json")
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            print(f"Сохранено в {out}: элементы списков чатов и сообщений с их классами.")
            print("Теперь подбери селекторы и запиши в selectors.local.yaml (см. README).")
        elif mode == "shot":
            await page.screenshot(path="help.png")
            print("Скриншот: help.png")
        else:
            print("Логин-состояние проверено. Используй: dump | shot")

        input("Enter — закрыть браузер...")
        await ctx.close()


DUMP_JS = """
() => {
  const pick = (el) => ({
    tag: el.tagName.toLowerCase(),
    cls: el.className,
    testid: el.getAttribute('data-testid'),
    text: (el.innerText || '').slice(0, 60),
  });
  const nodes = [...document.querySelectorAll('*')];
  const msgLike = nodes.filter(el =>
    /message|msg|bubble/i.test(el.className + ' ' + (el.getAttribute('data-testid') || ''))
  ).slice(0, 40);
  const chatLike = nodes.filter(el =>
    /chat|dialog|peer|conversation/i.test(el.className + ' ' + (el.getAttribute('data-testid') || ''))
  ).slice(0, 40);
  const inputs = [...document.querySelectorAll('textarea, [contenteditable="true"]')].map(pick);
  return { chats: chatLike.map(pick), messages: msgLike.map(pick), inputs };
}
"""


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "status"))
