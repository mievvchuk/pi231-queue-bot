"""Validate deployment settings without contacting Telegram or the database."""
import sys


def main():
    try:
        from queue_bot import config
        config.validate_runtime()
    except RuntimeError as error:
        print(f'Configuration error: {error}')
        return 1
    print('Configuration OK. No Telegram or database connections were made.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
