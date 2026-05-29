from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Static
from textual.containers import Center, Vertical
from textual.binding import Binding
from rich.text import Text
from client.version import __version__
from client.local_config import DEFAULT_SERVER_URL


class WelcomeScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.quit", "Sair"),
    ]

    CSS = """
    WelcomeScreen {
        align: center middle;
        background: #0a0a0f;
    }
    #brand-container {
        width: 80; height: auto; margin-bottom: 2;
    }
    #logo {
        text-style: bold; color: #7c3aed;
        text-align: center; content-align: center middle; width: 100%;
    }
    #tagline {
        color: #64748b; text-align: center; width: 100%; margin-top: 1; margin-bottom: 2;
    }
    .action-btn { width: 100%; margin-bottom: 1; }
    .bottom-btn { width: 100%; margin-top: 0; }
    #entrar-btn { background: #7c3aed; color: #e2e8f0; }
    #criar-btn { background: #1e293b; color: #e2e8f0; border: tall #7c3aed; }
    #config-btn { background: #111118; color: #64748b; border: none; text-style: underline; }
    #footer-hint { color: #475569; text-align: center; width: 100%; margin-top: 2; }
    #version-info { color: #334155; text-align: center; width: 100%; }
    """

    def _get_server_url(self) -> str:
        return self.app.client_state.server_url or DEFAULT_SERVER_URL

    def compose(self) -> ComposeResult:
        server_url = self._get_server_url()

        with Center():
            with Vertical(id="brand-container"):
                yield Static(
                    Text(
                        "╔══════════════════════════╗\n"
                        "║      ⬡  H E X F E E D    ║\n"
                        "╚══════════════════════════╝",
                        style="bold #7c3aed",
                    ),
                    id="logo",
                )
                yield Static(self.app.lang["welcome_tagline"], id="tagline")
                yield Button(self.app.lang["enter"], id="entrar-btn", classes="action-btn")
                yield Button(self.app.lang["create_account"], id="criar-btn", classes="action-btn")
                yield Button(self.app.lang["settings"], id="config-btn", classes="bottom-btn")

                yield Static(
                    self.app.lang["connected_to"].format(server_url),
                    id="footer-hint",
                )
                yield Static(
                    self.app.lang["version_label"].format(__version__),
                    id="version-info",
                )

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "entrar-btn":
            self.app.push_screen("login")
        elif event.button.id == "criar-btn":
            self.app.push_screen("register")
        elif event.button.id == "config-btn":
            self.app.push_screen("settings")

    def on_mount(self):
        self.refresh_static()

    def refresh_static(self):
        server_url = self._get_server_url()

        self.query_one("#tagline", Static).update(self.app.lang["welcome_tagline"])
        self.query_one("#entrar-btn", Button).label = self.app.lang["enter"]
        self.query_one("#criar-btn", Button).label = self.app.lang["create_account"]
        self.query_one("#version-info", Static).update(
            self.app.lang["version_label"].format(__version__)
        )
        self.query_one("#footer-hint", Static).update(
            self.app.lang["connected_to"].format(server_url)
        )
