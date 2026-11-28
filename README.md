# PI-231 Queue Bot

A Telegram bot and optional Telegram Mini App/PWA for managing student queues for laboratory work, practical assignments, and consultations in the PI-231 group. The application also provides class schedules, substitutions, polls, administrative workflows, and reports.

**Stack:** Python 3.11+, aiogram 3, aiohttp, PostgreSQL (production), SQLite (local development/tests), HTML/CSS/vanilla JavaScript (Mini App), and Node.js 22+ for frontend tests.

> The bot's user interface and bundled group data are primarily in Ukrainian. This README documents the project in English.

## Features

### Students
- Register with a full name through the Telegram bot or Mini App, view active queues, and join at the end or in an available reserved slot.
- Choose subgroup 1 or 2 in the bot. Existing students without a selection receive a one-time prompt after startup; their next private interaction also requires a selection. Change it via «👥 Моя підгрупа» or `/subgroup`. The choice persists across restarts; only КПЗ is split by subgroup: Щуцький (1) and Кондратюк (2), for lessons and consultations. Other subjects, including course work, are shared. Student lists, creation and signup enforce this rule; administrators can manage all queues.
- View their current position, queue participants, and personal entries; leave a queue or mark work as completed.
- Select individual laboratory numbers for supported subjects and set an estimated completion time from **5 minutes up to the sum of their selected labs' maximum durations**.
- Edit selected labs or planned time without losing their position. When multiple labs are selected, mark only the completed ones and keep the remaining work in the same queue.
- Send direct position-swap requests, respond to requests, and indicate a preference to move up or down. Unanswered requests expire after 10 minutes.
- View class schedules, week variants, substitutions, working Saturdays, polls, and personal information.
- Report problems through the bot.

### Administrators
- In a consultation queue, use **«▶️ Запуск консультації»** to start now, after a number of minutes,
  today at a specified time, or on an exact date and time (Kyiv). Choose a duration of 10–240 minutes
  and confirm. Scheduled starts persist across restarts, can be edited or cancelled, and use the same
  attendance and student calling workflow as ordinary classes. Stopping preserves queue positions and work.
- Create and manage lesson or consultation queues; open/close registration, move participants, add students, complete or remove entries, and archive sessions.
- Create a follow-up queue and transfer selected students to chosen positions while retaining session history.
- Manage users, administrator access, subject/teacher data, schedules, restrictions, and bot settings.
- Manage schedule substitutions, including optional AI-assisted extraction from one image or a two-image Telegram album, followed by administrator review before saving.
- Manage working Saturdays and polls, review activity/statistics, and export group or subject reports as PDF.
- Apply temporary or indefinite bans. Authorization is enforced in backend services, not merely by hiding UI controls.

### Telegram Mini App and installable PWA
- Optional web interface with **Home**, **Queues**, **Schedule**, and **More** sections, plus administrator screens where permitted.
- Queue actions, lab selection, partial completions, swaps, schedule viewing, profile, and registration. Polls live only in the bot as native Telegram polls.
- Responsive light/dark interface with reduced-motion and reduced-transparency fallbacks; bundled assets require no frontend CDN.
- Telegram Mini App authentication through validated `initData`. Optional browser/PWA sign-in uses Telegram Login (OIDC authorization code with PKCE).
- Installable PWA manifest and service worker. Browser sign-in requires separate Telegram Login credentials; it is not enabled merely by setting a Mini App URL.

The Mini App uses the **same application service and database** as the Telegram bot. It supplements rather than replaces the bot's existing controls. See [Mini App documentation](docs/MINI_APP.md) for feature details, sign-in configuration, and limitations.

## How queues work

A queue session belongs to a subject, teacher, and queue type. Its lifecycle is **open → closed → archived**. Only one unfinished session is allowed for a given subject/teacher/type combination. Active queue positions are maintained consistently; completed, withdrawn, removed, and transferred records remain available in history.

For subjects with a lab catalog, students choose lab numbers and an individual time estimate before their place is confirmed. The same lab and time selection is available in consultation queues for those subjects. Subjects without a lab catalog skip these fields. A single selected lab can be completed immediately; with multiple labs, students can report partial completion and retain their place for the remainder. Old or stale action buttons must not modify a newer entry.

«Видалити чергу» in Telegram and the Mini App permanently deletes the selected queue, its entries, swap requests, and attendance data after confirmation. It preserves other queues, student profiles, and grades in the independent journal.

Saved subject pair counts decrease at the scheduled start of each effective lesson (Kyiv time), taking substitutions, cancellations, and working Saturdays into account. The count is recomputed from the saved baseline, so refreshing or restarting cannot subtract a lesson twice. A manual count reset describes the remaining lessons from the moment it is saved.

When a saved subject count reaches zero, that subject is omitted from the current and future effective timetable, including Mini App views, daily schedules and automatic attendance starts. Its final ongoing lesson stays visible until it ends. Historical days and the editable source timetable are preserved so the count remains stable; setting a positive remaining count restores upcoming lessons. Subjects without a saved count stay visible.

Admin actions are logged. Queue updates, swaps, transfers, and archival are handled in database transactions. Swap requests expire after **10 minutes** if unanswered.

## Laboratory catalog

The default catalog is [`data/lab_catalog.json`](data/lab_catalog.json). It can be overridden with `LAB_CATALOG_PATH`.

| Subject | Lab numbers | Maximum time per lab |
| --- | --- | --- |
| Internet Programming | 1–7 | 40 min |
| Human–Computer Interaction | 1–10 | 15 min |
| Machine Learning | 1–7 | 30 min |
| Software Architecture and Design | 1–3 | 20 min |
| Software Construction | 1–11 | 25, 10, 10, 15, 10, 15, 20, 15, 15, 15, 15 min |

Some labs have titles in the catalog; untitled ones are identified by number. These are **per-lab maximums**, not an automatically shared classroom time budget.

Administrators can open **«Предмети та викладачі → 🧪 Лабораторні»**, select a subject, and add or name several works in one message:

```text
1. Topic of the first lab
2. Topic of the second lab
3 | Topic with a custom time limit | 30
```

The bot previews the batch before confirmation. Existing numbers keep their time limits unless a new limit is supplied; new works default to 20 minutes. A subject supports up to 20 works with unique numbers 1–100 and titles up to 100 characters. Other works and existing student entries are preserved. Administrator changes are stored in the database, appear immediately in the Telegram picker and Mini App, and survive deployments. Editing the fallback JSON file still requires an application restart; loading it never creates subjects or queues.

## Project structure

```text
bot.py                       Hosting-compatible application entry point
queue_bot/
  __main__.py                Alternative module entry point
  application.py             Application lifecycle, Telegram polling, background tasks
  config.py                  Environment configuration and validation
  check_config.py            Offline configuration check
  routing.py                 aiogram router/middleware registration
  web.py                     HTTP health endpoints and Mini App static assets
  handlers/
    admin/                   Administrative Telegram UI and workflows
    ...                      Student, schedule, polls, substitutions, bans, etc.
  services/                  Business logic, authorization, reports, recognition
  storage/database.py        PostgreSQL/SQLite adapters, schemas, migrations
  mini_app/
    api.py                   Mini App HTTP API
    student_api.py           Student actions
    admin_api.py             Administrative actions
    browser_auth.py          Optional browser login and sessions
    static/                  HTML, JS, CSS, icons, manifest, service worker
data/
  subjects.json              Default subjects and teachers
  schedule.json              Default timetable
  lab_catalog.json           Laboratory numbers and time limits
docs/
  CONFIGURATION.md           Full environment/deployment guidance
  MINI_APP.md                Mini App, PWA, and Telegram Login documentation
tests/                       Python and Node.js tests
scripts/                     Auxiliary UI checks and PWA icon tooling
.github/workflows/tests.yml  CI checks
CONTRIBUTING.md              Branching, PRs, and release process
Procfile                     Web process: python bot.py
```

## Requirements

- **Python 3.11 or newer** and `pip`.
- A Telegram bot token from [BotFather](https://t.me/BotFather) and the Telegram numeric ID of the owner.
- A database connection string. **PostgreSQL is the intended production database**; the storage layer also supports a local SQLite file for development/tests.
- **Node.js 22+** only if running JavaScript tests; Node.js is not required to serve the Mini App.
- Optional: a public HTTPS URL for the Mini App and a supported recognition API key for image-based schedule substitutions.

## Local setup

1. Clone the repository and install Python dependencies:

   ```bash
   git clone https://github.com/mievvchuk/pi231-queue-bot.git
   cd pi231-queue-bot
   python -m venv .venv
   ```

   Activate the virtual environment on macOS/Linux:

   ```bash
   source .venv/bin/activate
   ```

   Or on Windows PowerShell:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   Install dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and provide at least the following values:

   ```dotenv
   BOT_TOKEN=your_telegram_bot_token
   OWNER_ID=your_numeric_telegram_id
   DATABASE_URL=postgresql://user:password@host:5432/database
   ```

   For a local SQLite development database, `DATABASE_URL` may instead be set to a local file path (for example `local-dev.db`). Never point development or test scripts at the live production database.

3. Validate configuration and start the service:

   ```bash
   python -m queue_bot.check_config
   python bot.py
   ```

The process runs Telegram long polling and an aiohttp web server. Visit `http://localhost:8080/health` for a basic healthcheck when using the default port. You can also start the package with `python -m queue_bot`.

**Important:** Local `.env` is ignored by Git, and actual process environment variables take precedence. Configuration validation does not contact Telegram or the database, but startup does.

## Environment variables

See [`.env.example`](.env.example) and [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for the authoritative template, validation requirements, and deployment notes.

| Variable | Purpose |
| --- | --- |
| `BOT_TOKEN` | **Required.** Telegram bot API token |
| `DATABASE_URL` | **Required.** PostgreSQL URL for production or SQLite path for local use |
| `OWNER_ID` | **Required.** Numeric Telegram ID of the owner |
| `INITIAL_ADMIN_IDS` | Comma-separated initial admins, used only if the admin table is empty |
| `GROUP_CHAT_ID` | Optional Telegram group chat ID; saved database settings take precedence |
| `STUDENT_GROUP` | Group name (default: PI-231 in Ukrainian) |
| `TZ` | IANA time zone (default: `Europe/Kyiv`) |
| `NOTIFICATION_TIME` | Schedule notification time, `HH:MM` (default: `17:00`) |
| `SUBJECTS_PATH`, `SCHEDULE_PATH` | Optional subject catalog file and one-time timetable import source |
| `LAB_CATALOG_PATH` | Optional lab catalog JSON file |
| `MINI_APP_URL` | Optional public HTTPS URL ending in `/app/` to expose Mini App launch controls |
| `TELEGRAM_LOGIN_CLIENT_ID`, `TELEGRAM_LOGIN_CLIENT_SECRET` | Optional Telegram Login credentials for browser/PWA access; both required together with `MINI_APP_URL` |
| `RECOGNITION_PROVIDER` | `disabled`, `auto`, `groq`, `openai`, or `nvidia`. With no API key, `auto` disables photo recognition. |
| `RECOGNITION_API_KEY` | Key for optional image recognition; `OPENAI_API_KEY` is supported as a fallback |
| `GROQ_MODEL`, `OPENAI_MODEL` | Recognition model selection |
| `GROQ_API_URL`, `OPENAI_API_URL` | Recognition API endpoints |
| `RECOGNITION_TIMEOUT_SECONDS` | Recognition timeout (default: 120 seconds for NVIDIA, 45 otherwise) |
| `HTTP_HOST`, `PORT` | Web server binding (defaults: `0.0.0.0`, `8080`) |
| `LOG_LEVEL` | Logging level (default: `INFO`) |
| `POLLING_RETRY_SECONDS` | Telegram polling retry delay |
| `POLLS_WATCH_INTERVAL` | Poll watcher interval |
| `SCHEDULE_CHECK_INTERVAL` | Schedule notifier check interval |
| `ENV_FILE` | Alternate environment file path, set before launching |
| `TEST_DATABASE_URL` | **Test-only** separate PostgreSQL database for integration tests |

Recognition is optional. In `auto` mode, the service selects Groq for `gsk_`-prefixed keys and OpenAI otherwise. Without a configured public Mini App URL, the existing Telegram bot controls remain usable.

## Mini App and deployment

The same aiohttp service serves:

- `/` and `/health` — healthcheck.
- `/app/` — Mini App and PWA frontend.
- `/api/miniapp/` — authenticated API endpoints.
- `/auth/telegram/...` — optional browser login endpoints.

For a deployment on Render or another compatible host:

1. Configure a Python web service with the command `python bot.py` (the included `Procfile` uses this command).
2. Set `BOT_TOKEN`, `DATABASE_URL`, and `OWNER_ID` in the host's environment.
3. Reuse the **existing production database connection** when updating a deployed instance, to preserve queues, users, and history. Back up the database before schema changes.
4. If enabling the Mini App, set `MINI_APP_URL=https://your-service.example/app/` using the service's actual HTTPS domain.
5. If enabling standalone browser/PWA sign-in, configure the Telegram Login Widget client ID and secret and the callback URL `https://your-service.example/auth/telegram/callback` in Telegram's configuration.

The Mini App uses the same deployment, not a second backend. The app validates Telegram authorization on the server and checks access rights for each action. For details, see [`docs/MINI_APP.md`](docs/MINI_APP.md).

## Testing

Run the full Python suite:

```bash
python -m pytest -q
```

Run the Node.js frontend tests:

```bash
node --test tests/mini_app_frontend.test.cjs tests/mini_app_features.test.cjs
```

Additional browser/PWA and layout checks are available in `tests/` and `scripts/`. Most Python tests run against temporary SQLite databases and do **not** require a production bot token. PostgreSQL integration tests require a separately configured `TEST_DATABASE_URL`; never reuse `DATABASE_URL` from production.

GitHub Actions runs the Python suite with a PostgreSQL 16 service, runs the two Node.js suites, and checks Python compilation. The workflow is triggered by pull requests and pushes to `master` or `develop`, with newer runs replacing superseded runs. The existence of CI configuration does not guarantee that hosted runners are available; check the repository's Actions page for runtime or account-level startup failures.

## Data persistence and operational notes

- PostgreSQL is used for deployed persistent data; the storage layer includes SQLite support for local use and tests.
- Database migrations are additive and retain existing user, queue, and historical records. SQLite migrations use `PRAGMA user_version`; PostgreSQL has its own migration path.
- The bot uses in-memory FSM dialogue state. **Unfinished multi-step conversations are lost on process restart**, while persisted database records remain.
- Pending two-photo Telegram album collection must be restarted after a process restart.
- The bot imports the legacy timetable into the database on first use. Admins can then edit individual pairs with `/edit_schedule` in a private chat with the bot.
- Browser/PWA sessions last seven days from sign-in and are stored in the database (only a hash of the cookie token). They survive application restarts and deployments when the same persistent database is used. Logout revokes the session; clearing browser data or switching browsers requires another sign-in. Login attempts in progress still expire after ten minutes or a restart.
- Keep bot tokens, database credentials, recognition keys, and Telegram Login secrets out of commits and PR descriptions.

## Contributing

The repository follows Gitflow:

- `master`: stable deployment/releases.
- `develop`: integration branch.
- `feature/*` and `bugfix/*`: changes based on `develop`, merged through pull requests.
- `release/*`: release preparation.
- `hotfix/*`: urgent fixes branched from `master`.

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) for PR conventions, release checks, backup requirements, and access restrictions. Branch protections and deployment automation are not implied by the documentation; configure them separately in GitHub and the hosting platform.


## Автоматичне отримання фото замін

Бот перевіряє публічну сторінку https://t.me/s/replacements_xpk кожні 5 хвилин.
Додавати бота в канал або авторизувати особистий Telegram-акаунт не потрібно.
Альбом обробляється цілком. При першому запуску береться лише останній допис
із фото, потім — нові видимі дописи. Редагування старих дописів не відстежуються;
після тривалої зупинки дописи за межами публічної сторінки можуть бути пропущені.

Потрібні чинні `BOT_TOKEN`, `DATABASE_URL`, `RECOGNITION_API_KEY` та `OWNER_ID`.
Власник має бути адміністратором бота й попередньо натиснути `/start` у приватному чаті.
Чернетки та позиція перевірки зберігаються в `bot_state` і переживають перезапуск.
Результат із посиланням на оригінал надходить власнику з кнопками підтвердження
та відхилення. До підтвердження розклад не змінюється й студенти не отримують повідомлень.
Якщо повністю прочитана таблиця не містить замін, бот пропонує підтвердити
«На ДД.ММ.РРРР замін немає для ПІ-231». Після підтвердження видаляються попередні заміни
за цю дату й повідомлення надсилається студентам. Помилки завантаження та
нечіткі фото не вважаються відсутністю замін; бот повторює спробу.

Налаштування: `REPLACEMENTS_CHANNEL=replacements_xpk`, `CHANNEL_POLL_SECONDS=60`
(мінімум 60 секунд). Порожній `REPLACEMENTS_CHANNEL` вимикає перевірку.
Без ключа розпізнавання перевірка не запускається. Вебсторінка Telegram не є
стабільним API: зміна розмітки може потребувати оновлення парсера.

### Виправлення автоматичних замін

Власник може натиснути «✏️ Редагувати» до підтвердження або після нього,
а також відкрити редагування через «Активні заміни». Повідомлення для редагування
містить дату та рядки `номер пари | предмет | викладач | аудиторія`.
Для відсутності замін після дати потрібно написати `немає`.
Редагування зберігає окрему чернетку; чинний розклад змінюється лише після
підтвердження виправлень. Студенти отримують повідомлення з позначкою виправлення.
Підтверджена відсутність замін відображається на конкретну дату в Telegram
і розкладі мініапки. Кнопку ручного додавання фото прибрано з адмінського меню.

### Електронний журнал у Telegram та Мініапці

У приватному чаті кнопка «Електронний журнал» або команда `/journal` відкриває предмети, атестації та редагування оцінок. Адміністратор може відкрити журнал іншого студента.

Студентська навігація Мініапки містить чотири основні розділи: «Мої оцінки», «Черги»,
«Розклад», «Профіль». Опитування, обміни, допомога та додаткові налаштування
доступні через профіль. Після входу студент потрапляє на огляд власних оцінок.
Вісім предметів показані картками; курсова з КПЗ доступна всередині КПЗ.
На телефоні роботи відображаються компактним списком, на ПК поруч є навігація
за предметами. Оцінка вводиться в картці роботи та зберігається окремою кнопкою;
порожня оцінка позначена «Не внесено». Предмет і період зберігаються в адресі сторінки.
Особистий інтерфейс показує лише записи поточного студента. Серверні перевірки
власника оцінки та версії запису збережено. Адміністративний журнал групи
відкривається окремою кнопкою в режимі адміністратора.

Адміністратор після входу автоматично відкриває адмінський режим.
На телефоні перемикач режиму доступний у верхній панелі та профілі;
«Журнал групи» є окремим пунктом навігації. На вузькому екрані відкривається картка вибраного студента з перемикачем атестацій. Повний список групи доступний у режимі «Таблиця». Вибір предмета зберігається в адресі сторінки. Перемикання
в особистий режим не змінює серверних прав облікового запису.

### Пауза присутності під час тривоги

Додайте `ALERTS_API_TOKEN` з alerts.in.ua у секрети сервера або `.env` і перезапустіть бота. Без токена функція вимкнена. Ключ не додавайте в Git.

Бот перевіряє всі активні загрози кожні 15 секунд лише для локації «Хмельницький район» (тип `raion`). Враховуються всі типи загроз та рівні, включно з жовтим і червоним. Записи для всієї області, інших районів чи окремих міст не враховуються. Очікування після відбою починається лише після зникнення всіх активних загроз району. Під час тривоги та 5 хвилин після підтвердженого відбою опитування присутності, виклики й нагадування призупинені, час на відповідь зберігається. Повторна тривога скидає очікування. Закінчення пари не переноситься. Стан паузи зберігається у БД. При недоступності API бот відкладає запити до відновлення перевірки та ще на 5 хвилин. Після першого підключення також діє 5-хвилинне очікування. Студентам повідомлення про тривоги не надсилаються.

### Оголошення над таблицею та моніторинг замін

Вказівки на кшталт «4-та пара за розкладом 1-ї пари четверга (знаменник)» підставляють предмет, викладача й аудиторію зі збереженого розкладу. Підтримуються оголошення, розбиті на рядки. Неоднозначна вказівка потребує ручної перевірки. Примітка зберігається разом із заміною та відображається в актуальному розкладі. Старі записи не видаляються під час додавання поля примітки.

У меню адміністратора «Розклад і заміни» відображається моніторинг каналу: остання спроба, успішна перевірка, останній допис із фото, помилка та кількість результатів на підтвердження. Якщо розпізнавання вимкнене або канал не задано, це прямо вказано. Після оновлення розпізнавача актуальні видимі дописи зі старими результатами перевіряються повторно й знову потребують підтвердження власника. Це не розсилка про оновлення бота.
