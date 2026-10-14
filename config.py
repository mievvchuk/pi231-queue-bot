import os


def load_env_file(path: str = ".env") -> None:
    """Читає KEY=VALUE з файлу .env. Змінні середовища мають пріоритет."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env_file()
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_LhRQsAB7i0If@ep-morning-base-b9my7fky-pooler.c-7.eu-central-1.aws.neon.tech/neondb?sslmode=require"
)
SUBJECTS_PATH = os.getenv("SUBJECTS_PATH", "subjects.json")
ALERTS_TOKEN = os.getenv("ALERTS_TOKEN", "e99b53d5fed806f150b79df3a19c8293d73cac28ab2203")



def get_bot_token() -> str:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Set the BOT_TOKEN environment variable")
    return token


def get_initial_admin_ids() -> list[int]:
    raw = os.getenv("INITIAL_ADMIN_IDS", "5937915430")
    return [int(part) for part in raw.replace(" ", "").split(",") if part.isdigit()]
