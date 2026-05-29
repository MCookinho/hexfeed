"""
hexfeed - tela principal
Interface principal com abas: Feed, Chat, DMs, Arquivos, Busca, Perfil, Configurações.
"""

from datetime import datetime
import os
import tempfile
import asyncio
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Input, Button
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.binding import Binding
from textual import work
from rich.text import Text
from client.local_config import load_config, save_config


def _ago(ts: str, lang: dict) -> str:
    """Converte timestamp ISO para tempo relativo (ex: '5m', '2h', '3d')."""
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return ""
    diff = datetime.utcnow() - dt
    secs = int(diff.total_seconds())
    if secs < 60:
        return lang["now"]
    if secs < 3600:
        return f"{secs // 60}{lang['ago_m']}"
    if secs < 86400:
        return f"{secs // 3600}{lang['ago_h']}"
    return f"{secs // 86400}{lang['ago_d']}"


class PostWidget(Vertical):
    """Widget de post no feed. Exibe conteúdo, mídia e menu de ações."""

    CHAFA_HEIGHT = 12

    def __init__(self, post: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.post_data = post
        self._media_rendered = False
        self._anim_timer = None
        self._anim_frames = []
        self._anim_idx = 0
        self._post_base: Text | None = None

    def compose(self) -> ComposeResult:
        # Cabeçalho: username + tempo + menu (apenas para dono)
        with Horizontal(id="post-header"):
            yield Static("", id="post-header-text")
            yield Button(self.app.lang["post_menu_btn"], id="post-menu-btn", classes="post-menu-btn")
        yield Static("", id="post-content")
        with Horizontal(id="post-actions"):
            yield Button("", id="post-like-btn", classes="post-like-btn")
            yield Button("", id="post-comment-toggle", classes="comment-toggle")
        with Vertical(id="post-comments-area", classes="comments-area-hidden"):
            yield ScrollableContainer(id="post-comments-list", classes="comments-list")
            with Horizontal(id="post-comment-input-box"):
                yield Input(placeholder=self.app.lang["comment_placeholder"], id="post-comment-input")
                yield Button(self.app.lang["comment_btn"], id="post-comment-btn", variant="primary")

    def on_mount(self):
        # Verifica se o post é do usuário logado (mostra menu de exclusão)
        user_id = self.app.client_state.user.get("id") if self.app.client_state.user else None
        self._is_owner = user_id is not None and self.post_data.get("user_id") == user_id
        if not self._is_owner:
            self.query_one("#post-menu-btn", Button).display = False

        # Monta cabeçalho do post
        ago = _ago(self.post_data["created_at"], self.app.lang)
        h = Text()
        h.append(f"@{self.post_data['username']}", style="bold #7c3aed")
        if self.post_data.get("display_name"):
            h.append(f" ({self.post_data['display_name']})", style="#64748b")
        h.append(f"  · {ago}", style="#475569")
        if self.post_data.get("reply_to"):
            h.append(f"  ↩", style="#22c55e")
        self.query_one("#post-header-text", Static).update(h)

        # Monta corpo do post: conteúdo + mídia
        body = Text()
        body.append(self.post_data.get("content", ""), style=self.app.theme_colors["text"])
        if self.post_data.get("file_name"):
            ext = self.post_data["file_name"].rsplit(".", 1)[-1].lower() if "." in self.post_data["file_name"] else ""
            icon = "🎬" if ext in ("mp4", "webm", "mov", "avi") else "📷" if ext in ("gif",) else "📷"
            body.append(f"\n{icon} {self.post_data['file_name']}", style="#64748b")
        self._post_base = body
        self._content_st = self.query_one("#post-content", Static)

        # Se há mídia anexada, renderiza via chafa
        if self.post_data.get("file_id") and not self._media_rendered:
            placeholder = "\n" * (self.CHAFA_HEIGHT + 1)
            combined = Text()
            combined.append_text(self._post_base)
            combined.append(placeholder)
            self._content_st.update(combined)
            self._render_media()
        else:
            self._content_st.update(self._post_base)

        # Botão de like
        like_count = self.post_data.get("like_count", 0)
        is_liked = self.post_data.get("is_liked", False)
        like_btn = self.query_one("#post-like-btn", Button)
        like_btn.label = f"{'♥' if not is_liked else '♥'} {like_count}"
        like_btn.classes = "post-like-btn liked" if is_liked else "post-like-btn"

        # Botão de comentários
        self._comments_visible = False
        cc = self.post_data.get("comment_count", 0)
        self.query_one("#post-comment-toggle", Button).label = f"{self.app.lang['view_comments']} ({cc})"

    def on_unmount(self):
        # Para animação GIF quando widget é removido
        if self._anim_timer:
            self._anim_timer.stop()

    def on_click(self):
        # Clique baixa mídia ou abre perfil
        if self.post_data.get("file_id"):
            self._download_media()
        else:
            username = self.post_data.get("username", "")
            if username:
                self.screen._open_profile(username)

    def on_button_pressed(self, event: Button.Pressed):
        bid = event.button.id
        if bid == "post-menu-btn":
            self._confirm_delete()
        elif bid == "post-like-btn":
            self._toggle_like()
        elif bid == "post-comment-toggle":
            self._toggle_comments()
        elif bid == "post-comment-btn":
            inp = self.query_one("#post-comment-input", Input)
            content = inp.value.strip()
            if content:
                self._create_comment(content)
                inp.value = ""

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "post-comment-input":
            content = event.value.strip()
            if content:
                self._create_comment(content)
                event.input.value = ""

    def _toggle_comments(self):
        self._comments_visible = not self._comments_visible
        area = self.query_one("#post-comments-area", Vertical)
        btn = self.query_one("#post-comment-toggle", Button)
        cc = self.post_data.get("comment_count", 0)
        if self._comments_visible:
            area.remove_class("comments-area-hidden")
            btn.label = f"{self.app.lang['hide_comments']} ({cc})"
            self._load_comments()
        else:
            area.add_class("comments-area-hidden")
            btn.label = f"{self.app.lang['view_comments']} ({cc})"

    @work(exclusive=False)
    async def _load_comments(self):
        container = self.query_one("#post-comments-list", ScrollableContainer)
        await container.remove_children()
        try:
            comments = await self.app.api.get_comments(self.post_data["id"])
            if not comments:
                await container.mount(Static(self.app.lang["comment_empty"], classes="info"))
            for c in comments:
                await container.mount(CommentWidget(c, classes="comment-item"))
        except Exception as e:
            await container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    @work(exclusive=False)
    async def _create_comment(self, content: str):
        try:
            await self.app.api.create_comment(self.post_data["id"], content)
            self.post_data["comment_count"] = self.post_data.get("comment_count", 0) + 1
            self._load_comments()
            cc = self.post_data.get("comment_count", 0)
            self.query_one("#post-comment-toggle", Button).label = f"{self.app.lang['hide_comments']} ({cc})"
        except Exception as e:
            self.notify(f"{self.app.lang['comment_create_error'].format(e)}", severity="error")

    def _confirm_delete(self):
        """Pede confirmação antes de excluir o post."""
        from client.screens.confirm_screen import ConfirmScreen

        def handle(result):
            if result:
                self._do_delete()

        self.app.push_screen(
            ConfirmScreen(self.app.lang["post_delete_confirm_title"], self.app.lang["post_delete_confirm_message"]),
            callback=handle,
        )

    @work
    async def _do_delete(self):
        """Exclui o post via API e remove o widget."""
        try:
            await self.app.api.delete_post(self.post_data["id"])
            self.remove()
            self.notify(self.app.lang["post_deleted"])
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    @work(exclusive=False)
    async def _toggle_like(self):
        """Curtir/descurtir post."""
        try:
            post_id = self.post_data["id"]
            is_liked = self.post_data.get("is_liked", False)
            if is_liked:
                await self.app.api.unlike_post(post_id)
                self.post_data["is_liked"] = False
                self.post_data["like_count"] = max(0, self.post_data.get("like_count", 0) - 1)
            else:
                await self.app.api.like_post(post_id)
                self.post_data["is_liked"] = True
                self.post_data["like_count"] = self.post_data.get("like_count", 0) + 1
            like_count = self.post_data["like_count"]
            new_is_liked = self.post_data["is_liked"]
            btn = self.query_one("#post-like-btn", Button)
            btn.label = f"{'♥' if not new_is_liked else '♥'} {like_count}"
            btn.classes = "post-like-btn liked" if new_is_liked else "post-like-btn"
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    @work(exclusive=False)
    async def _render_media(self):
        """Baixa e renderiza mídia do post via chafa."""
        self._media_rendered = True
        file_id = self.post_data["file_id"]
        file_name = self.post_data.get("file_name", f"file_{file_id}")
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "jpg"
        video_exts = ("mp4", "wmv", "webm", "mov", "avi", "mkv")
        is_video = ext in video_exts
        is_gif = ext == "gif"
        tmp = None
        cleanup_files = []
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}")
            await self.app.api.download_file(file_id, tmp.name)
            tmp.close()
            if os.path.getsize(tmp.name) < 50:
                return

            if is_gif:
                await self._render_gif_animation(tmp.name)
            elif is_video:
                thumb = await self._extract_frame(tmp.name)
                if thumb:
                    cleanup_files.append(thumb)
                    await self._render_static(thumb)
                else:
                    await self._render_static(tmp.name)
            else:
                await self._render_static(tmp.name)
        except Exception:
            pass
        finally:
            for p in cleanup_files + ([tmp.name] if tmp else []):
                if os.path.exists(p):
                    os.unlink(p)

    async def _extract_frame(self, video_path: str) -> str | None:
        """Extrai um frame de vídeo como thumbnail usando ffmpeg."""
        thumb = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        thumb.close()
        vproc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", video_path, "-vframes", "1",
            "-q:v", "2", thumb.name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await vproc.wait()
        if os.path.getsize(thumb.name) > 100:
            return thumb.name
        os.unlink(thumb.name)
        return None

    async def _render_static(self, img_path: str):
        """Renderiza imagem estática com chafa."""
        chafa_args = ["chafa", img_path, "--format", "symbols", "--symbols", "block", "--size", "36x12", "--animate", "off"]
        proc = await asyncio.create_subprocess_exec(
            *chafa_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if stdout and self.is_mounted:
            preview = Text.from_ansi(stdout.decode())
            combined = Text()
            combined.append_text(self._post_base or Text())
            combined.append("\n")
            combined.append_text(preview)
            self._content_st.update(combined)

    async def _render_gif_animation(self, gif_path: str):
        """Renderiza GIF animado: extrai frames com ffmpeg, processa com chafa em paralelo."""
        frame_dir = tempfile.mkdtemp()
        try:
            vproc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", gif_path,
                "-vsync", "vfr", f"{frame_dir}/frame_%04d.png",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await vproc.wait()
            frame_files = sorted(
                f for f in os.listdir(frame_dir) if f.startswith("frame_")
            )
            if not frame_files:
                return
            max_frames = min(len(frame_files), 30)
            frame_paths = [os.path.join(frame_dir, frame_files[i]) for i in range(max_frames)]

            # Processa todos os frames em paralelo com chafa
            async def _render_frame(fp):
                chafa_args = ["chafa", fp, "--format", "symbols", "--symbols", "block", "--size", "36x12", "--animate", "off"]
                proc = await asyncio.create_subprocess_exec(
                    *chafa_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, _ = await proc.communicate()
                if out and self.is_mounted:
                    frame_text = Text()
                    frame_text.append_text(self._post_base or Text())
                    frame_text.append("\n")
                    frame_text.append_text(Text.from_ansi(out.decode()))
                    return frame_text
                return None

            results = await asyncio.gather(*[_render_frame(fp) for fp in frame_paths])
            self._anim_frames = [r for r in results if r is not None]

            if len(self._anim_frames) > 1 and self.is_mounted:
                self._anim_idx = 0
                self._content_st.update(self._anim_frames[0])

                def advance():
                    self._anim_idx = (self._anim_idx + 1) % len(self._anim_frames)
                    if self.is_mounted:
                        self._content_st.update(self._anim_frames[self._anim_idx])

                self._anim_timer = self.set_interval(1 / 10, advance)
        finally:
            for f in os.listdir(frame_dir):
                os.unlink(os.path.join(frame_dir, f))
            os.rmdir(frame_dir)

    @work(exclusive=False)
    async def _download_media(self):
        """Abre diálogo para salvar mídia do post localmente."""
        file_id = self.post_data["file_id"]
        file_name = self.post_data.get("file_name", f"file_{file_id}")
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        try:
            save_path = filedialog.asksaveasfilename(
                initialdir=os.path.expanduser("~/Downloads"),
                initialfile=file_name,
                title="Salvar mídia",
                parent=root,
            )
        finally:
            root.destroy()
        if not save_path:
            return
        try:
            await self.app.api.download_file(file_id, save_path)
            self.notify(f"{self.app.lang['downloaded_prefix']}: {file_name}", timeout=5)
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error", timeout=5)


class FileItemWidget(Static):
    """Widget que exibe um arquivo enviado na aba Arquivos."""

    def __init__(self, file_data: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.file_data = file_data

    def on_mount(self):
        f = self.file_data
        size_kb = f["size"] / 1024
        t = Text()
        t.append(f["original_name"], style=self.app.theme_colors["text"])
        t.append(f"  ({size_kb:.1f} KB)", style="#475569")
        t.append(f"\nTipo: {f['content_type']}", style="#64748b")
        t.append(f"  Enviado: {f['uploaded_at'][:19]}", style="#475569")
        self.update(t)

    def on_click(self):
        self._download()

    @work(exclusive=False)
    async def _download(self):
        """Abre diálogo para salvar arquivo localmente."""
        f = self.file_data
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        try:
            save_path = filedialog.asksaveasfilename(
                initialdir=os.path.expanduser("~/Downloads"),
                initialfile=f["original_name"],
                title="Salvar arquivo",
                parent=root,
            )
        finally:
            root.destroy()
        if not save_path:
            return
        try:
            await self.app.api.download_file(f["id"], save_path)
            self.notify(f"{self.app.lang['downloaded_prefix']}: {f['original_name']}", timeout=5)
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error", timeout=5)


class ChatMsgWidget(Static):
    """Widget de mensagem do chat público."""

    def __init__(self, msg: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.msg_data = msg

    def on_mount(self):
        m = self.msg_data
        ago = _ago(m["created_at"], self.app.lang)
        t = Text()
        if m.get("is_anonymous"):
            t.append(f"{self.app.lang['chat_anonymous']}  ", style="bold #475569")
        else:
            t.append(f"@{m['username']}  ", style="bold #06b6d4")
        t.append(f"· {ago}", style="#475569")
        t.append(f"\n{m['content']}", style=self.app.theme_colors["text"])
        self.update(t)

    def on_click(self):
        # Clique no username abre perfil (exceto anônimo)
        if not self.msg_data.get("is_anonymous"):
            username = self.msg_data.get("username", "")
            if username:
                self.screen._open_profile(username)


class CommentWidget(Static):
    """Widget de um comentário em um post."""

    def __init__(self, comment: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.comment_data = comment

    def on_mount(self):
        c = self.comment_data
        ago = _ago(c.get("created_at", ""), self.app.lang)
        t = Text()
        t.append(f"@{c['username']}  ", style="bold #06b6d4")
        t.append(f"· {ago}", style="#475569")
        t.append(f"\n{c.get('content', '')}", style=self.app.theme_colors["text"])
        self.update(t)


class NotificationWidget(Static):
    """Widget de notificação."""

    def __init__(self, notif: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.notif_data = notif

    def on_mount(self):
        n = self.notif_data
        lang = self.app.lang
        ago = _ago(n.get("created_at", ""), lang)
        t = Text()
        actor = n.get("actor_username", lang["notif_unknown_actor"])
        t.append(f"@{actor}  ", style="bold #7c3aed" if not n.get("read") else "#64748b")
        t.append(f"· {ago}", style="#475569")
        notif_type = n.get("type", "")
        verb = {
            "like": lang["notif_like"],
            "follow": lang["notif_follow"],
            "comment": lang["notif_comment"],
            "mention": lang["notif_mention"],
        }.get(notif_type, "")
        t.append(f"\n{verb}", style=self.app.theme_colors["text"])
        preview = n.get("post_preview", "")
        if preview:
            preview = (preview[:60] + "…") if len(preview) > 60 else preview
            t.append(f"\n  \"{preview}\"", style="#64748b")
        self.t = t
        self.update(t)
        if not n.get("read"):
            self.styles.background = "#16162a"

    def on_click(self):
        if not self.notif_data.get("read"):
            self._mark_read()

    @work(exclusive=False)
    async def _mark_read(self):
        try:
            await self.app.api.mark_notification_read(self.notif_data["id"])
            self.notif_data["read"] = True
            self.styles.background = ""
            self.update(self.t)
            self.screen._refresh_notification_badge()
        except Exception:
            pass


class DmMsgWidget(Static):
    """Widget de mensagem de DM (conversa privada)."""

    def __init__(self, msg: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.msg_data = msg

    def on_mount(self):
        m = self.msg_data
        ago = _ago(m["created_at"], self.app.lang)
        t = Text()
        is_me = m.get("is_me", False)
        t.append(f"{'✎' if is_me else '@'}{m['sender_username']}  ", style="bold #06b6d4" if not is_me else "bold #7c3aed")
        t.append(f"· {ago}", style="#475569")
        t.append(f"\n{m['content']}", style=self.app.theme_colors["text"])
        if m.get("dm_file_name"):
            t.append(f"\n📎 {m['dm_file_name']}", style="#64748b")
        elif m.get("file_name"):
            t.append(f"\n📎 {m['file_name']}", style="#64748b")
        self.update(t)

    def on_click(self):
        # Clique baixa arquivo anexado
        if self.msg_data.get("dm_file_id"):
            self._download_dm_file()
        elif self.msg_data.get("file_id"):
            self._download_media()

    @work(exclusive=False)
    async def _download_dm_file(self):
        """Baixa arquivo anexado à DM."""
        file_id = self.msg_data["dm_file_id"]
        file_name = self.msg_data.get("dm_file_name", f"file_{file_id}")
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        try:
            save_path = filedialog.asksaveasfilename(
                initialdir=os.path.expanduser("~/Downloads"),
                initialfile=file_name,
                title="Salvar arquivo",
                parent=root,
            )
        finally:
            root.destroy()
        if not save_path:
            return
        try:
            await self.app.api.download_dm_file(file_id, save_path)
            self.notify(f"{self.app.lang['downloaded_prefix']}: {file_name}", timeout=5)
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error", timeout=5)

    @work(exclusive=False)
    async def _download_media(self):
        """Baixa mídia anexada à DM."""
        file_id = self.msg_data["file_id"]
        file_name = self.msg_data.get("file_name", f"file_{file_id}")
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        try:
            save_path = filedialog.asksaveasfilename(
                initialdir=os.path.expanduser("~/Downloads"),
                initialfile=file_name,
                title="Salvar arquivo",
                parent=root,
            )
        finally:
            root.destroy()
        if not save_path:
            return
        try:
            await self.app.api.download_file(file_id, save_path)
            self.notify(f"{self.app.lang['downloaded_prefix']}: {file_name}", timeout=5)
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error", timeout=5)


class DmConvItem(Static):
    """Widget de conversa na lista de DMs."""

    def __init__(self, conv_data: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conv_data = conv_data

    def on_mount(self):
        c = self.conv_data
        t = Text()
        t.append(f"@{c['other_username']}", style="bold #06b6d4")
        if c.get("other_display_name"):
            t.append(f"\n{c['other_display_name']}", style="#64748b")
        if c.get("last_message"):
            t.append(f"\n{c['last_message'][:60]}", style="#475569")
        self.update(t)

    async def on_click(self):
        # Seleciona conversa e carrega mensagens
        self.screen._current_conv_id = self.conv_data["id"]
        await self.screen._load_dm_messages(self.conv_data["id"])


class GroupConvItem(Static):
    """Widget de grupo na lista de grupos."""

    def __init__(self, group_data: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_data = group_data

    def on_mount(self):
        g = self.group_data
        t = Text()
        t.append(f"{self.app.lang['group_header'].format(g['name'])}", style="bold #7c3aed")
        t.append(f"\n{g['member_count']} membros", style="#64748b")
        if g.get("last_message"):
            sender = g.get("last_sender_username", "")
            t.append(f"\n{sender}: {g['last_message'][:60]}", style="#475569")
        self.update(t)

    async def on_click(self):
        self.screen._current_group_id = self.group_data["id"]
        await self.screen._load_group_messages(self.group_data["id"])


class UserResultWidget(Static):
    """Widget de resultado de busca de usuário."""

    def __init__(self, user: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_data = user

    def on_mount(self):
        u = self.user_data
        t = Text()
        t.append(f"@{u['username']}", style="bold #7c3aed")
        if u.get("display_name"):
            t.append(f" ({u['display_name']})", style="#64748b")
        t.append(f"\n{u.get('bio', '')}", style=self.app.theme_colors["text"])
        t.append(f"\nPosts: {u['post_count']}  Seguidores: {u['follower_count']}", style="#475569")
        self.update(t)

    def on_click(self):
        username = self.user_data.get("username", "")
        if username:
            self.screen._open_profile(username)


class MainScreen(Screen):
    """Tela principal do hexfeed com sistema de abas próprio."""

    TAB_NAMES = ["feed", "chat", "dms", "files", "search", "notifications", "profile", "settings"]

    BINDINGS = [
        Binding("q", "quit", "Sair"),
        Binding("escape", "logout", "Sair"),
        Binding("r", "refresh", "Atualizar"),
        Binding("ctrl+p", "focus_feed_input", "Novo Post"),
        Binding("1", "tab_feed", "1 Feed"),
        Binding("2", "tab_chat", "2 Chat"),
        Binding("3", "tab_dms", "3 DMs"),
        Binding("4", "tab_files", "4 Arqs"),
        Binding("5", "tab_search", "5 Busca"),
        Binding("6", "tab_notifications", "6 Notif"),
        Binding("7", "tab_profile", "7 Perfil"),
        Binding("8", "tab_settings", "8 Config"),
        Binding("ctrl+tab", "next_tab", "Próx Aba"),
        Binding("ctrl+shift+tab", "prev_tab", "Ant Aba"),
        Binding("j", "scroll_down", "↓"),
        Binding("k", "scroll_up", "↑"),
        Binding("n", "new_post", "Novo Post"),
        Binding("slash", "focus_search", "Buscar"),
    ]

    CSS = """
    MainScreen { background: #0a0a0f; }

    #app-header { height: 3; dock: top; background: #111118; border-bottom: solid #7c3aed; padding: 0 2; }
    #header-title { text-style: bold; color: #7c3aed; content-align: left middle; width: 60%; }
    #header-user { color: #64748b; content-align: right middle; width: 40%; }

    #main-content { height: 1fr; }

    #tab-bar { height: 3; background: #111118; }
    .tab-btn { width: auto; padding: 0 2; background: transparent; border: none; color: #64748b; }
    .tab-btn:hover { color: #a78bfa; }
    .tab-btn.-active { color: #7c3aed; text-style: bold; }
    .tab-btn.-has-unread { color: #f59e0b; text-style: bold; }
    #tab-spacer { width: 1fr; height: 100%; }
    .tab-content { display: none; height: 1fr; }
    .tab-content.-active { display: block; }

    #feed-list { height: 1fr; overflow-y: auto; }
    #feed-input-box { dock: bottom; height: auto; background: #111118; border-top: solid #1e293b; padding: 1 2; }
    #feed-input { width: 1fr; border: solid #1e293b; }
    #feed-attach { width: auto; min-width: 4; padding: 0 1; }
    PostWidget { padding: 1 2; border-bottom: solid #1e293b; height: auto; }
    PostWidget:hover { background: #16162a; }
    #post-header { height: auto; margin-bottom: 0; }
    #post-header-text { width: 1fr; }
    .post-menu-btn { width: auto; min-width: 3; padding: 0 1; background: transparent; border: none; color: #64748b; }
    .post-menu-btn:hover { color: #ef4444; }
    #post-content { height: auto; }
    #post-actions { height: auto; }
    .post-like-btn { width: auto; background: transparent; border: none; color: #475569; padding: 0 1; }
    .post-like-btn:hover { color: #ef4444; }
    .post-like-btn.liked { color: #ef4444; }
    .comment-toggle { width: auto; background: transparent; border: none; color: #06b6d4; padding: 0 2; }
    .comment-toggle:hover { color: #7c3aed; }
    #post-comments-area { margin: 0 0 0 2; }
    .comments-area-hidden { display: none; }
    #post-comments-list { height: auto; max-height: 20; overflow-y: auto; border-left: solid #1e293b; padding: 0 1; }
    #post-comment-input-box { height: auto; border-top: solid #1e293b; padding: 0 1; }
    #post-comment-input { width: 1fr; }
    #post-comment-btn { width: auto; }
    .comment-item { padding: 0 1; border-bottom: dashed #1a1a2e; height: auto; }

    #notif-list { height: 1fr; overflow-y: auto; }
    #notif-toolbar { dock: top; height: 3; background: #111118; border-bottom: solid #1e293b; padding: 0 1; }
    NotificationWidget { padding: 1 2; border-bottom: solid #1e293b; height: auto; }
    NotificationWidget:hover { background: #16162a; }

    #chat-messages { height: 1fr; overflow-y: auto; }
    #chat-input-box { dock: bottom; height: auto; background: #111118; border-top: solid #1e293b; padding: 1 1; }
    #chat-input { width: 1fr; }
    .chat-msg { padding: 1 2; border-bottom: dashed #1a1a2e; height: auto; }

    #profile-header { height: auto; margin: 1; }
    #profile-info { height: auto; padding: 2; border: solid #1e293b; background: #111118; width: 1fr; }
    #profile-avatar-art { height: auto; width: auto; }
    #profile-posts { height: 1fr; overflow-y: auto; }
    #profile-edit-box { height: auto; padding: 1 2; border: solid #06b6d4; background: #111118; margin: 1; }

    #search-results { height: 1fr; overflow-y: auto; }
    #search-toolbar { dock: top; height: auto; background: #111118; border-bottom: solid #1e293b; padding: 1 2; }
    #search-toolbar > #search-input { width: 1fr; }
    #search-toolbar > .toolbar-btn { width: auto; min-width: 4; margin: 0 0 0 1; }
    #search-toolbar > #search-filter-toggle { width: auto; min-width: 3; margin: 0 0 0 1; border: none; background: #1e293b; color: #64748b; padding: 0 1; }
    #search-toolbar > #search-filter-toggle.-open { color: #06b6d4; background: #1e293b; }
    #search-filters { layout: vertical; height: auto; background: #0f0f16; border-bottom: solid #1e293b; padding: 1 2; }
    #search-filters.-hidden { display: none; }
    #sf-row-types, #sf-row-dates { layout: horizontal; height: auto; }
    #sf-row-types { margin: 0 0 1 0; }
    .filter-btn { width: auto; min-width: 1; margin: 0 1 0 0; padding: 0 1; border: none; background: #1e293b; color: #e2e8f0; }
    .filter-btn.on { background: #7c3aed; color: #e2e8f0; }
    .filter-input-username { width: 22; }
    Horizontal#sf-row-dates > Input { width: 1fr; border: none; background: #1e293b; color: #e2e8f0; }
    Horizontal#sf-row-dates > Static.date-sep { width: auto; }
    .date-sep { color: #64748b; padding: 0 0; }
    .search-section-header { padding: 1 2; background: #1a1a2e; border-bottom: solid #1e293b; }

    #files-list { height: 1fr; overflow-y: auto; }
    #files-toolbar { dock: top; height: 3; background: #111118; border-bottom: solid #1e293b; padding: 0 2; }
    .file-item { padding: 1 2; border-bottom: solid #1e293b; height: auto; }

    #dms-layout { height: 1fr; }
    #dms-conv-list { width: 28; min-width: 20; height: 1fr; border-right: solid #1e293b; overflow-y: auto; }
    #dms-conv-list:focus-within { border-right: solid #7c3aed; }
    #dms-right { height: 1fr; }
    #dms-messages { height: 1fr; overflow-y: auto; }
    #dms-input-box { dock: bottom; height: auto; background: #111118; border-top: solid #1e293b; padding: 1 1; }
    #dms-input { width: 1fr; }
    #dms-toolbar { height: 3; background: #111118; border-bottom: solid #1e293b; padding: 0 2; }
    #dms-toolbar .-active { color: #7c3aed; text-style: bold; }
    #dms-header { height: 3; background: #111118; border-bottom: solid #1e293b; padding: 0 2; content-align: center middle; color: #7c3aed; text-style: bold; }
    #dms-add-member { display: none; }
    #dms-remove-member { display: none; }
    .dms-conv-item { padding: 1 2; border-bottom: dashed #1a1a2e; height: auto; }
    .dms-conv-item:hover { background: #16162a; }
    .dms-conv-item.-active { background: #1e1e3a; border-left: solid #7c3aed; }
    .dms-info { color: #64748b; padding: 1 2; }
    DmMsgWidget { padding: 1 2; border-bottom: dashed #1a1a2e; height: auto; }

    #config-box { padding: 2; height: auto; border: solid #1e293b; background: #111118; margin: 1; }

    .edit-title { color: #06b6d4; text-style: bold; }
    .error { color: #ef4444; }
    .success { color: #22c55e; }
    .info { color: #64748b; }

    .toolbar-btn { width: auto; min-width: 12; margin: 0 1; }
    Input { margin: 0; }
    Button { margin: 0 1; }
    #feed-toolbar, #profile-toolbar, #files-toolbar {
        height: 3; background: #111118; border-bottom: solid #1e293b; padding: 0 1;
    }
    #feed-toolbar { dock: top; }
    #profile-toolbar { dock: top; height: 3; background: #111118; border-bottom: solid #1e293b; padding: 0 1; }

    #chat-anon-toggle { width: auto; background: transparent; color: #64748b; border: solid #1e293b; padding: 0 1; }
    #chat-anon-toggle.active { background: #7c3aed; color: #e2e8f0; border: solid #7c3aed; }

    #config-lang-box { height: auto; padding: 1; margin: 0 0 1 0; }
    #config-theme-box { height: auto; padding: 1; margin: 0 0 1 0; }
    .cfg-lang-btn { width: auto; min-width: 12; background: #1e293b; color: #e2e8f0; }
    .cfg-lang-btn.-primary { background: #7c3aed; color: #e2e8f0; }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="app-header"):
            yield Static(self.app.lang["app_title"], id="header-title")
            yield Static("", id="header-user")

        with Container(id="main-content"):
            # Barra de abas: feed, chat, arquivos (esquerda) | busca, perfil, config (direita)
            with Horizontal(id="tab-bar"):
                yield Button(self.app.lang["tab_feed"], id="tab-feed", classes="tab-btn -active")
                yield Button(self.app.lang["tab_chat"], id="tab-chat", classes="tab-btn")
                yield Button(self.app.lang["tab_dms"], id="tab-dms", classes="tab-btn")
                yield Button(self.app.lang["tab_files"], id="tab-files", classes="tab-btn")
                yield Static(id="tab-spacer")
                yield Button(self.app.lang["tab_search"], id="tab-search", classes="tab-btn")
                yield Button(self.app.lang["tab_notifications"], id="tab-notifications", classes="tab-btn")
                yield Button(self.app.lang["tab_profile"], id="tab-profile", classes="tab-btn")
                yield Button(self.app.lang["tab_settings"], id="tab-settings", classes="tab-btn")

            # ── PAINÉIS DE CONTEÚDO ──────────────────────────────────

            # Feed
            with Vertical(id="content-feed", classes="tab-content -active"):
                with Horizontal(id="feed-toolbar"):
                    yield Button(self.app.lang["feed_following"], id="feed-following", classes="toolbar-btn")
                    yield Button(self.app.lang["feed_global"], id="feed-global", classes="toolbar-btn")
                    yield Button("↻", id="feed-refresh", classes="toolbar-btn")
                yield ScrollableContainer(id="feed-list")
                with Horizontal(id="feed-input-box"):
                    yield Input(placeholder=self.app.lang["feed_input_placeholder"], id="feed-input")
                    yield Button("📷", id="feed-attach", classes="toolbar-btn")

            # Chat público
            with Vertical(id="content-chat", classes="tab-content"):
                yield ScrollableContainer(id="chat-messages")
                with Horizontal(id="chat-input-box"):
                    yield Input(placeholder=self.app.lang["chat_input_placeholder"], id="chat-input")
                    yield Button(self.app.lang["chat_anon_toggle"], id="chat-anon-toggle")
                    yield Button(self.app.lang["send"], id="chat-send", variant="primary", classes="toolbar-btn")
                    yield Button(self.app.lang["chat_refresh"], id="chat-refresh", classes="toolbar-btn")

            # DMs (mensagens privadas + grupos)
            with Vertical(id="content-dms", classes="tab-content"):
                with Horizontal(id="dms-toolbar"):
                    yield Button(self.app.lang["dm_mode_conv"], id="dms-mode", classes="toolbar-btn")
                    yield Button(self.app.lang["dm_new"], id="dms-new", classes="toolbar-btn")
                    yield Button(self.app.lang["dm_clear"], id="dms-clear", classes="toolbar-btn")
                    yield Button(self.app.lang["group_add_member"], id="dms-add-member", classes="toolbar-btn")
                    yield Button(self.app.lang["group_remove_member"], id="dms-remove-member", classes="toolbar-btn")
                    yield Button("↻", id="dms-refresh", classes="toolbar-btn")
                with Horizontal(id="dms-layout"):
                    yield ScrollableContainer(id="dms-conv-list")
                    with Vertical(id="dms-right"):
                        yield Static("", id="dms-header")
                        yield ScrollableContainer(id="dms-messages")
                        with Horizontal(id="dms-input-box"):
                            yield Input(placeholder=self.app.lang["dm_input_placeholder"], id="dms-input")
                            yield Button("📎", id="dms-attach", classes="toolbar-btn")
                            yield Button(self.app.lang["send"], id="dms-send", variant="primary", classes="toolbar-btn")

            # Arquivos
            with Vertical(id="content-files", classes="tab-content"):
                with Horizontal(id="files-toolbar"):
                    yield Button(self.app.lang["files_upload"], id="files-upload", classes="toolbar-btn")
                    yield Button("↻", id="files-refresh", classes="toolbar-btn")
                yield ScrollableContainer(id="files-list")

            # Busca
            with Vertical(id="content-search", classes="tab-content"):
                with Horizontal(id="search-toolbar"):
                    yield Input(placeholder=self.app.lang["search_placeholder"], id="search-input")
                    yield Button("🔍", id="search-go", variant="primary", classes="toolbar-btn")
                    yield Button("▾", id="search-filter-toggle")
                with Vertical(id="search-filters", classes="-hidden"):
                    with Horizontal(id="sf-row-types"):
                        yield Button(self.app.lang["search_filter_users"], id="sf-users", classes="filter-btn on")
                        yield Button(self.app.lang["search_filter_posts"], id="sf-posts", classes="filter-btn on")
                        yield Button(self.app.lang["search_filter_files"], id="sf-files", classes="filter-btn")
                        yield Input(placeholder=self.app.lang["search_filter_from"], id="sf-username", classes="filter-input-username")
                    with Horizontal(id="sf-row-dates"):
                        day_first = self.app.language.startswith("pt")
                        fmt = "DD/MM/AA" if day_first else "MM/DD/AA"
                        yield Input(placeholder=fmt, id="sf-date-from")
                        yield Static(self.app.lang["date_separator"], classes="date-sep")
                        yield Input(placeholder=fmt, id="sf-date-to")
                yield ScrollableContainer(id="search-results")

            # Perfil (próprio)
            with Vertical(id="content-profile", classes="tab-content"):
                with Horizontal(id="profile-header"):
                    yield Static("", id="profile-avatar-art")
                    yield Vertical(id="profile-info")
                with Vertical(id="profile-edit-box"):
                    yield Static(self.app.lang["edit_profile_title"], classes="edit-title")
                    yield Input(placeholder=self.app.lang["display_name_placeholder"], id="profile-display-name")
                    yield Input(placeholder=self.app.lang["bio_placeholder"], id="profile-bio")
                    yield Input(placeholder=self.app.lang["email_placeholder"], id="profile-email")
                    yield Button(self.app.lang["profile_photo"], id="profile-avatar", classes="toolbar-btn")
                    with Horizontal():
                        yield Button(self.app.lang["save"], id="profile-edit-save", variant="primary")
                        yield Button(self.app.lang["cancel"], id="profile-edit-cancel")
                with Horizontal(id="profile-toolbar"):
                    yield Button(self.app.lang["edit"], id="profile-edit-toggle", classes="toolbar-btn")
                    yield Button(self.app.lang["view_blocks"], id="profile-blocks", classes="toolbar-btn")
                    yield Button("↻", id="profile-refresh", classes="toolbar-btn")
                yield ScrollableContainer(id="profile-posts")

            # Configurações
            with Vertical(id="content-settings", classes="tab-content"):
                with Vertical(id="config-box"):
                    yield Static(self.app.lang["settings_title"], classes="edit-title")
                    yield Static("", id="config-info")
                    yield Static("", id="config-status")

                    yield Static(self.app.lang["language_label"], classes="edit-title")
                    with Horizontal(id="config-lang-box"):
                        yield Button(self.app.lang["lang_pt_br"], id="cfg-lang-pt-BR", classes="cfg-lang-btn")
                        yield Button(self.app.lang["lang_en"], id="cfg-lang-en", classes="cfg-lang-btn")
                        yield Button(self.app.lang["lang_es"], id="cfg-lang-es", classes="cfg-lang-btn")

                    yield Static(self.app.lang["settings_theme_label"], classes="edit-title")
                    with Horizontal(id="config-theme-box"):
                        yield Button(self.app.lang["theme_dark"], id="cfg-theme-dark", classes="cfg-lang-btn")
                        yield Button(self.app.lang["theme_light"], id="cfg-theme-light", classes="cfg-lang-btn")

                with Horizontal():
                    yield Button(self.app.lang["logout"], id="config-logout", variant="primary")

            # Notificações
            with Vertical(id="content-notifications", classes="tab-content"):
                with Horizontal(id="notif-toolbar"):
                    yield Button(self.app.lang["notifications_all_read"], id="notif-read-all", classes="toolbar-btn")
                    yield Button("\u21bb", id="notif-refresh", classes="toolbar-btn")
                yield ScrollableContainer(id="notif-list")

    def on_mount(self):
        self.query_one("#profile-edit-box", Vertical).display = False
        self._chat_anonymous = False
        self._pending_media_id = None
        self._conversations = []
        self._current_conv_id = None
        self._groups = []
        self._current_group_id = None
        self._dm_mode = "conv"
        self._feed_mode = "following"
        self._last_chat_id = 0
        self._last_dm_id = 0
        self._last_group_msg_id = 0
        self._update_header()
        self.set_timer(0.1, self._initial_load)
        self.set_interval(30, self._poll_notification_badge)
        self.set_interval(5, self._poll_messages)

    # ── TROCAR ABA ──────────────────────────────────────────────────

    def _switch_tab(self, tab_name: str):
        """Ativa uma aba: mostra conteúdo e destaca botão."""
        for content in self.query(".tab-content"):
            content.remove_class("-active")
        new_content = self.query_one(f"#content-{tab_name}", Vertical)
        new_content.add_class("-active")
        for btn in self.query(".tab-btn"):
            btn.remove_class("-active")
        self.query_one(f"#tab-{tab_name}", Button).add_class("-active")
        new_content.refresh(layout=True)

    @work(exclusive=False)
    async def _refresh_notification_badge(self):
        """Busca apenas o contador de não lidas e atualiza o badge."""
        try:
            data = await self.app.api.get_notifications(limit=1)
            unread = data.get("unread_count", 0) if isinstance(data, dict) else 0
            self._update_notification_badge(unread)
        except Exception:
            pass

    def _poll_notification_badge(self):
        """Polling periódico do badge de notificações."""
        self._refresh_notification_badge()

    def _initial_load(self):
        """Carrega dados iniciais (exceto feed, que é só manual)."""
        self._refresh_notification_badge()

    def _update_header(self):
        """Atualiza o nome do usuário no cabeçalho."""
        if self.app.client_state.user:
            username = self.app.client_state.user.get("username", "")
            self.query_one("#header-user", Static).update(f"@{username}")

    # ── CARREGAMENTO DE DADOS ──────────────────────────────────────

    @work(exclusive=True)
    async def _load_feed(self, mode: str | None = None):
        """Carrega lista de posts (following ou global)."""
        if mode:
            self._feed_mode = mode
        container = self.query_one("#feed-list", ScrollableContainer)
        await container.remove_children()
        try:
            posts = await self.app.api.get_feed(limit=30) if self._feed_mode == "following" else await self.app.api.get_global_feed(limit=30)
            if not posts:
                await container.mount(Static(self.app.lang["feed_empty"], classes="info"))
            for p in posts:
                await container.mount(PostWidget(p))
        except Exception as e:
            await container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))


    @work(exclusive=False)
    async def _load_chat(self):
        """Carrega mensagens do chat público."""
        container = self.query_one("#chat-messages", ScrollableContainer)
        await container.remove_children()
        try:
            msgs = await self.app.api.get_chat(limit=50)
            if not msgs:
                await container.mount(Static(self.app.lang["chat_empty"], classes="info"))
            self._last_chat_id = 0
            for m in msgs:
                await container.mount(ChatMsgWidget(m, classes="chat-msg"))
                mid = m.get("id", 0)
                if mid > self._last_chat_id:
                    self._last_chat_id = mid
            container.scroll_end(animate=False)
        except Exception as e:
            await container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    def _poll_messages(self):
        active = self._get_active_tab()
        if active == "chat":
            self._poll_chat_messages()
        elif active == "dms":
            if self._dm_mode == "group" and self._current_group_id:
                self._poll_group_messages()
            elif not self._dm_mode == "group" and self._current_conv_id:
                self._poll_dm_messages()

    @work(exclusive=False)
    async def _poll_chat_messages(self):
        try:
            container = self.query_one("#chat-messages", ScrollableContainer)
            msgs = await self.app.api.get_chat(limit=20)
            new_count = 0
            for m in msgs:
                if m.get("id", 0) > self._last_chat_id:
                    await container.mount(ChatMsgWidget(m, classes="chat-msg"))
                    self._last_chat_id = m["id"]
                    new_count += 1
            if new_count:
                container.scroll_end(animate=False)
        except Exception:
            pass

    @work(exclusive=False)
    async def _poll_dm_messages(self):
        try:
            container = self.query_one("#dms-messages", ScrollableContainer)
            msgs = await self.app.api.get_dm_messages(self._current_conv_id)
            me = (await self.app.api.get_me())["username"]
            new_count = 0
            for m in msgs:
                if m.get("id", 0) > self._last_dm_id:
                    m["is_me"] = m["sender_username"] == me
                    await container.mount(DmMsgWidget(m))
                    self._last_dm_id = m["id"]
                    new_count += 1
            if new_count:
                container.scroll_end(animate=False)
        except Exception:
            pass

    @work(exclusive=False)
    async def _poll_group_messages(self):
        try:
            container = self.query_one("#dms-messages", ScrollableContainer)
            msgs = await self.app.api.get_group_messages(self._current_group_id)
            me = (await self.app.api.get_me())["username"]
            new_count = 0
            for m in msgs:
                if m.get("id", 0) > self._last_group_msg_id:
                    m["is_me"] = m["sender_username"] == me
                    await container.mount(DmMsgWidget(m))
                    self._last_group_msg_id = m["id"]
                    new_count += 1
            if new_count:
                container.scroll_end(animate=False)
        except Exception:
            pass

    @work(exclusive=False)
    async def _load_profile(self):
        """Carrega perfil do usuário logado: dados, avatar e posts."""
        display = self.query_one("#profile-info", Vertical)
        edit_box = self.query_one("#profile-edit-box", Vertical)
        avatar_art = self.query_one("#profile-avatar-art", Static)
        edit_box.display = False
        avatar_art.display = False
        self.query_one("#profile-edit-toggle", Button).label = self.app.lang["edit"]
        try:
            user = await self.app.api.get_me()
            posts = await self.app.api.get_user_posts(user["username"], limit=20)
            t = Text()
            t.append(f"@{user['username']}", style="bold #7c3aed")
            if user.get("display_name"):
                t.append(f"\n{user['display_name']}", style="#06b6d4")
            t.append(f"\n{user.get('bio', '')}", style=self.app.theme_colors["text"])
            if user.get("avatar_path"):
                t.append(f"\n{self.app.lang['profile_has_avatar']}", style="#22c55e")
            else:
                t.append(f"\n{self.app.lang['profile_no_avatar']}", style="#64748b")
            t.append(f"\n{self.app.lang['stats_posts'].format(user['post_count'], user['following_count'], user['follower_count'])}", style="#64748b")
            t.append(f"\n{self.app.lang['profile_joined']}: {user['created_at'][:10]}", style="#475569")
            await display.remove_children()
            await display.mount(Static(t))
            self.query_one("#profile-display-name", Input).value = user.get("display_name", "")
            self.query_one("#profile-bio", Input).value = user.get("bio", "")
            self.query_one("#profile-email", Input).value = user.get("email", "")
            container = self.query_one("#profile-posts", ScrollableContainer)
            await container.remove_children()
            for p in posts:
                await container.mount(PostWidget(p))
            self._update_header()

            # Renderiza avatar via chafa
            if user.get("avatar_path"):
                try:
                    img_data = await self.app.api.download_avatar(user["username"])
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
                                avatar_art.update(Text.from_ansi(stdout.decode()))
                                avatar_art.display = True
                        finally:
                            os.unlink(tmp.name)
                except Exception:
                    pass
        except Exception as e:
            await display.remove_children()
            await display.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    @work(exclusive=False)
    async def _load_files(self):
        """Carrega lista de arquivos enviados."""
        container = self.query_one("#files-list", ScrollableContainer)
        await container.remove_children()
        try:
            files = await self.app.api.list_files()
            if not files:
                await container.mount(Static(self.app.lang["files_empty"], classes="info"))
            for f in files:
                await container.mount(FileItemWidget(f, classes="file-item"))
        except Exception as e:
            await container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    @work(exclusive=False)
    async def _load_config(self):
        """Carrega informações de configuração do servidor."""
        info = self.query_one("#config-info", Static)
        status = self.query_one("#config-status", Static)
        try:
            user = await self.app.api.get_me()
            onion = self.app.client_state.onion_address
            rede = self.app.lang["config_network_tor"].format(onion) if onion else self.app.lang["config_network_direct"]
            info.update(Text.from_markup(
                f"[bold #e2e8f0]Usuário:[/] [#7c3aed]@{user['username']}[/]\n"
                f"[bold #e2e8f0]Servidor:[/] [#64748b]{self.app.client_state.server_url}[/]\n"
                f"[bold #e2e8f0]Rede:[/] [#64748b]{rede}[/]\n"
            ))
            status.update(Text(self.app.lang["config_connected"], style="bold #22c55e"))

            self._refresh_config_buttons()
        except Exception as e:
            status.update(Text(f"❌ {self.app.lang['error_prefix']}: {e}", style="bold #ef4444"))

    def _update_notification_badge(self, unread_count: int):
        """Atualiza o badge de notificações não lidas na aba."""
        btn = self.query_one("#tab-notifications", Button)
        base = self.app.lang["tab_notifications"]
        if unread_count > 0:
            btn.label = f"{base} ({unread_count})"
            btn.classes = "tab-btn -has-unread"
        else:
            btn.label = base
            btn.classes = "tab-btn"

    @work(exclusive=False)
    async def _load_notifications(self):
        """Carrega lista de notificações."""
        container = self.query_one("#notif-list", ScrollableContainer)
        await container.remove_children()
        try:
            data = await self.app.api.get_notifications(limit=50)
            notifications = data.get("notifications", []) if isinstance(data, dict) else data
            unread_count = data.get("unread_count", 0) if isinstance(data, dict) else 0
            self._update_notification_badge(unread_count)
            if not notifications:
                await container.mount(Static(self.app.lang["notifications_empty"], classes="info"))
            for n in notifications:
                await container.mount(NotificationWidget(n))
        except Exception as e:
            await container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    @work(exclusive=False)
    async def _mark_all_notifications_read(self):
        """Marca todas as notificações como lidas."""
        try:
            await self.app.api.mark_all_notifications_read()
            self.notify(self.app.lang["notifications_mark_read"], severity="information")
            self._load_notifications()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    def _refresh_config_buttons(self):
        """Destaca botão do idioma e tema conforme configuração atual."""
        for btn in self.query(".cfg-lang-btn"):
            ident = btn.id or ""
            if ident.startswith("cfg-lang-"):
                lang = ident.replace("cfg-lang-", "")
                btn.variant = "primary" if lang == self.app.language else "default"
            elif ident.startswith("cfg-theme-"):
                theme = ident.replace("cfg-theme-", "")
                btn.variant = "primary" if theme == self.app.theme_name else "default"

    # ── EVENTOS DE BOTÃO ───────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed):
        bid = event.button.id

        # Tabs
        if bid and bid.startswith("tab-"):
            self._switch_tab(bid[4:])
            if bid[4:] == "dms":
                self._update_dm_toolbar()
                self.run_worker(self._load_dms(), exclusive=False)
            return

        # Feed
        if bid in ("feed-following", "feed-refresh"):
            self._load_feed("following")
        elif bid == "feed-global":
            self._load_feed("global")
        elif bid == "feed-attach":
            self._attach_media_to_post()

        # Chat
        elif bid == "chat-refresh":
            self._load_chat()
        elif bid == "chat-send":
            inp = self.query_one("#chat-input", Input)
            self._submit_chat(inp.value.strip())
            inp.value = ""
        elif bid == "chat-anon-toggle":
            self._chat_anonymous = not self._chat_anonymous
            btn = self.query_one("#chat-anon-toggle", Button)
            btn.classes = "active" if self._chat_anonymous else ""

        # Perfil
        elif bid == "profile-refresh":
            self._load_profile()
        elif bid == "profile-edit-toggle":
            self._toggle_profile_edit()
        elif bid == "profile-edit-save":
            self._save_profile()
        elif bid == "profile-edit-cancel":
            self.query_one("#profile-edit-box", Vertical).display = False
            self.query_one("#profile-edit-toggle", Button).label = self.app.lang["edit"]
        elif bid == "profile-avatar":
            self._upload_avatar_prompt()
        elif bid == "profile-blocks":
            self._open_blocks()

        # Busca
        elif bid == "search-go":
            self._do_search()
        elif bid in ("sf-users", "sf-posts", "sf-files"):
            event.button.toggle_class("on")
            if self.query_one("#search-input", Input).value.strip():
                self._do_search()
        elif bid == "search-filter-toggle":
            filters = self.query_one("#search-filters", Vertical)
            filters.toggle_class("-hidden")
            btn = self.query_one("#search-filter-toggle", Button)
            if not filters.has_class("-hidden"):
                btn.label = "▴"
                btn.add_class("-open")
            else:
                btn.label = "▾"
                btn.remove_class("-open")

        # Arquivos
        elif bid == "files-refresh":
            self._load_files()
        elif bid == "files-upload":
            self._upload_file_prompt()

        # DMs
        elif bid == "dms-mode":
            self._dm_mode = "group" if self._dm_mode == "conv" else "conv"
            self._current_conv_id = None
            self._current_group_id = None
            self._update_dm_toolbar()
            self.run_worker(self._load_dms(), exclusive=False)
        elif bid == "dms-refresh":
            self.run_worker(self._load_dms(), exclusive=False)
        elif bid == "dms-send":
            inp = self.query_one("#dms-input", Input)
            self._submit_dm(inp.value.strip())
            inp.value = ""
        elif bid == "dms-new":
            if self._dm_mode == "group":
                self._do_new_group()
            else:
                self._do_new_dm()
        elif bid == "dms-clear":
            if self._dm_mode == "group":
                self._do_leave_group()
            else:
                self._do_clear_dm()
        elif bid == "dms-attach":
            self._attach_media_to_dm()
        elif bid == "dms-add-member":
            self._do_add_group_member()
        elif bid == "dms-remove-member":
            self._do_remove_group_member()

        # Notificações
        elif bid == "notif-refresh":
            self._load_notifications()
        elif bid == "notif-read-all":
            self._mark_all_notifications_read()

        # Config
        elif bid == "config-logout":
            self._do_logout()
        elif bid and bid.startswith("cfg-lang-"):
            lang = bid.replace("cfg-lang-", "")
            self.app.set_language(lang)
            self.notify(self.app.lang["language_changed"].format(lang))
            self._restart_main()
        elif bid and bid.startswith("cfg-theme-"):
            theme = bid.replace("cfg-theme-", "")
            self.app.set_theme(theme)
            self.notify(self.app.lang["theme_changed"].format(self.app.lang[f"theme_{theme}"]))
            self._restart_main()

    # ── EVENTOS DE INPUT ───────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "feed-input":
            self._submit_post(event.value.strip())
            event.input.value = ""
        elif event.input.id == "chat-input":
            self._submit_chat(event.value.strip())
            event.input.value = ""
        elif event.input.id == "search-input":
            self._do_search()
        elif event.input.id in ("sf-username", "sf-date-from", "sf-date-to"):
            if self.query_one("#search-input", Input).value.strip():
                self._do_search()
        elif event.input.id == "dms-input":
            self._submit_dm(event.value.strip())
            event.input.value = ""
        elif event.input.id in ("sf-username", "sf-date-from", "sf-date-to"):
            if self.query_one("#search-input", Input).value.strip():
                self._do_search()
        elif event.input.id == "dms-input":
            self._submit_dm(event.value.strip())
            event.input.value = ""

    def on_input_changed(self, event: Input.Changed):
        # Filtra caracteres não numéricos nos campos de data
        if event.input.id in ("sf-date-from", "sf-date-to"):
            clean = "".join(c for c in event.value if c.isdigit() or c == "/")
            if clean != event.value:
                event.input.value = clean

    # ── AÇÕES (submissão de posts, chat, etc.) ─────────────────────

    @work(exclusive=False)
    async def _submit_post(self, content: str):
        """Envia novo post com mídia opcional."""
        if not content and not self._pending_media_id:
            return
        try:
            file_id = self._pending_media_id
            self._pending_media_id = None
            await self.app.api.create_post(content, file_id=file_id)
            self._load_feed("following")
            btn = self.query_one("#feed-attach", Button)
            btn.label = "📷"
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    @work(exclusive=False)
    async def _submit_chat(self, content: str):
        """Envia mensagem no chat público."""
        if not content:
            return
        try:
            await self.app.api.send_chat(content, is_anonymous=self._chat_anonymous)
            self._load_chat()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    def _toggle_profile_edit(self):
        """Mostra/esconde formulário de edição de perfil."""
        edit_box = self.query_one("#profile-edit-box", Vertical)
        edit_box.display = not edit_box.display
        btn = self.query_one("#profile-edit-toggle", Button)
        btn.label = self.app.lang["cancel"] if edit_box.display else self.app.lang["edit"]

    @work(exclusive=False)
    async def _save_profile(self):
        """Salva alterações do perfil."""
        display_name = self.query_one("#profile-display-name", Input).value.strip()
        bio = self.query_one("#profile-bio", Input).value.strip()
        email = self.query_one("#profile-email", Input).value.strip()
        try:
            await self.app.api.update_profile(display_name, bio, email)
            self.notify(self.app.lang["profile_updated"], severity="information")
            self._load_profile()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    @work(exclusive=False)
    async def _do_search(self):
        """Executa busca com filtros opcionais."""
        q = self.query_one("#search-input", Input).value.strip()
        if not q:
            return

        filters_visible = not self.query_one("#search-filters", Vertical).has_class("-hidden")

        if filters_visible:
            include_users = self.query_one("#sf-users", Button).has_class("on")
            include_posts = self.query_one("#sf-posts", Button).has_class("on")
            include_files = self.query_one("#sf-files", Button).has_class("on")
            username = self.query_one("#sf-username", Input).value.strip()

            raw_from = self.query_one("#sf-date-from", Input).value.strip()
            raw_to = self.query_one("#sf-date-to", Input).value.strip()

            day_first = self.app.language.startswith("pt")

            def _parse_date(raw):
                """Converte data DD/MM ou MM/DD para ISO."""
                if not raw:
                    return ""
                parts = raw.split("/")
                if len(parts) != 3:
                    return ""
                if day_first:
                    d, m, y = parts
                else:
                    m, d, y = parts
                y = f"20{y}" if len(y) == 2 else y
                return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

            date_from = _parse_date(raw_from)
            date_to = _parse_date(raw_to)
        else:
            include_users = True
            include_posts = True
            include_files = False
            date_from = ""
            date_to = ""
            username = ""

        container = self.query_one("#search-results", ScrollableContainer)
        await container.remove_children()

        try:
            result = await self.app.api.search_all(
                q=q,
                include_users=include_users,
                include_posts=include_posts,
                include_files=include_files,
                date_from=date_from,
                date_to=date_to,
                username=username,
            )

            has_any = False

            if include_users and result.get("users"):
                has_any = True
                await container.mount(Static(self.app.lang["search_users_section"].format(len(result['users'])), classes="search-section-header"))
                for u in result["users"]:
                    await container.mount(UserResultWidget(u, classes="search-result"))

            if include_posts and result.get("posts"):
                has_any = True
                await container.mount(Static(self.app.lang["search_posts_section"].format(len(result['posts'])), classes="search-section-header"))
                for p in result["posts"]:
                    await container.mount(PostWidget(p, classes="search-result"))

            if include_files and result.get("files"):
                has_any = True
                await container.mount(Static(self.app.lang["search_files_section"].format(len(result['files'])), classes="search-section-header"))
                for f in result["files"]:
                    await container.mount(FileItemWidget(f, classes="search-result"))

            if not has_any:
                await container.mount(Static(self.app.lang["search_no_results"], classes="info"))
        except Exception as e:
            await container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    def _restart_main(self):
        """Reinicia a tela principal (após troca de idioma/tema)."""
        self.app.pop_screen()
        self.app.push_screen("main")

    # ── MÍDIA EM POSTS ─────────────────────────────────────────────

    def _attach_media_to_post(self):
        """Abre seletor de arquivos para anexar mídia a um post."""
        def handle_result(path):
            if path:
                self._upload_post_media(path)

        from client.screens.file_picker_screen import FilePickerScreen
        self.app.push_screen(FilePickerScreen(start_path="/"), callback=handle_result)

    @work(exclusive=False)
    async def _upload_post_media(self, filepath: str):
        """Faz upload de mídia para anexar ao post."""
        ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""
        if ext not in ("png", "jpg", "jpeg", "gif", "mp4", "wmv", "webm", "mov", "avi", "mkv"):
            self.notify(self.app.lang["media_formats_allowed"], severity="warning")
            return
        try:
            result = await self.app.api.upload_file(filepath)
            self._pending_media_id = result["id"]
            btn = self.query_one("#feed-attach", Button)
            btn.label = f"📷 {result.get('original_name', '')[:20]}"
            self.notify(self.app.lang["media_attached"], severity="information")
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    # ── DMs ─────────────────────────────────────────────────────────

    async def _load_dms(self):
        """Carrega lista de conversas/grupos e mensagens se houver selecionada."""
        if self._dm_mode == "group":
            await self._load_groups()
            if self._current_group_id:
                await self._load_group_messages(self._current_group_id)
        else:
            await self._load_conversations()
            if self._current_conv_id:
                await self._load_dm_messages(self._current_conv_id)

    async def _load_conversations(self):
        """Carrega lista de conversas de DM."""
        conv_list = self.query_one("#dms-conv-list", ScrollableContainer)
        await conv_list.remove_children()
        try:
            convs = await self.app.api.list_conversations()
            self._conversations = convs
            if not convs:
                await conv_list.mount(
                    Static("Nenhuma conversa.\nClique em ✉️ Novo DM para iniciar.", classes="dms-info")
                )
                return
            for c in convs:
                widget = DmConvItem(c, classes="dms-conv-item")
                if self._current_conv_id and c["id"] == self._current_conv_id:
                    widget.add_class("-active")
                await conv_list.mount(widget)
        except Exception as e:
            await conv_list.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    async def _load_dm_messages(self, conv_id: int):
        """Carrega mensagens de uma conversa específica."""
        msg_container = self.query_one("#dms-messages", ScrollableContainer)
        await msg_container.remove_children()
        self._current_conv_id = conv_id
        for w in self.query(".dms-conv-item"):
            w.remove_class("-active")
            if hasattr(w, "conv_data") and w.conv_data.get("id") == conv_id:
                w.add_class("-active")
        try:
            me = (await self.app.api.get_me())["username"]
            msgs = await self.app.api.get_dm_messages(conv_id)
            self._last_dm_id = 0
            if not msgs:
                await msg_container.mount(Static(self.app.lang["chat_empty"], classes="dms-info"))
            for m in msgs:
                m["is_me"] = m["sender_username"] == me
                await msg_container.mount(DmMsgWidget(m))
                mid = m.get("id", 0)
                if mid > self._last_dm_id:
                    self._last_dm_id = mid
            msg_container.scroll_end(animate=False)
        except Exception as e:
            await msg_container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    @work(exclusive=False)
    async def _submit_dm(self, content: str):
        """Envia mensagem de DM ou grupo."""
        if not content and not self._pending_media_id:
            return
        if self._dm_mode == "group":
            if not self._current_group_id:
                self.notify(self.app.lang["dm_select_conversation"], severity="warning")
                return
            try:
                file_id = self._pending_media_id
                self._pending_media_id = None
                await self.app.api.send_group_message(self._current_group_id, content, dm_file_id=file_id)
                await self._load_group_messages(self._current_group_id)
                await self._load_groups()
            except Exception as e:
                self.notify(f"{self.app.lang['group_error'].format(e)}", severity="error")
            return
        if not self._current_conv_id:
            self.notify(self.app.lang["dm_select_conversation"], severity="warning")
            return
        try:
            file_id = self._pending_media_id
            self._pending_media_id = None
            await self.app.api.send_dm(self._current_conv_id, content, dm_file_id=file_id)
            await self._load_dm_messages(self._current_conv_id)
            await self._load_conversations()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    def _do_new_dm(self):
        """Inicia uma nova conversa solicitando username."""
        def handle_input(username: str):
            if username:
                self._start_conversation(username.strip())

        from client.screens.input_screen import InputScreen
        self.app.push_screen(InputScreen(self.app.lang["dm_username_prompt"], self.app.lang["dm_start"]), callback=handle_input)

    @work(exclusive=False)
    async def _start_conversation(self, username: str):
        """Inicia conversa com outro usuário."""
        try:
            result = await self.app.api.start_conversation(username)
            self._current_conv_id = result["id"]
            await self._load_dms()
            inp = self.query_one("#dms-input", Input)
            inp.focus()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    def _do_clear_dm(self):
        """Confirma e limpa a conversa atual."""
        if not self._current_conv_id:
            self.notify(self.app.lang["dm_no_conversation_selected"], severity="warning")
            return

        def handle_confirm(confirmed: bool):
            if confirmed:
                self._clear_current_conversation()

        from client.screens.confirm_screen import ConfirmScreen
        self.app.push_screen(ConfirmScreen(self.app.lang["dm_clear_confirm_title"], self.app.lang["dm_clear_confirm_message"]), callback=handle_confirm)

    @work(exclusive=False)
    async def _clear_current_conversation(self):
        """Limpa todas as mensagens da conversa atual."""
        try:
            await self.app.api.clear_conversation(self._current_conv_id)
            self.notify(self.app.lang["dm_conversation_cleared"], severity="information")
            await self._load_dm_messages(self._current_conv_id)
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    def _update_dm_toolbar(self):
        """Atualiza botões da toolbar de DMs conforme modo."""
        mode_btn = self.query_one("#dms-mode", Button)
        mode_btn.label = self.app.lang["dm_mode_group"] if self._dm_mode == "group" else self.app.lang["dm_mode_conv"]
        mode_btn.set_class(self._dm_mode == "group", "-active")
        add_btn = self.query_one("#dms-add-member", Button)
        rm_btn = self.query_one("#dms-remove-member", Button)
        if self._dm_mode == "group":
            self.query_one("#dms-new", Button).label = self.app.lang["group_new"]
            self.query_one("#dms-clear", Button).label = self.app.lang["group_leave"]
            add_btn.display = True
            rm_btn.display = True
        else:
            self.query_one("#dms-new", Button).label = self.app.lang["dm_new"]
            self.query_one("#dms-clear", Button).label = self.app.lang["dm_clear"]
            add_btn.display = False
            rm_btn.display = False
        self.query_one("#dms-header", Static).update("")

    async def _load_groups(self):
        """Carrega lista de grupos."""
        conv_list = self.query_one("#dms-conv-list", ScrollableContainer)
        await conv_list.remove_children()
        try:
            groups = await self.app.api.list_groups()
            self._groups = groups
            if not groups:
                await conv_list.mount(
                    Static(self.app.lang["group_empty"], classes="dms-info")
                )
                return
            for g in groups:
                widget = GroupConvItem(g, classes="dms-conv-item")
                if self._current_group_id and g["id"] == self._current_group_id:
                    widget.add_class("-active")
                await conv_list.mount(widget)
        except Exception as e:
            await conv_list.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    async def _load_group_messages(self, group_id: int):
        """Carrega mensagens de um grupo."""
        msg_container = self.query_one("#dms-messages", ScrollableContainer)
        await msg_container.remove_children()
        self._current_group_id = group_id
        for w in self.query(".dms-conv-item"):
            w.remove_class("-active")
            if hasattr(w, "group_data") and w.group_data.get("id") == group_id:
                w.add_class("-active")
        try:
            group_info = next((g for g in self._groups if g["id"] == group_id), None)
            header = self.query_one("#dms-header", Static)
            if group_info:
                header.update(self.app.lang["group_header"].format(group_info["name"]))
            else:
                header.update("")
            me = (await self.app.api.get_me())["username"]
            msgs = await self.app.api.get_group_messages(group_id)
            self._last_group_msg_id = 0
            if not msgs:
                await msg_container.mount(Static(self.app.lang["chat_empty"], classes="dms-info"))
            for m in msgs:
                m["is_me"] = m["sender_username"] == me
                await msg_container.mount(DmMsgWidget(m))
                mid = m.get("id", 0)
                if mid > self._last_group_msg_id:
                    self._last_group_msg_id = mid
            msg_container.scroll_end(animate=False)
        except Exception as e:
            await msg_container.mount(Static(f"{self.app.lang['error_prefix']}: {e}", classes="error"))

    def _do_new_group(self):
        """Cria um novo grupo."""
        def handle_name(name: str):
            if not name:
                return
            def handle_members(members_str: str):
                if not members_str:
                    return
                members = [m.strip() for m in members_str.replace(",", " ").split() if m.strip()]
                self._create_group(name.strip(), members)
            from client.screens.input_screen import InputScreen
            self.app.push_screen(InputScreen(self.app.lang["group_members_prompt"], self.app.lang["save"]), callback=handle_members)
        from client.screens.input_screen import InputScreen
        self.app.push_screen(InputScreen(self.app.lang["group_name_prompt"], self.app.lang["save"]), callback=handle_name)

    @work(exclusive=False)
    async def _create_group(self, name: str, members: list[str]):
        """Envia requisição para criar grupo."""
        try:
            await self.app.api.create_group(name, members)
            self.notify(self.app.lang["group_create_success"], severity="information")
            await self._load_groups()
        except Exception as e:
            self.notify(f"{self.app.lang['group_error'].format(e)}", severity="error")

    def _do_leave_group(self):
        """Confirma e sai do grupo atual."""
        if not self._current_group_id:
            self.notify(self.app.lang["dm_no_conversation_selected"], severity="warning")
            return
        def handle_confirm(confirmed: bool):
            if confirmed:
                self._leave_current_group()
        from client.screens.confirm_screen import ConfirmScreen
        self.app.push_screen(ConfirmScreen(
            self.app.lang["group_leave"],
            self.app.lang["dm_clear_confirm_message"],
        ), callback=handle_confirm)

    def _do_add_group_member(self):
        """Adiciona membro ao grupo atual."""
        if not self._current_group_id:
            self.notify(self.app.lang["dm_select_conversation"], severity="warning")
            return
        def handle_username(username: str):
            if username:
                self._add_member_to_group(username.strip())
        from client.screens.input_screen import InputScreen
        self.app.push_screen(InputScreen(self.app.lang["group_member_add_prompt"], self.app.lang["save"]), callback=handle_username)

    def _do_remove_group_member(self):
        """Remove membro do grupo atual."""
        if not self._current_group_id:
            self.notify(self.app.lang["dm_select_conversation"], severity="warning")
            return
        def handle_username(username: str):
            if username:
                self._remove_member_from_group(username.strip())
        from client.screens.input_screen import InputScreen
        self.app.push_screen(InputScreen(self.app.lang["group_remove_prompt"], self.app.lang["save"]), callback=handle_username)

    @work(exclusive=False)
    async def _remove_member_from_group(self, username: str):
        """Envia requisição para remover membro."""
        try:
            members = await self.app.api.list_group_members(self._current_group_id)
            target = next((m for m in members if m["username"] == username), None)
            if not target:
                self.notify(self.app.lang["error_user_not_found"], severity="warning")
                return
            await self.app.api.remove_group_member(self._current_group_id, target["user_id"])
            self.notify(self.app.lang["group_member_removed"], severity="information")
            await self._load_group_messages(self._current_group_id)
        except Exception as e:
            self.notify(f"{self.app.lang['group_error'].format(e)}", severity="error")

    @work(exclusive=False)
    async def _add_member_to_group(self, username: str):
        """Envia requisição para adicionar membro."""
        try:
            await self.app.api.add_group_member(self._current_group_id, username)
            self.notify(self.app.lang["group_member_added"], severity="information")
            await self._load_group_messages(self._current_group_id)
        except Exception as e:
            self.notify(f"{self.app.lang['group_error'].format(e)}", severity="error")

    @work(exclusive=False)
    async def _leave_current_group(self):
        """Sai do grupo atual."""
        try:
            await self.app.api.leave_group(self._current_group_id)
            self.notify(self.app.lang["group_left"], severity="information")
            self._current_group_id = None
            await self._load_groups()
            msg_container = self.query_one("#dms-messages", ScrollableContainer)
            await msg_container.remove_children()
            self.query_one("#dms-header", Static).update("")
        except Exception as e:
            self.notify(f"{self.app.lang['group_error'].format(e)}", severity="error")

    def _attach_media_to_dm(self):
        """Abre seletor para anexar mídia a uma DM."""
        def handle_result(path):
            if path:
                self._upload_dm_media(path)

        from client.screens.file_picker_screen import FilePickerScreen
        self.app.push_screen(FilePickerScreen(start_path="/"), callback=handle_result)

    @work(exclusive=False)
    async def _upload_dm_media(self, filepath: str):
        """Faz upload de arquivo para anexar à DM."""
        try:
            result = await self.app.api.upload_dm_file(filepath)
            self._pending_media_id = result["id"]
            self.notify(self.app.lang["media_attached_dm"], severity="information")
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']}: {e}", severity="error")

    # ── UPLOAD DE ARQUIVOS ─────────────────────────────────────────

    def _upload_file_prompt(self):
        """Abre o seletor de arquivos para fazer upload."""
        def handle_result(path):
            if path:
                self._do_upload(path)

        from client.screens.file_picker_screen import FilePickerScreen
        self.app.push_screen(FilePickerScreen(start_path="/"), callback=handle_result)

    @work(exclusive=False)
    async def _do_upload(self, filepath: str):
        """Faz upload de arquivo para a aba Arquivos."""
        try:
            result = await self.app.api.upload_file(filepath)
            self.notify(f"{self.app.lang['upload_done_prefix']}: {result.get('original_name', 'OK')}", severity="information")
            self._load_files()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']} no envio: {e}", severity="error")

    # ── UPLOAD DE AVATAR ───────────────────────────────────────────

    def _upload_avatar_prompt(self):
        """Abre o seletor de arquivos para escolher foto de perfil."""
        def handle_result(path):
            if path:
                self._do_upload_avatar(path)

        from client.screens.file_picker_screen import FilePickerScreen
        self.app.push_screen(FilePickerScreen(start_path="/"), callback=handle_result)

    @work(exclusive=False)
    async def _do_upload_avatar(self, filepath: str):
        """Faz upload da foto de perfil."""
        try:
            await self.app.api.upload_avatar(filepath)
            self.notify(self.app.lang["avatar_updated"], severity="information")
            self._load_profile()
        except Exception as e:
            self.notify(f"{self.app.lang['error_prefix']} no envio: {e}", severity="error")

    # ── BLOQUEADOS ────────────────────────────────────────────────

    def _open_blocks(self):
        from client.screens.block_list_screen import BlockListScreen
        self.app.push_screen(BlockListScreen())

    # ── VISITAR PERFIL ─────────────────────────────────────────────

    def _open_profile(self, username: str):
        """Abre o perfil de outro usuário em tela separada."""
        from client.screens.profile_view_screen import ProfileViewScreen
        self.app.push_screen(ProfileViewScreen(username), callback=self._on_profile_closed)

    def _on_profile_closed(self, result) -> None:
        """Callback ao fechar perfil visitado: pode iniciar DM."""
        if result and isinstance(result, dict) and "dm" in result:
            self._switch_tab("dms")
            self._start_conversation(result["dm"])

    # ── NAVEGAÇÃO ──────────────────────────────────────────────────

    def _get_active_tab(self) -> str:
        for name in self.TAB_NAMES:
            if self.query_one(f"#content-{name}", Vertical).has_class("-active"):
                return name
        return "feed"

    def _get_active_scrollable(self) -> ScrollableContainer | None:
        for name in self.TAB_NAMES:
            content = self.query_one(f"#content-{name}", Vertical)
            if content.has_class("-active"):
                return content.query(ScrollableContainer).first()
        return None

    def action_tab_feed(self):
        self._switch_tab("feed")

    def action_tab_chat(self):
        self._switch_tab("chat")

    def action_tab_dms(self):
        self._switch_tab("dms")
        self._update_dm_toolbar()
        self.run_worker(self._load_dms(), exclusive=False)

    def action_tab_files(self):
        self._switch_tab("files")

    def action_tab_search(self):
        self._switch_tab("search")

    def action_tab_profile(self):
        self._switch_tab("profile")

    def action_tab_settings(self):
        self._switch_tab("settings")

    def action_tab_notifications(self):
        self._switch_tab("notifications")
        self._load_notifications()

    def action_next_tab(self):
        current = self._get_active_tab()
        idx = self.TAB_NAMES.index(current)
        self._switch_tab(self.TAB_NAMES[(idx + 1) % len(self.TAB_NAMES)])

    def action_prev_tab(self):
        current = self._get_active_tab()
        idx = self.TAB_NAMES.index(current)
        self._switch_tab(self.TAB_NAMES[(idx - 1) % len(self.TAB_NAMES)])

    def action_scroll_down(self):
        sc = self._get_active_scrollable()
        if sc:
            sc.scroll_down()

    def action_scroll_up(self):
        sc = self._get_active_scrollable()
        if sc:
            sc.scroll_up()

    def action_new_post(self):
        self._switch_tab("feed")
        self.query_one("#feed-input", Input).focus()

    def action_focus_search(self):
        self._switch_tab("search")
        self.query_one("#search-input", Input).focus()

    def action_focus_feed_input(self):
        self.query_one("#feed-input", Input).focus()

    def action_refresh(self):
        """Atualiza a aba ativa."""
        tab = self._get_active_tab()
        if tab == "feed":
            self._load_feed()
        elif tab == "chat":
            self._load_chat()
        elif tab == "dms":
            self.run_worker(self._load_dms(), exclusive=False)
            self._update_dm_toolbar()
        elif tab == "files":
            self._load_files()
        elif tab == "profile":
            self._load_profile()
        elif tab == "search":
            self._do_search()
        elif tab == "notifications":
            self._load_notifications()

    @work(exclusive=False)
    async def _do_logout(self):
        """Faz logout: limpa token salvo e volta para welcome."""
        try:
            await self.app.api.logout()
        except Exception:
            pass
        cfg = load_config()
        cfg["saved_token"] = ""
        cfg["saved_username"] = ""
        save_config(cfg)
        self.app.pop_screen()
        self.app.push_screen("welcome")
