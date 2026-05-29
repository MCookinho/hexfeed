from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.binding import Binding
from textual import work
from rich.text import Text


class BlockRow(Vertical):
    """Uma linha na lista de bloqueados: info + botão desbloquear."""

    def __init__(self, username: str, display_name: str, created_at: str):
        super().__init__(classes="bl-row")
        self._username = username
        self._display_name = display_name
        self._created_at = created_at

    def compose(self) -> ComposeResult:
        t = Text()
        t.append(f"@{self._username}", style="bold #7c3aed")
        if self._display_name:
            t.append(f" ({self._display_name})", style="#64748b")
        t.append(f"\n{self.app.lang['blocked_since']}: {self._created_at[:16]}", style="#475569")
        yield Static(t, classes="bl-info")
        yield Button(self.app.lang["unblock"], id=f"unblock-{self._username}", classes="unblock-btn")


class BlockListScreen(Screen):

    CSS = """
    BlockListScreen { background: #0a0a0f; }
    #bl-header { height: 3; dock: top; background: #111118; border-bottom: solid #7c3aed; padding: 0 2; }
    #bl-back { width: auto; }
    #bl-title { text-style: bold; color: #7c3aed; content-align: center middle; width: 100%; }
    #bl-list { height: 1fr; overflow-y: auto; }
    .bl-row { height: auto; padding: 1 1; border-bottom: solid #1e293b; }
    .bl-row:hover { background: #16162a; }
    .bl-info { color: #64748b; }
    .unblock-btn { width: auto; min-width: 16; margin: 0 0 0 2; }
    """

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="bl-header"):
            yield Button(self.app.lang["back"], id="bl-back")
            yield Static(self.app.lang["blocks_list_title"], id="bl-title")
        with ScrollableContainer(id="bl-list"):
            yield Static("", id="bl-content")

    def on_mount(self):
        self._load_blocks()

    @work
    async def _load_blocks(self):
        container = self.query_one("#bl-list", ScrollableContainer)
        await container.remove_children()
        try:
            blocks = await self.app.api.get_my_blocks()
            if not blocks:
                await container.mount(Static(self.app.lang["blocks_list_empty"], classes="bl-info"))
                return
            for b in blocks:
                row = BlockRow(
                    b.get("blocked_username", "?"),
                    b.get("blocked_display_name", ""),
                    b["created_at"],
                )
                await container.mount(row)
        except Exception as e:
            await container.mount(Static(Text(f"{self.app.lang['error_prefix']}: {e}", style="bold #ef4444"), classes="bl-info"))

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "bl-back":
            self.dismiss(None)
        elif event.button.id and event.button.id.startswith("unblock-"):
            username = event.button.id[len("unblock-"):]
            self._unblock_user(username)

    @work
    async def _unblock_user(self, username: str):
        try:
            await self.app.api.unblock_user(username)
            self.notify(self.app.lang["unblocked"].format(username), severity="information")
            await self._load_blocks()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    def action_go_back(self):
        self.dismiss(None)
