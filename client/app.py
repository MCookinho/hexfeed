"""
Hexfeed - aplicação TUI principal
Gerencia as telas (screens) e o estado global do cliente.
Suporta internacionalização, temas e configurações locais.
Inicia Tor automaticamente se necessário.
"""

import os
import shutil
import socket
import subprocess
import time
from urllib.parse import urlparse

from textual.app import App
from client.api import HexfeedAPI, ClientState
from client.version import __version__
from client.screens.welcome_screen import WelcomeScreen
from client.screens.login_screen import LoginScreen
from client.screens.register_screen import RegisterScreen
from client.screens.outdated_screen import OutdatedScreen
from client.screens.main_screen import MainScreen
from client.screens.file_picker_screen import FilePickerScreen
from client.screens.profile_view_screen import ProfileViewScreen
from client.screens.settings_screen import SettingsScreen
from client.i18n import LANGUAGES
from client.themes import THEMES
from client.local_config import load_config, save_config, DEFAULT_SERVER_URL


class HexfeedApp(App):
    """
    Aplicação principal do hexfeed.
    Suporta múltiplos idiomas, temas e configurações locais.
    """

    SCREENS = {
        "welcome": WelcomeScreen,
        "login": LoginScreen,
        "register": RegisterScreen,
        "outdated": OutdatedScreen,
        "main": MainScreen,
        "file-picker": FilePickerScreen,
        "profile-view": ProfileViewScreen,
        "settings": SettingsScreen,
    }

    CSS = """
    Screen {
        background: #0a0a0f;
    }

    * {
        scrollbar-color: #7c3aed #1a1a2e;
        scrollbar-size: 1 1;
    }

    Input:focus {
        border: solid #7c3aed;
    }

    Button:focus {
        border: solid #06b6d4;
    }

    Button.-primary {
        background: #7c3aed;
        color: #e2e8f0;
    }

    Button.-primary:hover {
        background: #6d28d9;
    }
    """

    def __init__(self):
        # Carrega configurações antes de super().__init__()
        # (não tocar em self.theme antes do super, é um reactive do Textual)
        self._local_config = load_config()
        self._language = self._local_config.get("language", "pt-BR")
        self._theme_name = self._local_config.get("theme", "dark")
        self._theme_source_keys: list[tuple[str, str]] = []
        self._tor_process: subprocess.Popen | None = None

        super().__init__()

        server_url = self._local_config.get("server_url", "")
        self._ensure_tor(server_url)
        self.client_state = ClientState(
            server_url=server_url,
        )
        self.api = HexfeedAPI(self.client_state)

    @property
    def lang(self) -> dict:
        """Retorna o dicionário de traduções do idioma atual."""
        return LANGUAGES.get(self._language, LANGUAGES["pt-BR"])

    @property
    def language(self) -> str:
        """Nome do idioma atual (pt-BR, en, es)."""
        return self._language

    @property
    def theme_name(self) -> str:
        """Nome do tema atual (default, matrix, ...)."""
        return self._theme_name

    @property
    def theme_colors(self) -> dict:
        """Retorna o dicionário de cores do tema atual."""
        return THEMES.get(self._theme_name, THEMES["dark"])

    def set_language(self, lang: str):
        """Muda o idioma e salva a preferência."""
        if lang in LANGUAGES:
            self._language = lang
            self._local_config["language"] = lang
            save_config(self._local_config)

    def set_theme(self, theme: str):
        """Muda o tema e salva a preferência."""
        if theme in THEMES:
            self._theme_name = theme
            self._local_config["theme"] = theme
            save_config(self._local_config)
            self.apply_theme()

    def apply_theme(self):
        """Aplica as cores do tema atual ao CSS da aplicação."""
        t = self.theme_colors
        css = f"""
        Screen {{ background: {t['bg']}; }}
        * {{ scrollbar-color: {t['primary']} {t['surface']}; scrollbar-size: 1 1; }}
        Input:focus {{ border: solid {t['primary']}; }}
        Button:focus {{ border: solid {t['secondary']}; }}
        Button.-primary {{ background: {t['primary']}; color: {t['text']}; }}
        Button.-primary:hover {{ background: {t['primary_hover']}; }}

        MainScreen, WelcomeScreen, LoginScreen, RegisterScreen,
        SettingsScreen, BlockListScreen, UserListScreen,
        ProfileViewScreen, InputScreen, ConfirmScreen,
        FilePickerScreen {{ background: {t['bg']}; }}

        #app-header, #bl-header, #ul-header,
        #config-box, #confirm-box, #input-box,
        #dms-toolbar, #chat-input-box, #feed-input-box,
        #dms-input-box, #dms-header,
        #feed-toolbar, #files-toolbar, #notif-toolbar,
        #search-toolbar, #profile-toolbar,
        #profile-edit-box, #profile-info,
        #pv-user-info, #tab-bar, #config-btn,
        #search-filters, #post-comment-input-box {{
            background: {t['surface']};
        }}

        .bl-row:hover, .dms-conv-item:hover,
        .dms-conv-item.-active,
        NotificationWidget:hover, PostWidget:hover,
        UserListItem:hover {{ background: {t['surface_hover']}; }}

        #app-header, #bl-header, #ul-header,
        #chat-input-box, #feed-input-box, #dms-input-box,
        #dms-toolbar, #dms-header, #feed-toolbar,
        #files-toolbar, #notif-toolbar, #search-toolbar,
        #profile-toolbar,
        #post-comment-input-box, #search-filters {{
            border-bottom: solid {t['border']};
        }}
        #dms-conv-list {{ border-right: solid {t['border']}; }}
        #dms-conv-list:focus-within {{ border-right: solid {t['primary']}; }}
        #post-comments-list {{ border-left: solid {t['border']}; }}
        #profile-edit-box {{ border: solid {t['secondary']}; }}

        #config-box, #profile-info, #pv-user-info {{
            border: solid {t['border']};
        }}

        #chat-anon-toggle {{ border: solid {t['border']}; color: {t['text_dim']}; }}
        #chat-anon-toggle.active {{ background: {t['primary']}; color: {t['text']}; border: solid {t['primary']}; }}
        #pgp-toggle-btn {{ background: {t['surface']}; border: solid {t['text_muted']}; color: {t['text']}; }}
        #pgp-toggle-btn.-active {{ background: {t['primary']}; border: solid {t['primary']}; color: {t['text']}; }}

        .bl-row, .chat-msg, .comment-item, .dms-conv-item,
        NotificationWidget, PostWidget, UserListItem,
        .file-item, .search-section-header {{
            border-bottom: solid {t['border']};
        }}
        .search-section-header {{ background: {t['surface']}; }}
        DmMsgWidget {{ border-bottom: dashed {t['border']}; }}

        .cfg-lang-btn, .lang-btn, .filter-btn {{
            background: {t['border']}; color: {t['text']};
        }}
        .cfg-lang-btn.-primary, .lang-btn.-primary, .filter-btn.on {{
            background: {t['primary']}; color: {t['text']};
        }}

        #header-title, #bl-title, #ul-title,
        .tab-btn.-active, .comment-toggle:hover {{
            color: {t['primary']};
        }}
        #header-user, #config-btn, #pow-status,
        .field-label, .date-sep, .dms-info,
        .info, .post-menu-btn, .tab-btn {{
            color: {t['text_dim']};
        }}
        #back-hint, #footer-hint, #status-text,
        .hint-text, .post-like-btn {{
            color: {t['text_muted']};
        }}
        .edit-title, .comment-toggle,
        #search-toolbar > #search-filter-toggle.-open {{
            color: {t['secondary']};
        }}
        .tab-btn:hover {{ color: {t['tab_hover']}; }}
        .tab-btn.-has-unread {{ color: #f59e0b; }}
        .success, #pow-status.done {{ color: {t['accent']}; }}
        .error, #confirm-title, #error-msg {{ color: {t['error']}; }}
        #version-info {{ color: {t['text_muted']}; }}

        Input {{
            color: {t['text']}; background: {t['surface']}; border: solid {t['border']};
        }}
        Button {{
            color: {t['text']}; background: {t['surface']};
        }}
        .action-btn, .bottom-btn, #entrar-btn, #criar-btn {{
            color: {t['text']}; background: {t['surface']};
        }}
        Label, Static, RichLog, Log, ListItem,
        Select, Switch, RadioSet, RadioButton, ListView,
        TabbedContent, TabPane, ContentSwitcher, DataTable, Tree,
        ProgressBar, DirectoryTree {{
            color: {t['text']};
        }}
        PostWidget, DmMsgWidget, NotificationWidget, ChatMsgWidget,
        CommentWidget, UserListItem {{
            background: {t['bg']}; color: {t['text']};
        }}
        .mq-label, #input-prompt, #confirm-msg {{
            color: {t['text']};
        }}
        .save-btn {{
            color: {t['text_dim']}; background: {t['surface']}; border: solid {t['border']};
        }}
        .save-btn.-saved {{
            color: {t['accent']}; border: solid {t['accent']}; background: {t['surface']};
        }}
        """
        self.stylesheet.add_source(css, read_from=("theme", "0"), is_default_css=False, tie_breaker=1)
        self.stylesheet.reparse()
        self.stylesheet.update(self)

    def _is_onion_url(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        return host.endswith(".onion")

    def _tor_socks_ports(self) -> list[int]:
        return [9050, 19050, 9150]

    def _port_open(self, port: int) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except Exception:
            return False

    def _ensure_tor(self, server_url: str):
        if not self._is_onion_url(server_url):
            return
        for port in self._tor_socks_ports():
            if self._port_open(port):
                return
        tor_path = shutil.which("tor")
        if not tor_path:
            return
        try:
            proc = subprocess.Popen(
                [tor_path, "--SocksPort", "9050", "--DataDirectory",
                 os.path.expanduser("~/.tor-hexfeed")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.time() + 30
            while time.time() < deadline:
                if self._port_open(9050):
                    self._tor_process = proc
                    return
                time.sleep(0.5)
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

    def on_mount(self):
        """Inicializa a UI: aplica tema e, se houver token salvo, faz login automático."""
        self.apply_theme()
        saved_token = self._local_config.get("saved_token", "")
        saved_username = self._local_config.get("saved_username", "")
        if saved_token and saved_username:
            self.client_state.token = saved_token
            self.client_state.user = {"username": saved_username}
            self.set_timer(0.1, self._auto_login)
        else:
            self.push_screen("welcome")

    async def _auto_login(self):
        """Tenta login automático com token salvo."""
        if self.client_state.server_url:
            ok = await self._check_version()
            if not ok:
                return
            self.push_screen("main")
        else:
            self.push_screen("welcome")

    async def _check_version(self) -> bool:
        """Verifica se o cliente é compatível com o servidor."""
        if not self.client_state.server_url:
            return False
        try:
            info = await self.api.get_version_info()
            min_version = info.get("client_min_version", "0.1.0")
            if min_version > __version__:
                self.push_screen("outdated", min_version)
                return False
            return True
        except Exception:
            self.push_screen("welcome")
            self.notify(
                "Não foi possível conectar ao servidor. "
                "Verifique o endereço e se o Tor está rodando.",
                severity="error",
            )
            return False

    async def on_app_quit(self):
        """Fecha a sessão HTTP e encerra Tor ao sair."""
        try:
            await self.api.close()
        except Exception:
            pass
        if self._tor_process:
            try:
                self._tor_process.terminate()
                self._tor_process.wait(timeout=5)
            except Exception:
                self._tor_process.kill()
                self._tor_process.wait(timeout=5)
