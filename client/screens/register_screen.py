"""
hexfeed - tela de cadastro
Cria conta com username, senha, PGP (opcional).
Anti-bot: PoW em background + desafio matemático (2 perguntas).
"""

import hashlib
import asyncio
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Static, Input, TextArea
from textual.containers import Center, Vertical
from textual.binding import Binding
from textual import work


def solve_pow(challenge: str, difficulty: int) -> int:
    """Resolve Proof-of-Work: encontra nonce cujo SHA256 inicie com 'difficulty' zeros."""
    target = "0" * difficulty
    nonce = 0
    while True:
        data = f"{challenge}{nonce}".encode()
        h = hashlib.sha256(data).hexdigest()
        bits = bin(int(h, 16))[2:].zfill(256)
        if bits.startswith(target):
            return nonce
        nonce += 1


class RegisterScreen(Screen):
    # Escape volta para a tela anterior
    BINDINGS = [
        Binding("escape", "go_back", "Voltar"),
    ]

    CSS = """
    RegisterScreen {
        align: center middle;
        background: #0a0a0f;
    }
    #register-box {
        width: 74; height: auto; max-height: 95%;
        padding: 1 2; border: solid #06b6d4; background: #111118;
    }
    #register-title {
        text-style: bold; color: #06b6d4;
        text-align: center; width: 100%; margin-bottom: 1;
    }
    .field-label { color: #64748b; text-align: left; width: 100%; margin-top: 1; }
    Input { width: 100%; margin-bottom: 0; }
    TextArea { width: 100%; border: solid #334155; }
    #pgp-key-area { height: 10; }
    Button { width: 100%; margin-top: 1; }
    #error-msg { color: #ef4444; text-align: center; width: 100%; margin-top: 1; }
    #pow-status { color: #64748b; text-align: center; width: 100%; margin-top: 1; }
    #pow-status.done { color: #22c55e; }
    .hint-text { color: #475569; text-align: center; width: 100%; margin-bottom: 0; }
    #back-hint { color: #475569; text-align: center; width: 100%; margin-top: 1; }
    #pgp-toggle-btn { background: #1e293b; color: #e2e8f0; border: solid #334155; }
    #pgp-toggle-btn.-active { background: #7c3aed; color: #e2e8f0; border: solid #7c3aed; }
    #math-box { margin-top: 1; }
    .mq-label { color: #e2e8f0; text-align: left; width: 100%; margin-top: 1; }
    .math-input { width: 100%; }
    #math-loading { color: #94a3b8; text-align: center; width: 100%; margin-top: 1; }
    """

    def __init__(self):
        super().__init__()
        # Estado do Proof-of-Work e desafio matemático
        self._challenge = ""
        self._difficulty = 0
        self._pow_nonce: int | None = None
        self._questions: list[dict] = []
        self._pow_done = False
        self._in_math = False

    def compose(self) -> ComposeResult:
        # Monta formulário de cadastro com campos e área anti-bot
        lang = self.app.lang
        with Center():
            with Vertical(id="register-box"):
                yield Static(lang["register_title"], id="register-title")
                yield Static("", id="pow-status")
                yield Static(lang["register_username"], classes="field-label")
                yield Input(placeholder=lang["register_username_placeholder"], id="username")
                yield Static(lang["register_password"], classes="field-label")
                yield Input(placeholder=lang["register_password"], password=True, id="password")
                yield Input(placeholder=lang["register_confirm"], password=True, id="confirm-password")
                yield Static(lang["register_email"], classes="field-label")
                yield Input(placeholder=lang["register_email_placeholder"], id="email")
                yield Button(lang["register_add_pgp"], id="pgp-toggle-btn")
                with Vertical(id="pgp-fields"):
                    yield Static(lang["register_pgp_key"], classes="field-label")
                    yield TextArea("", id="pgp-key-area")
                    yield Static(lang["register_pgp_hint"], classes="hint-text")
                    yield Static(lang["register_pgp_explanation"], classes="hint-text")
                with Vertical(id="math-box"):
                    # 2 perguntas matemáticas anti-bot
                    for i in range(2):
                        yield Static("", id=f"mq-{i}", classes="mq-label")
                        yield Input(placeholder="?", id=f"math-ans-{i}", classes="math-input")
                    yield Static(lang["register_math_hint"], id="math-loading")
                yield Button(lang["register_btn"], id="register-btn", variant="primary")
                yield Static("", id="error-msg")
                yield Static(lang["back_hint"], id="back-hint")

    def on_mount(self):
        # Esconde PGP e questões matemáticas inicialmente
        self.query_one("#pgp-fields", Vertical).display = False
        self.query_one("#math-box", Vertical).display = False
        self._fetch_and_start_pow()

    def on_input_submitted(self, event: Input.Submitted):
        # Enter no campo de resposta matemática avança ou submete
        if event.input.id and event.input.id.startswith("math-ans-"):
            idx = int(event.input.id.split("-")[-1])
            if idx < 1:
                # Foca próxima pergunta se esta for a primeira
                self.query_one(f"#math-ans-{idx + 1}", Input).focus()
            else:
                self._submit_register()

    def _fetch_and_start_pow(self):
        """Inicia o fluxo anti-bot: busca desafio + PoW + perguntas."""
        lang = self.app.lang
        self.query_one("#pow-status", Static).update(lang["register_pow_requesting"])
        self._do_fetch_challenge()

    @work(exclusive=True)
    async def _do_fetch_challenge(self):
        """Busca desafio PoW e perguntas matemáticas do servidor."""
        lang = self.app.lang
        try:
            data = await self.app.api.get_challenge()
            self._challenge = data["challenge"]
            self._difficulty = data["difficulty"]
            self._questions = data["questions"]
            self.query_one("#pow-status", Static).update(lang["register_pow_verifying"])
            # Resolução do PoW em thread separada (CPU-bound)
            self._pow_nonce = await asyncio.to_thread(solve_pow, self._challenge, self._difficulty)
            self._pow_done = True
            pow_st = self.query_one("#pow-status", Static)
            pow_st.update(lang["register_pow_done"])
            pow_st.add_class("done")
        except Exception:
            self.query_one("#pow-status", Static).update(lang["register_pow_error"])

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "register-btn":
            if self._in_math:
                self._submit_register()
            else:
                self._start_register()
        elif event.button.id == "pgp-toggle-btn":
            self._toggle_pgp()

    def _toggle_pgp(self):
        """Mostra/esconde campos de chave PGP."""
        lang = self.app.lang
        fields = self.query_one("#pgp-fields", Vertical)
        btn = self.query_one("#pgp-toggle-btn", Button)
        fields.display = not fields.display
        if fields.display:
            btn.label = lang["register_remove_pgp"]
            btn.add_class("-active")
        else:
            btn.label = lang["register_add_pgp"]
            btn.remove_class("-active")

    def _start_register(self):
        """Valida campos básicos e inicia desafio matemático."""
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        confirm = self.query_one("#confirm-password", Input).value
        email = self.query_one("#email", Input).value.strip()

        lang = self.app.lang
        error_st = self.query_one("#error-msg", Static)

        if not username or not password:
            error_st.update(lang["register_required_fields"])
            return
        if password != confirm:
            error_st.update(lang["register_passwords_mismatch"])
            return
        if len(password) < 6:
            error_st.update(lang["register_password_min_length"])
            return
        if len(username) < 3:
            error_st.update(lang["register_username_min_length"])
            return

        if not self._pow_done:
            error_st.update(lang["register_waiting_verification"])
            return

        # Armazena dados temporariamente para envio após desafio
        self._stored_username = username
        self._stored_password = password
        self._stored_email = email
        fields = self.query_one("#pgp-fields", Vertical)
        self._stored_pgp = self.query_one("#pgp-key-area", TextArea).text.strip() if fields.display else ""

        self._show_math()

    def _show_math(self):
        """Exibe perguntas matemáticas anti-bot."""
        lang = self.app.lang
        self._in_math = True
        self.query_one("#register-btn", Button).display = False
        self.query_one("#error-msg", Static).update("")
        for i in range(2):
            self.query_one(f"#mq-{i}", Static).update(self._questions[i]["q"].replace("*", "×"))
            inp = self.query_one(f"#math-ans-{i}", Input)
            inp.value = ""
            inp.styles.display = "block"
        self.query_one("#math-box", Vertical).display = True
        self.query_one("#math-loading", Static).update(lang["register_math_hint"])
        self.query_one("#math-ans-0", Input).focus()

    def _hide_math(self, error: str = ""):
        """Esconde perguntas e reexibe botão de cadastro."""
        self._in_math = False
        self.query_one("#math-box", Vertical).display = False
        self.query_one("#register-btn", Button).disabled = False
        self.query_one("#register-btn", Button).display = True
        self.query_one("#error-msg", Static).update(error)

    def _submit_register(self):
        """Valida respostas matemáticas e envia cadastro."""
        lang = self.app.lang
        answers = []
        for i in range(2):
            try:
                val = int(self.query_one(f"#math-ans-{i}", Input).value.strip())
                answers.append(val)
            except ValueError:
                self.query_one("#math-loading", Static).update(lang["register_enter_numbers_only"])
                return

        self._math_answers = answers
        register_btn = self.query_one("#register-btn", Button)
        register_btn.disabled = True
        self.query_one("#math-loading", Static).update(lang["register_creating_account"])
        self._do_register()

    @work(exclusive=True)
    async def _do_register(self):
        """Chama API de registro com PoW + respostas matemáticas."""
        try:
            await self.app.api.register(
                username=self._stored_username,
                password=self._stored_password,
                email=self._stored_email,
                pgp_key=self._stored_pgp,
                pow_challenge=self._challenge,
                pow_nonce=self._pow_nonce,
                math_answers=self._math_answers,
            )
            self.app.pop_screen()
            self.app.push_screen("main")
        except (ValueError, Exception) as e:
            self._hide_math(str(e))

    def action_go_back(self):
        self.app.pop_screen()
