"""CSS-селекторы элементов web.max.ru (https://web.max.ru).

ВАЖНО: web-версия MAX — неофициальный интерфейс, структура DOM может
меняться. Значения ниже сняты с web.max.ru (Svelte). Хеши svelte-*
не используются: они меняются между сборками. Если бот не находит
чаты/сообщения:

  1. Запусти:  python inspector.py
  2. Открой браузер, зайди в MAX, открой любой чат
  3. Выведи дерево:  python inspector.py dump
     и/или сделай скриншот элемента:  python inspector.py shot
  4. Подставь правильные селекторы в этот файл (или в selectors.local.yaml —
     он имеет приоритет и не попадает в git).

Как это работает:
  - CHAT_ITEM        — карточка чата в левом списке
  - CHAT_TITLE       — имя собеседника внутри карточки
  - CHAT_PREVIEW     — текст последнего сообщения в строке списка
  - CHAT_UNREAD      — счётчик непрочитанных (элемент есть <=> есть непрочитанные)
  - OPEN_CHAT        — имя чата, который реально открыт справа
  - MESSAGE          — сообщение в открытой переписке
  - MESSAGE_TEXT     — текст внутри сообщения
  - MESSAGE_OUT      — доп. класс/маркер исходящего сообщения. Если класса нет,
                       оставь пустую строку "" — тогда исходящими считаем
                       сообщения, отправленные от твоего имени, см. bot.py.
  - MESSAGE_IN       — маркер входящего сообщения (пусто = любое без MESSAGE_OUT)
  - INPUT            — поле ввода (contenteditable div или textarea)
"""

# Классы svelte-* в хеше сборки меняются, поэтому селекторы держатся
# за смысловые классы и aria, снятые с web.max.ru.
CHAT_ITEM = "aside button.cell"
CHAT_TITLE = "h3.title .name .text"
CHAT_PREVIEW = "> span.text"          # превью в строке списка, не имя
CHAT_UNREAD = ".indicators .badgeIcon"
OPEN_CHAT = "main h2.sr-only"         # «Окно чата с {имя}»

MESSAGE = "main .messageWrapper"
MESSAGE_TEXT = ".bubbleContent > span.text"
MESSAGE_OUT = ".message--isOut"
MESSAGE_IN = ""  # входящее = сообщение без MESSAGE_OUT

# Lexical-редактор: атрибут contenteditable пустой, не "true".
INPUT = "[data-testid='composer'] [contenteditable][role='textbox']"

# Элементы, по которым понимаем, что пользователь залогинен.
# Навбар есть и в компактном списке чатов, где карточек ещё не видно.
LOGGED_IN_MARKERS = [
    "nav[aria-label='Папки и профиль']",
    "aside button.cell",
]
