"""
hexfeed - tela de perfil de usuário
Mostra dados de um usuário + seus posts + opção de seguir.
"""

import asyncio
import os
import tempfile

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.binding import Binding
from textual import work
from rich.text import Text


def _ago(ts: str) -> str:
    """Formata timestamp ISO para tempo relativo (ex: '5m', '2h')."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return ""
    diff = datetime.utcnow() - dt
    secs = int(diff.total_seconds())
    if secs < 60:
        return "agora"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


class PostWidget(Static):
    """Widget que exibe um post no perfil visitado."""

    def __init__(self, post: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.post_data = post

    def on_mount(self):
        # Monta o texto do post com autor, tempo, conteúdo e curtidas
        ago = _ago(self.post_data["created_at"])
        likes = self.post_data.get("like_count", 0)
        t = Text()
        t.append(f"@{self.post_data['username']}", style="bold #7c3aed")
        if self.post_data.get("display_name"):
            t.append(f" ({self.post_data['display_name']})", style="#64748b")
        t.append(f"  · {ago}", style="#475569")
        if self.post_data.get("reply_to"):
            t.append(f"  ↩", style="#22c55e")
        t.append(f"\n{self.post_data['content']}", style=self.app.theme_colors["text"])
        like_style = "#ef4444" if likes > 0 else "#475569"
        t.append(f"\n♥ {likes}", style=like_style)
        self.update(t)


class ProfileViewScreen(Screen):
    """Tela que mostra o perfil de outro usuário."""

    CSS = """
    ProfileViewScreen {
        background: #0a0a0f;
    }
    #pv-header {
        height: 3; dock: top;
        background: #111118; border-bottom: solid #7c3aed;
        padding: 0 2;
    }
    #pv-back {
        width: auto;
    }
    #pv-title {
        text-style: bold; color: #7c3aed;
        content-align: center middle; width: 100%;
    }
    #pv-header-profile { height: auto; margin: 1; }
    #pv-avatar-box {
        height: auto; width: auto;
    }
    #pv-user-info { height: auto; padding: 2; border: solid #1e293b; background: #111118; width: 1fr; }
    #pv-actions { height: 3; padding: 0 2; margin: 0 1; }
    #pv-posts {
        height: 1fr; overflow-y: auto;
        border-top: solid #1e293b;
    }
    PostWidget {
        padding: 1 2; border-bottom: solid #1e293b; height: auto;
    }
    PostWidget:hover { background: #16162a; }
    .pv-btn { width: auto; min-width: 16; }
    .error { color: #ef4444; }
    .info { color: #64748b; }
    """

    BINDINGS = [
        Binding("escape", "go_back", "Voltar"),
    ]

    def __init__(self, username: str):
        super().__init__()
        self.viewed_username = username
        self._user_data = None

    def compose(self) -> ComposeResult:
        # Cabeçalho com volta e título
        with Horizontal(id="pv-header"):
            yield Button(self.app.lang["back"], id="pv-back", classes="pv-btn")
            yield Static(self.app.lang['profile_view_title'].format(self.viewed_username), id="pv-title")
        # Info do perfil: avatar + dados do usuário
        with Horizontal(id="pv-header-profile"):
            yield Static("", id="pv-avatar-box")
            yield Static("", id="pv-user-info")
        # Ações: seguir, DM, bloquear, seguidores/sguindo, atualizar
        with Horizontal(id="pv-actions"):
            yield Button(self.app.lang["follow"], id="pv-follow", variant="primary", classes="pv-btn")
            yield Button(self.app.lang["dm_button"], id="pv-dm", classes="pv-btn")
            yield Button(self.app.lang["block_user"], id="pv-block", classes="pv-btn")
            yield Button(self.app.lang["view_followers"], id="pv-followers", classes="pv-btn")
            yield Button(self.app.lang["view_following"], id="pv-following", classes="pv-btn")
            yield Button("↻", id="pv-refresh", classes="pv-btn")
        yield ScrollableContainer(id="pv-posts")

    def on_mount(self):
        self._load_profile()
        self._load_posts()

    @work(exclusive=False)
    async def _load_profile(self):
        """Carrega dados do perfil e avatar."""
        try:
            u = await self.app.api.get_user(self.viewed_username)
            self._user_data = u
            me = await self.app.api.get_me()
            is_self = me["username"] == self.viewed_username
            self.query_one("#pv-follow", Button).display = not is_self
            self.query_one("#pv-dm", Button).display = not is_self
            self.query_one("#pv-block", Button).display = not is_self
            info = self.query_one("#pv-user-info", Static)
            t = Text()
            t.append(f"@{u['username']}", style="bold #7c3aed")
            if u.get("display_name"):
                t.append(f"\n{u['display_name']}", style="#06b6d4")
            t.append(f"\n{u.get('bio', '')}", style=self.app.theme_colors["text"])
            t.append(self.app.lang["stats_posts"].format(u['post_count'], u['following_count'], u['follower_count']), style="#64748b")
            t.append(f"\n{self.app.lang['profile_joined']}: {u['created_at'][:10]}", style="#475569")
            info.update(t)

            # Renderiza avatar via chafa (ASCII art do terminal)
            avatar_box = self.query_one("#pv-avatar-box", Static)
            if u.get("avatar_path"):
                try:
                    img_data = await self.app.api.download_avatar(u["username"])
                    if img_data and len(img_data) > 100:
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                        try:
                            tmp.write(img_data)
                            tmp.close()
                            proc = await asyncio.create_subprocess_exec(
                                "chafa", tmp.name,
                                "--format", "symbols",
                                "--symbols", "block",
                                "--size", "36x16",
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            stdout, _ = await proc.communicate()
                            if stdout:
                                avatar_box.update(Text.from_ansi(stdout.decode()))
                        finally:
                            os.unlink(tmp.name)
                except Exception:
                    pass

            btn = self.query_one("#pv-follow", Button)
            if u.get("is_following"):
                btn.label = self.app.lang["unfollow"]
                btn.variant = "default"
            else:
                btn.label = self.app.lang["follow"]
                btn.variant = "primary"

            # Block button
            block_btn = self.query_one("#pv-block", Button)
            if u.get("is_blocked"):
                block_btn.label = self.app.lang["unblock_user"]
                block_btn.variant = "default"
            else:
                block_btn.label = self.app.lang["block_user"]
                block_btn.variant = "default"

            # Followers/Following buttons (mostra sempre, inclusive para si mesmo)
            self.query_one("#pv-followers", Button).display = True
            self.query_one("#pv-following", Button).display = True
        except Exception as e:
            self.query_one("#pv-user-info", Static).update(f"{self.app.lang['error_prefix']}: {e}")

    @work(exclusive=False)
    async def _load_posts(self):
        """Carrega posts do usuário visitado."""
        container = self.query_one("#pv-posts", ScrollableContainer)
        await container.remove_children()
        try:
            posts = await self.app.api.get_user_posts(self.viewed_username, limit=30)
            if not posts:
                await container.mount(Static(self.app.lang["profile_no_posts"], classes="info"))
            for p in posts:
                await container.mount(PostWidget(p))
        except Exception as e:
            await container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    def on_button_pressed(self, event: Button.Pressed):
        bid = event.button.id
        if bid == "pv-back":
            self.dismiss(None)
        elif bid == "pv-dm":
            self._open_dm()
        elif bid == "pv-refresh":
            self._load_profile()
            self._load_posts()
        elif bid == "pv-follow":
            self._toggle_follow()
        elif bid == "pv-block":
            self._toggle_block()
        elif bid == "pv-followers":
            self._open_followers()
        elif bid == "pv-following":
            self._open_following()

    @work(exclusive=False)
    async def _toggle_follow(self):
        """Segue/deixa de seguir o usuário e recarrega perfil."""
        if not self._user_data:
            return
        try:
            if self._user_data.get("is_following"):
                await self.app.api.unfollow(self.viewed_username)
                self.notify(f"{self.app.lang['unfollowed_user_prefix']} @{self.viewed_username}")
            else:
                await self.app.api.follow(self.viewed_username)
                self.notify(f"{self.app.lang['followed_user_prefix']} @{self.viewed_username}")
            self._load_profile()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    @work(exclusive=False)
    async def _toggle_block(self):
        """Bloqueia/desbloqueia o usuário."""
        if not self._user_data:
            return
        try:
            if self._user_data.get("is_blocked"):
                await self.app.api.unblock_user(self.viewed_username)
                self.notify(self.app.lang["unblocked_user"])
            else:
                await self.app.api.block_user(self.viewed_username)
                self.notify(self.app.lang["blocked_user"])
            self._load_profile()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    def _open_followers(self):
        """Abre tela de seguidores."""
        from client.screens.user_list_screen import UserListScreen
        self.app.push_screen(UserListScreen(self.viewed_username, "followers"))

    def _open_following(self):
        """Abre tela de seguindo."""
        from client.screens.user_list_screen import UserListScreen
        self.app.push_screen(UserListScreen(self.viewed_username, "following"))

    @work(exclusive=False)
    async def _open_dm(self):
        """Sinaliza para tela principal que deve abrir DM."""
        btn = self.query_one("#pv-dm", Button)
        btn.label = self.app.lang["opening"]
        btn.disabled = True
        self.dismiss({"dm": self.viewed_username})

    def action_go_back(self):
        self.dismiss(None)
