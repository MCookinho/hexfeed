from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Input
from textual.containers import Vertical, Horizontal
from textual.binding import Binding
from client.api import HexfeedAPI
from client.i18n import LANGUAGES
from client.local_config import save_config, DEFAULT_SERVER_URL


class SettingsScreen(Screen):

    BINDINGS = [
        Binding("escape", "go_back", "Voltar"),
    ]

    CSS = """
    SettingsScreen {
        align: center middle;
        background: #0a0a0f;
    }
    #settings-box {
        width: 60; height: auto; padding: 2 3;
        border: solid #7c3aed; background: #111118;
    }
    #settings-title {
        text-style: bold; color: #7c3aed;
        text-align: center; width: 100%; margin-bottom: 2;
    }
    .section-title {
        color: #64748b; text-style: bold;
        margin-top: 1; margin-bottom: 0;
    }
    .config-input {
        width: 100%; margin-bottom: 1;
    }
    .lang-btn { width: auto; min-width: 14; background: #1e293b; color: #e2e8f0; }
    .lang-btn.-primary { background: #7c3aed; color: #e2e8f0; }
    #status-text { color: #475569; text-align: center; width: 100%; margin-top: 1; }
    #back-hint { color: #475569; text-align: center; width: 100%; margin-top: 1; }
    #server-config-box { margin-top: 1; margin-bottom: 1; }
    #save-server-btn { width: 100%; margin-top: 1; margin-bottom: 1; }
    """

    def __init__(self, from_main: bool = False):
        super().__init__()
        self._from_main = from_main

    def compose(self) -> ComposeResult:
        cfg = self.app._local_config
        with Vertical(id="settings-box"):
            yield Static(self.app.lang["settings_title"], id="settings-title")

            yield Static(self.app.lang["language_label"], classes="section-title")
            with Horizontal():
                for lang_key in ("pt-BR", "en", "es"):
                    btn = Button(
                        self.app.lang["lang_pt_br"] if lang_key == "pt-BR" else self.app.lang["lang_en"] if lang_key == "en" else self.app.lang["lang_es"],
                        id=f"lang-{lang_key}",
                        classes="lang-btn",
                    )
                    if lang_key == self.app.language:
                        btn.variant = "primary"
                    yield btn

            yield Static(self.app.lang["server_section"], classes="section-title")
            with Vertical(id="server-config-box"):
                yield Input(
                    value=cfg.get("server_url", ""),
                    placeholder=self.app.lang["server_placeholder"],
                    id="server-url-input",
                    classes="config-input",
                )
                yield Button(self.app.lang["save_btn"], id="save-server-btn")

            yield Static(self.app.lang["settings_theme_label"], classes="section-title")
            with Horizontal():
                for key in ("dark", "light"):
                    theme_btn = Button(
                        self.app.lang[f"theme_{key}"],
                        id=f"theme-{key}",
                        classes="lang-btn",
                    )
                    if key == self.app.theme_name:
                        theme_btn.variant = "primary"
                    yield theme_btn

            yield Static("", id="status-text")

            if not self._from_main:
                yield Button(self.app.lang["back"], id="settings-back", variant="primary")
            yield Static(self.app.lang["settings_back_hint"], id="back-hint")

    def on_button_pressed(self, event: Button.Pressed):
        bid = event.button.id

        if bid.startswith("theme-"):
            theme = bid.split("-", 1)[1]
            self.app.set_theme(theme)
            self.notify(self.app.lang["theme_changed"].format(self.app.lang[f"theme_{theme}"]))
            self._refresh_buttons()
            return

        if bid.startswith("lang-"):
            lang = bid.split("-", 1)[1]
            self.app.set_language(lang)
            self.notify(self.app.lang["language_changed"].format(lang))
            self.app.pop_screen()
            self.app.set_timer(0.05, lambda: self.app.push_screen("welcome"))
            return

        if bid == "save-server-btn":
            self._save_server_config()
            return

        if bid == "settings-back":
            self.app.pop_screen()

    def _save_server_config(self):
        server_url = self.query_one("#server-url-input", Input).value.strip()

        if not server_url:
            cfg = self.app._local_config
            cfg.pop("server_url", None)
            save_config(cfg)
            self.app.client_state.server_url = DEFAULT_SERVER_URL
            self.app.api = HexfeedAPI(self.app.client_state)
            self.notify(self.app.lang["server_default_restored"])
            return

        cfg = self.app._local_config
        cfg["server_url"] = server_url
        save_config(cfg)

        self.app.client_state.server_url = server_url
        self.app.api = HexfeedAPI(self.app.client_state)

        self.notify(self.app.lang["config_saved"])

    def _refresh_buttons(self):
        for btn in self.query(".lang-btn"):
            ident = btn.id or ""
            if ident.startswith("lang-"):
                lang = ident.split("-", 1)[1]
                btn.variant = "primary" if lang == self.app.language else "default"
            elif ident.startswith("theme-"):
                theme = ident.split("-", 1)[1]
                btn.variant = "primary" if theme == self.app.theme_name else "default"
        self.query_one("#status-text", Static).update(
            f"{self.app.lang['language_label']} {self.app.language}"
        )

    def action_go_back(self):
        self.app.pop_screen()

    def on_mount(self):
        self._refresh_buttons()
