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
SCHEDULE_PATH = os.getenv("SCHEDULE_PATH", "schedule.json")
STUDENT_GROUP = os.getenv("STUDENT_GROUP", "ПІ-231")
NOTIFICATION_TIME = os.getenv("NOTIFICATION_TIME", "07:00")
TIMEZONE = os.getenv("TZ", "Europe/Kyiv")
RECOGNITION_API_KEY = os.getenv("RECOGNITION_API_KEY", os.getenv("OPENAI_API_KEY", ""))
RECOGNITION_PROVIDER = os.getenv("RECOGNITION_PROVIDER", "openai" if RECOGNITION_API_KEY else "disabled")



def get_bot_token() -> str:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Set the BOT_TOKEN environment variable")
    return token


def get_initial_admin_ids() -> list[int]:
    raw = os.getenv("INITIAL_ADMIN_IDS", "5937915430")
    return [int(part) for part in raw.replace(" ", "").split(",") if part.isdigit()]
