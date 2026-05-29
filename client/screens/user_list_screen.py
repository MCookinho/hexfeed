from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.binding import Binding
from textual import work
from rich.text import Text


class UserListItem(Static):
    def __init__(self, user: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_data = user

    def on_mount(self):
        u = self.user_data
        t = Text()
        t.append(f"@{u['username']}", style="bold #7c3aed")
        if u.get("display_name"):
            t.append(f" ({u['display_name']})", style="#64748b")
        if u.get("post_count"):
            t.append(f"\n\U0001f4ca Posts: {u['post_count']}", style="#475569")
        self.update(t)

    def on_click(self):
        username = self.user_data.get("username", "")
        if username:
            from client.screens.profile_view_screen import ProfileViewScreen
            self.app.push_screen(ProfileViewScreen(username))


class UserListScreen(Screen):
    CSS = """
    UserListScreen { background: #0a0a0f; }
    #ul-header { height: 3; dock: top; background: #111118; border-bottom: solid #7c3aed; padding: 0 2; }
    #ul-back { width: auto; }
    #ul-title { text-style: bold; color: #7c3aed; content-align: center middle; width: 100%; }
    #ul-list { height: 1fr; overflow-y: auto; }
    UserListItem { padding: 1 2; border-bottom: solid #1e293b; height: auto; }
    UserListItem:hover { background: #16162a; }
    .info { color: #64748b; padding: 1 2; }
    """

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
    ]

    def __init__(self, username: str, mode: str = "followers"):
        super().__init__()
        self.target_username = username
        self.mode = mode

    def compose(self) -> ComposeResult:
        lang = self.app.lang
        mode_label = lang["followers_list"] if self.mode == "followers" else lang["following_list"]
        with Horizontal(id="ul-header"):
            yield Button(lang["back"], id="ul-back")
            yield Static(f"{mode_label}: @{self.target_username}", id="ul-title")
        yield ScrollableContainer(id="ul-list")

    def on_mount(self):
        self._load_list()

    @work
    async def _load_list(self):
        container = self.query_one("#ul-list", ScrollableContainer)
        try:
            if self.mode == "followers":
                data = await self.app.api.get_followers(self.target_username)
                empty_msg = self.app.lang["followers_empty"]
            else:
                data = await self.app.api.get_following(self.target_username)
                empty_msg = self.app.lang["following_empty"]
            users = data.get("users", []) if isinstance(data, dict) else data
            if not users:
                await container.mount(Static(empty_msg, classes="info"))
            for u in users:
                widget = UserListItem(u)
                await container.mount(widget)
        except Exception as e:
            await container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "ul-back":
            self.dismiss(None)

    def action_go_back(self):
        self.dismiss(None)
