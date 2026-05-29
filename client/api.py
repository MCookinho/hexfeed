from __future__ import annotations
import os
import mimetypes
from dataclasses import dataclass
from urllib.parse import urlparse
import httpx


TOR_PORTS = [9050, 19050, 9150]
LOCAL_FALLBACK = "http://127.0.0.1:8000"


@dataclass
class ClientState:
    server_url: str = ""
    token: str = ""
    user: dict | None = None


class HexfeedAPI:

    def __init__(self, state: ClientState):
        self.state = state
        self._client = self._build_client()

    def _build_client(self) -> httpx.AsyncClient:
        url = self.state.server_url or ""
        if not self._is_local_url(url):
            return self._try_proxy_ports()
        return httpx.AsyncClient(timeout=30)

    def _try_proxy_ports(self) -> httpx.AsyncClient:
        for port in TOR_PORTS:
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
                s.close()
                return httpx.AsyncClient(proxy=f"socks5://127.0.0.1:{port}", timeout=30)
            except Exception:
                continue
        return httpx.AsyncClient(timeout=30)

    @staticmethod
    def _is_local_url(url: str) -> bool:
        host = urlparse(url).hostname or ""
        return host in ("127.0.0.1", "::1", "localhost") or host.endswith(".local")

    @staticmethod
    def _is_onion_url(url: str) -> bool:
        host = urlparse(url).hostname or ""
        return host.endswith(".onion")

    def _get_server_url(self) -> str:
        return self.state.server_url

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self._get_server_url()}{path}"
        try:
            r = await self._client.request(method, url, **kwargs)
            return r
        except (httpx.ProxyError, httpx.ConnectError, httpx.ProtocolError) as e:
            if self._is_onion_url(self.state.server_url):
                fallback = httpx.AsyncClient(timeout=30)
                try:
                    r = await fallback.request(
                        method, f"{LOCAL_FALLBACK}{path}", **kwargs
                    )
                    return r
                finally:
                    await fallback.aclose()
            raise

    async def get_version_info(self) -> dict:
        r = await self._request("GET", "/api/version")
        r.raise_for_status()
        return r.json()

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.state.token:
            h["Authorization"] = f"Bearer {self.state.token}"
        return h

    async def health(self) -> dict:
        r = await self._request("GET", "/api/health")
        r.raise_for_status()
        return r.json()

    async def get_challenge(self) -> dict:
        r = await self._request("GET", "/api/auth/challenge")
        r.raise_for_status()
        return r.json()

    async def register(
        self, username: str, password: str, pgp_key: str = "",
        pgp_private_key: str = "", email: str = "",
        pow_challenge: str = "", pow_nonce: int = 0,
        math_answers: list[int] | None = None,
    ) -> dict:
        r = await self._request(
            "POST", "/api/auth/register",
            json={
                "username": username,
                "password": password,
                "pgp_public_key": pgp_key,
                "pgp_private_key": pgp_private_key,
                "email": email,
                "pow_challenge": pow_challenge,
                "pow_nonce": pow_nonce,
                "math_answers": math_answers or [0, 0],
            },
        )
        if r.status_code == 409:
            raise ValueError("Nome de usuário já está em uso")
        if r.status_code == 400:
            raise ValueError(r.json().get("detail", "Dados inválidos"))
        if r.status_code == 429:
            raise ValueError("Muitas tentativas de registro. Aguarde 1 hora.")
        r.raise_for_status()
        data = r.json()
        self.state.token = data["token"]
        self.state.user = data["user"]
        return data

    async def login(self, username: str, password: str, pgp_private_key: str = "") -> dict:
        r = await self._request(
            "POST", "/api/auth/login",
            json={"username": username, "password": password, "pgp_private_key": pgp_private_key},
        )
        if r.status_code == 401:
            detail = r.json().get("detail", "Usuário ou senha inválidos")
            raise ValueError(detail)
        r.raise_for_status()
        data = r.json()
        self.state.token = data["token"]
        self.state.user = data["user"]
        return data

    async def logout(self):
        if self.state.token:
            await self._request(
                "POST", "/api/auth/logout",
                headers=self._headers(),
            )
        self.state.token = ""
        self.state.user = None

    async def get_me(self) -> dict:
        r = await self._request("GET", "/api/users/me", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def get_user(self, username: str) -> dict:
        r = await self._request(
            "GET", f"/api/users/{username}", headers=self._headers()
        )
        if r.status_code == 404:
            raise ValueError("Usuário não encontrado")
        r.raise_for_status()
        return r.json()

    async def update_profile(self, display_name: str = "", bio: str = "", email: str = "") -> dict:
        r = await self._request(
            "PUT", "/api/users/profile",
            headers=self._headers(),
            json={"display_name": display_name, "bio": bio, "email": email},
        )
        r.raise_for_status()
        self.state.user = r.json()
        return self.state.user

    async def search_users(self, q: str, limit: int = 20) -> list:
        r = await self._request(
            "GET", "/api/users/search/list",
            headers=self._headers(),
            params={"q": q, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def follow(self, username: str):
        r = await self._request("POST", f"/api/follow/{username}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def unfollow(self, username: str):
        r = await self._request("DELETE", f"/api/follow/{username}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def block_user(self, username: str) -> dict:
        r = await self._request("POST", f"/api/users/{username}/block", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def unblock_user(self, username: str) -> dict:
        r = await self._request("POST", f"/api/users/{username}/unblock", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def get_my_blocks(self) -> list:
        r = await self._request("GET", "/api/users/me/blocks", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def get_followers(self, username: str, offset: int = 0, limit: int = 20) -> list:
        r = await self._request(
            "GET", f"/api/users/{username}/followers",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def get_following(self, username: str, offset: int = 0, limit: int = 20) -> list:
        r = await self._request(
            "GET", f"/api/users/{username}/following",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def create_post(self, content: str, reply_to: int | None = None, file_id: int | None = None) -> dict:
        r = await self._request(
            "POST", "/api/posts",
            headers=self._headers(),
            json={"content": content, "reply_to": reply_to, "file_id": file_id},
        )
        r.raise_for_status()
        return r.json()

    async def get_feed(self, offset: int = 0, limit: int = 20) -> list:
        r = await self._request(
            "GET", "/api/posts",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def get_global_feed(self, offset: int = 0, limit: int = 20) -> list:
        r = await self._request(
            "GET", "/api/posts/global",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def get_user_posts(self, username: str, offset: int = 0, limit: int = 20) -> list:
        r = await self._request(
            "GET", f"/api/posts/user/{username}",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def search_posts(self, q: str, offset: int = 0, limit: int = 20) -> list:
        r = await self._request(
            "GET", "/api/posts/search",
            headers=self._headers(),
            params={"q": q, "offset": offset, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def search_all(
        self, q: str, include_users: bool = True, include_posts: bool = True,
        include_files: bool = False, date_from: str = "", date_to: str = "",
        username: str = "", limit: int = 20,
    ) -> dict:
        r = await self._request(
            "GET", "/api/search",
            headers=self._headers(),
            params={
                "q": q, "include_users": str(include_users).lower(),
                "include_posts": str(include_posts).lower(),
                "include_files": str(include_files).lower(),
                "date_from": date_from, "date_to": date_to,
                "username": username, "limit": limit,
            },
        )
        r.raise_for_status()
        return r.json()

    async def delete_post(self, post_id: int):
        r = await self._request("DELETE", f"/api/posts/{post_id}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def like_post(self, post_id: int):
        r = await self._request("POST", f"/api/posts/{post_id}/like", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def unlike_post(self, post_id: int):
        r = await self._request("DELETE", f"/api/posts/{post_id}/like", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def create_comment(self, post_id: int, content: str) -> dict:
        r = await self._request(
            "POST", f"/api/posts/{post_id}/comments",
            headers=self._headers(),
            json={"content": content},
        )
        r.raise_for_status()
        return r.json()

    async def get_comments(self, post_id: int, offset: int = 0, limit: int = 20) -> list:
        r = await self._request(
            "GET", f"/api/posts/{post_id}/comments",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def delete_comment(self, post_id: int, comment_id: int):
        r = await self._request(
            "DELETE", f"/api/posts/{post_id}/comments/{comment_id}",
            headers=self._headers(),
        )
        r.raise_for_status()

    async def get_chat(self, offset: int = 0, limit: int = 50) -> list:
        r = await self._request(
            "GET", "/api/chat",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def send_chat(self, content: str, is_anonymous: bool = False) -> dict:
        r = await self._request(
            "POST", "/api/chat",
            headers=self._headers(),
            json={"content": content, "is_anonymous": is_anonymous},
        )
        r.raise_for_status()
        return r.json()

    async def start_conversation(self, username: str) -> dict:
        r = await self._request(
            "POST", "/api/conversations",
            headers=self._headers(),
            params={"username": username},
        )
        r.raise_for_status()
        return r.json()

    async def list_conversations(self) -> list:
        r = await self._request("GET", "/api/conversations", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def get_dm_messages(self, conv_id: int) -> list:
        r = await self._request(
            "GET", f"/api/conversations/{conv_id}/messages",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def send_dm(self, conv_id: int, content: str, file_id: int | None = None, dm_file_id: int | None = None) -> dict:
        r = await self._request(
            "POST", f"/api/conversations/{conv_id}/messages",
            headers=self._headers(),
            json={"content": content, "file_id": file_id, "dm_file_id": dm_file_id},
        )
        r.raise_for_status()
        return r.json()

    async def clear_conversation(self, conv_id: int):
        r = await self._request(
            "DELETE", f"/api/conversations/{conv_id}/messages",
            headers=self._headers(),
        )
        r.raise_for_status()

    async def upload_file(self, filepath: str) -> dict:
        size = os.path.getsize(filepath)
        if size > 10 * 1024 * 1024:
            raise ValueError("Arquivo muito grande (max 10 MB)")
        with open(filepath, "rb") as f:
            files = {"file": (os.path.basename(filepath), f, "application/octet-stream")}
            r = await self._request(
                "POST", "/api/files/upload",
                headers={"Authorization": f"Bearer {self.state.token}"},
                files=files,
            )
        r.raise_for_status()
        return r.json()

    async def list_files(self) -> list:
        r = await self._request("GET", "/api/files", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def download_file(self, file_id: int, save_path: str):
        r = await self._request(
            "GET", f"/api/files/{file_id}/download",
            headers=self._headers(),
        )
        r.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(r.content)
        return save_path

    async def delete_file(self, file_id: int):
        r = await self._request("DELETE", f"/api/files/{file_id}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def upload_avatar(self, filepath: str) -> dict:
        size = os.path.getsize(filepath)
        if size > 2 * 1024 * 1024:
            raise ValueError("Imagem muito grande (max 2 MB)")
        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = "image/png"
        with open(filepath, "rb") as f:
            files = {"file": (os.path.basename(filepath), f, mime_type)}
            r = await self._request(
                "POST", "/api/users/avatar",
                headers={"Authorization": f"Bearer {self.state.token}"},
                files=files,
            )
        r.raise_for_status()
        return r.json()

    async def get_avatar_url(self, username: str) -> str:
        return f"{self._get_server_url()}/api/users/{username}/avatar"

    async def download_avatar(self, username: str) -> bytes | None:
        r = await self._request("GET", f"/api/users/{username}/avatar", headers=self._headers())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    async def upload_dm_file(self, filepath: str) -> dict:
        size = os.path.getsize(filepath)
        if size > 10 * 1024 * 1024:
            raise ValueError("Arquivo muito grande (max 10 MB)")
        with open(filepath, "rb") as f:
            files = {"file": (os.path.basename(filepath), f, "application/octet-stream")}
            r = await self._request(
                "POST", "/api/dm-files/upload",
                headers={"Authorization": f"Bearer {self.state.token}"},
                files=files,
            )
        r.raise_for_status()
        return r.json()

    async def download_dm_file(self, file_id: int, save_path: str):
        r = await self._request(
            "GET", f"/api/dm-files/{file_id}/download",
            headers=self._headers(),
        )
        r.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(r.content)
        return save_path

    async def get_notifications(self, offset: int = 0, limit: int = 50) -> list:
        r = await self._request(
            "GET", "/api/notifications",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def mark_notification_read(self, notification_id: int):
        r = await self._request(
            "POST", f"/api/notifications/{notification_id}/read",
            headers=self._headers(),
        )
        r.raise_for_status()

    async def mark_all_notifications_read(self):
        r = await self._request(
            "POST", "/api/notifications/read-all",
            headers=self._headers(),
        )
        r.raise_for_status()

    async def create_group(self, name: str, members: list[str]) -> dict:
        r = await self._request(
            "POST", "/api/groups",
            headers=self._headers(),
            json={"name": name, "members": members},
        )
        if r.status_code == 400:
            raise ValueError(r.json().get("detail", "Erro ao criar grupo"))
        r.raise_for_status()
        return r.json()

    async def list_groups(self) -> list:
        r = await self._request("GET", "/api/groups", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def get_group_messages(self, group_id: int) -> list:
        r = await self._request(
            "GET", f"/api/groups/{group_id}/messages",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def send_group_message(self, group_id: int, content: str, file_id: int | None = None, dm_file_id: int | None = None) -> dict:
        r = await self._request(
            "POST", f"/api/groups/{group_id}/messages",
            headers=self._headers(),
            json={"content": content, "file_id": file_id, "dm_file_id": dm_file_id},
        )
        r.raise_for_status()
        return r.json()

    async def list_group_members(self, group_id: int) -> list:
        r = await self._request(
            "GET", f"/api/groups/{group_id}/members",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def add_group_member(self, group_id: int, username: str) -> dict:
        r = await self._request(
            "POST", f"/api/groups/{group_id}/members",
            headers=self._headers(),
            json={"username": username},
        )
        r.raise_for_status()
        return r.json()

    async def remove_group_member(self, group_id: int, member_id: int) -> dict:
        r = await self._request(
            "DELETE", f"/api/groups/{group_id}/members/{member_id}",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def leave_group(self, group_id: int) -> dict:
        r = await self._request(
            "POST", f"/api/groups/{group_id}/leave",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def close(self):
        await self._client.aclose()
