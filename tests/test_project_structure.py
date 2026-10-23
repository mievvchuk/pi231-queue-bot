import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_router_registration_order_is_preserved():
    from queue_bot.routing import ordered_routers
    expected = json.loads((ROOT / 'tests/fixtures/router-order.json').read_text(encoding='utf-8'))
    actual = [{event: [h.callback.__name__ for h in observer.handlers] for event, observer in r.observers.items() if observer.handlers} for r in ordered_routers()]
    assert actual == expected


def read_config(tmp_path, values, cwd=None):
    env_path = tmp_path / 'settings.env'
    env_path.write_text(values, encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in {'OWNER_ID', 'INITIAL_ADMIN_IDS', 'RECOGNITION_PROVIDER', 'RECOGNITION_API_KEY', 'OPENAI_API_KEY', 'GROQ_MODEL', 'SUBJECTS_PATH', 'SCHEDULE_PATH', 'PORT'}}
    env.update(ENV_FILE=str(env_path), PYTHONPATH=str(ROOT))
    code = ('import json; from queue_bot import config; '
            'print(json.dumps({"owner": config.OWNER_ID, "model": config.recognition_settings()[1], '
            '"subjects_exist": __import__("pathlib").Path(config.SUBJECTS_PATH).exists(), '
            '"port": config.PORT, "admins": config.get_initial_admin_ids()}))')
    return json.loads(subprocess.check_output([sys.executable, '-c', code], env=env, cwd=cwd or ROOT, text=True))


def test_env_file_configures_owner_model_and_port_independent_of_cwd(tmp_path):
    result = read_config(tmp_path, 'OWNER_ID=100\nINITIAL_ADMIN_IDS=100,200\nRECOGNITION_PROVIDER=groq\nGROQ_MODEL=custom-vision\nPORT=9090\n', cwd=tmp_path)
    assert result == {'owner': 100, 'model': 'custom-vision', 'subjects_exist': True, 'port': 9090, 'admins': [100, 200]}


def test_owner_has_no_personal_fallback(tmp_path):
    result = read_config(tmp_path, '')
    assert result['owner'] == 0
    assert result['admins'] == []


def test_package_entrypoint_imports_without_starting_bot():
    from queue_bot import application, web
    assert callable(application.main)
    assert callable(web.run_web_server)


def test_preflight_checks_environment_without_printing_secrets(tmp_path):
    env_path = tmp_path / 'preflight.env'
    env_path.write_text('OWNER_ID=100\nBOT_TOKEN=offline-secret-token\nDATABASE_URL=postgresql://example.invalid/unreachable\nRECOGNITION_PROVIDER=disabled\n', encoding='utf-8')
    env = dict(os.environ, ENV_FILE=str(env_path))
    for key in ['OWNER_ID', 'BOT_TOKEN', 'DATABASE_URL', 'RECOGNITION_PROVIDER']:
        env.pop(key, None)
    result = subprocess.run([sys.executable, '-m', 'queue_bot.check_config'], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert 'Configuration OK' in result.stdout
    assert 'offline-secret-token' not in result.stdout
    assert 'postgresql://' not in result.stdout
