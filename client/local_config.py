"""
hexfeed - configurações locais
Persiste idioma, tema e outras preferências em um JSON.
"""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "hexfeed"
CONFIG_FILE = CONFIG_DIR / "config.json"


DEFAULT_SERVER_URL = "http://uryagywp6vchlttekoaui75bidl4ir5tamx6mhk3ojg252lnj33dn2qd.onion"

DEFAULT_CONFIG = {
    "language": "pt-BR",
    "theme": "dark",
    "saved_token": "",
    "saved_username": "",
    "server_url": DEFAULT_SERVER_URL,
}


def _ensure_dir():
    """Garante que o diretório de configuração existe."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """
    Carrega as configurações locais do arquivo JSON.
    Faz merge com DEFAULT_CONFIG para garantir que chaves novas existam.
    """
    _ensure_dir()
    if CONFIG_FILE.exists():
        try:
            # Merge: valores salvos sobrescrevem os defaults
            dados = json.loads(CONFIG_FILE.read_text())
            return {**DEFAULT_CONFIG, **dados}
        except Exception:
            # Arquivo corrompido ou inválido → retorna default
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    """Salva as configurações locais no arquivo JSON."""
    _ensure_dir()
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))
