"""
hexfeed - tela de entrar
Login com username e senha. Se a conta tiver PGP, pede chave privada.
"""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Static, Input, TextArea
from textual.containers import Center, Vertical, Horizontal
from textual.binding import Binding
from textual import work
from client.local_config import load_config, save_config


class LoginScreen(Screen):
    # Escape volta para a tela anterior
    BINDINGS = [
        Binding("escape", "go_back", "Voltar"),
    ]

    CSS = """
    LoginScreen {
        align: center middle;
        background: #0a0a0f;
    }
    #login-box {
        width: 60; height: auto; padding: 2 3;
        border: solid #7c3aed; background: #111118;
    }
    #login-title {
        text-style: bold; color: #7c3aed;
        text-align: center; width: 100%; margin-bottom: 2;
    }
    .field-label { color: #64748b; text-align: left; width: 100%; margin-top: 1; }
    Input { width: 100%; margin-bottom: 0; }
    TextArea { width: 100%; border: solid #334155; }
    #pgp-login-area { height: 8; }
    Button { width: 100%; margin-top: 1; }
    #error-msg { color: #ef4444; text-align: center; width: 100%; margin-top: 1; }
    #back-hint { color: #475569; text-align: center; width: 100%; margin-top: 1; }
    .save-btn { background: #1e293b; color: #64748b; border: solid #1e293b; }
    .save-btn.-saved { background: #1a1a2e; color: #22c55e; border: solid #22c55e; }
    """

    def compose(self) -> ComposeResult:
        # Monta formulário de login: username, senha, PGP opcional
        lang = self.app.lang
        self._save_login = False
        with Center():
            with Vertical(id="login-box"):
                yield Static(lang["login_title"], id="login-title")
                yield Static(self.app.lang["login_username_label"], classes="field-label")
                yield Input(placeholder=lang["login_username"], id="username")
                yield Static(self.app.lang["login_password_label"], classes="field-label")
                yield Input(placeholder=lang["login_password"], password=True, id="password")
                with Vertical(id="pgp-login-section"):
                    yield Static(self.app.lang["login_pgp_label"], classes="field-label")
                    yield TextArea("", id="pgp-login-area")
                yield Button(self.app.lang["login_save_toggle"], id="save-toggle", classes="save-btn")
                yield Button(lang["login_btn"], id="login-btn", variant="primary")
                yield Static("", id="error-msg")
                yield Static(lang["back_hint"], id="back-hint")

    def on_mount(self):
        # Esconde campo PGP até que o servidor requisite
        self.query_one("#pgp-login-section", Vertical).display = False

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "login-btn":
            self._do_login()
        elif event.button.id == "save-toggle":
            # Alterna salvamento de token local
            self._save_login = not self._save_login
            event.button.classes = "save-btn -saved" if self._save_login else "save-btn"

    def on_input_submitted(self, event: Input.Submitted):
        # Enter no campo de senha dispara login
        if event.input.id == "password":
            self._do_login()

    @work
    async def _do_login(self):
        # Tenta autenticar; se servidor exigir PGP, mostra campo de chave
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        pgp_priv = self.query_one("#pgp-login-area", TextArea).text.strip()
        if not username or not password:
            self.query_one("#error-msg", Static).update(self.app.lang["login_fill_all_fields"])
            return
        try:
            await self.app.api.login(username, password, pgp_priv)
            # Salva token local se marcado
            if self._save_login:
                cfg = load_config()
                cfg["saved_token"] = self.app.client_state.token
                cfg["saved_username"] = username
                save_config(cfg)
            self.app.pop_screen()
            self.app.push_screen("main")
        except ValueError as e:
            # Erros de validação (PGP necessária, chave inválida, etc.)
            errmsg = str(e)
            self.query_one("#error-msg", Static).update(errmsg)
            if "PGP private key required" in errmsg or "Invalid PGP private key" in errmsg:
                self.query_one("#pgp-login-section", Vertical).display = True
        except Exception as e:
            # Erros de rede/conexão
            self.query_one("#error-msg", Static).update(f"{self.app.lang['login_connection_error_prefix']}: {e}")

    def action_go_back(self):
        self.app.pop_screen()
