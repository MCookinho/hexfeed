"""Testes de segurança automatizados para o hexfeed server."""
import io, os, struct, tempfile
from pathlib import Path
import pytest
from conftest import auth_headers


# ════════════════════════════════════════════════════════════════════
# AUTH - Bypass de autenticação
# ════════════════════════════════════════════════════════════════════

class TestAuthBypass:
    def test_no_token_returns_401(self, client):
        r = client.get("/api/users/me")
        assert r.status_code == 401

    def test_invalid_token_returns_401(self, client):
        r = client.get("/api/users/me", headers={"Authorization": "Bearer invalidtoken123"})
        assert r.status_code == 401

    def test_empty_token_returns_401(self, client):
        r = client.get("/api/users/me", headers={"Authorization": "Bearer "})
        assert r.status_code == 401

    def test_malformed_auth_header_returns_401(self, client):
        r = client.get("/api/users/me", headers={"Authorization": "Basic abc123"})
        assert r.status_code == 401

    def test_banned_user_returns_403(self, client, user1):
        conn = __import__("server.database", fromlist=["get_connection"]).get_connection()
        conn.execute("UPDATE users SET banned = 1 WHERE id = ?", (user1["id"],))
        conn.commit()
        conn.close()
        r = client.get("/api/users/me", headers=auth_headers(user1["token"]))
        assert r.status_code == 403

    def test_create_post_without_token(self, client):
        r = client.post("/api/posts", json={"content": "teste"})
        assert r.status_code == 401

    def test_delete_post_without_token(self, client):
        r = client.delete("/api/posts/1")
        assert r.status_code == 401


# ════════════════════════════════════════════════════════════════════
# FILE UPLOAD - Limites de tamanho
# ════════════════════════════════════════════════════════════════════

class TestFileUploadLimits:
    def test_upload_oversized_file_via_content_length(self, client, user1):
        """Middleware rejeita por Content-Length antes de processar."""
        headers = auth_headers(user1["token"])
        headers["Content-Length"] = str(11 * 1024 * 1024)
        headers["Content-Type"] = "application/octet-stream"
        r = client.post("/api/files/upload", content=b"x" * 100, headers=headers)
        assert r.status_code == 413

    def test_upload_oversized_file_via_stream(self, client, user1):
        """_read_upload_stream aborta ao atingir o limite."""
        big = b"x" * (11 * 1024 * 1024)
        r = client.post(
            "/api/files/upload",
            files={"file": ("big.bin", io.BytesIO(big), "application/octet-stream")},
            headers=auth_headers(user1["token"]),
        )
        assert r.status_code == 413

    def test_upload_file_within_limit(self, client, user1):
        small = b"hello world" * 100
        r = client.post(
            "/api/files/upload",
            files={"file": ("small.txt", io.BytesIO(small), "text/plain")},
            headers=auth_headers(user1["token"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["size"] == len(small)

    def test_upload_oversized_dm_file(self, client, user1):
        big = b"x" * (11 * 1024 * 1024)
        r = client.post(
            "/api/dm-files/upload",
            files={"file": ("big.bin", io.BytesIO(big), "application/octet-stream")},
            headers=auth_headers(user1["token"]),
        )
        assert r.status_code == 413

    def test_upload_oversized_avatar(self, client, user1):
        big = b"x" * (3 * 1024 * 1024)
        r = client.post(
            "/api/users/avatar",
            files={"file": ("big.png", io.BytesIO(big), "image/png")},
            headers=auth_headers(user1["token"]),
        )
        assert r.status_code == 413

    def test_avatar_wrong_extension(self, client, user1):
        small = b"x" * 100
        r = client.post(
            "/api/users/avatar",
            files={"file": ("evil.exe", io.BytesIO(small), "application/x-msdownload")},
            headers=auth_headers(user1["token"]),
        )
        assert r.status_code == 400


# ════════════════════════════════════════════════════════════════════
# SQL INJECTION
# ════════════════════════════════════════════════════════════════════

class TestSQLInjection:
    def test_sql_injection_in_username_registration(self, client):
        r = client.get("/api/auth/challenge")
        assert r.status_code == 200
        chal = r.json()

        payloads = [
            "'; DROP TABLE users; --",
            "' OR '1'='1",
            "admin' --",
            "'; SELECT * FROM tokens; --",
            "'; DELETE FROM posts; --",
        ]
        for payload in payloads:
            r = client.post("/api/auth/register", json={
                "username": payload,
                "password": "senha123",
                "pgp_public_key": "",
                "pgp_private_key": "",
                "email": "",
                "pow_challenge": chal["challenge"],
                "pow_nonce": 0,
                "math_answers": [0, 0],
            })
            # Deve ser 400 (PoW/math inválido) porque o username não passa pelo regex
            # O importante é NÃO ser 500 (crash do banco)
            assert r.status_code != 500, f"SQL injection em username causou 500: {payload}"

    def test_sql_injection_in_search(self, client, user1):
        payloads = [
            "'; DROP TABLE users; --",
            "test' OR '1'='1",
            "%' OR 1=1 --",
        ]
        for payload in payloads:
            r = client.get(
                f"/api/users/search/list?q={payload}",
                headers=auth_headers(user1["token"]),
            )
            # LIKE com % é normal, só não pode crashar
            assert r.status_code != 500, f"SQL injection em search causou 500: {payload}"

    def test_sql_injection_in_post_content(self, client, user1):
        r = client.post(
            "/api/posts",
            json={"content": "'; DROP TABLE users; --"},
            headers=auth_headers(user1["token"]),
        )
        assert r.status_code == 200, "Post com SQL no conteúdo deve ser aceito (parametrização)"
        post_id = r.json()["id"]
        r = client.delete(f"/api/posts/{post_id}", headers=auth_headers(user1["token"]))
        assert r.status_code == 200


# ════════════════════════════════════════════════════════════════════
# AUTHORIZATION - Acesso a recursos de outros usuários
# ════════════════════════════════════════════════════════════════════

class TestAuthorization:
    def test_cannot_delete_others_post(self, client, user1, user2):
        # user1 cria um post
        r = client.post("/api/posts", json={"content": "post do 1"}, headers=auth_headers(user1["token"]))
        assert r.status_code == 200
        post_id = r.json()["id"]

        # user2 tenta deletar
        r = client.delete(f"/api/posts/{post_id}", headers=auth_headers(user2["token"]))
        assert r.status_code == 403

    def test_cannot_delete_others_comment(self, client, user1, user2):
        # user1 cria post
        r = client.post("/api/posts", json={"content": "post alvo"}, headers=auth_headers(user1["token"]))
        post_id = r.json()["id"]

        # user2 comenta
        r = client.post(f"/api/posts/{post_id}/comments", json={"content": "comentario do 2"},
                         headers=auth_headers(user2["token"]))
        assert r.status_code == 201
        comment_id = r.json()["id"]

        # user1 tenta deletar comentário do user2
        r = client.delete(f"/api/posts/{post_id}/comments/{comment_id}",
                          headers=auth_headers(user1["token"]))
        assert r.status_code == 403

    def test_cannot_delete_others_file(self, client, user1, user2):
        # user1 faz upload de arquivo
        r = client.post("/api/files/upload",
                        files={"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")},
                        headers=auth_headers(user1["token"]))
        assert r.status_code == 200
        file_id = r.json()["id"]

        # user2 tenta deletar
        r = client.delete(f"/api/files/{file_id}", headers=auth_headers(user2["token"]))
        assert r.status_code == 403

    def test_cannot_like_nonexistent_post(self, client, user1):
        r = client.post("/api/posts/99999/like", headers=auth_headers(user1["token"]))
        assert r.status_code == 404

    def test_cannot_comment_on_nonexistent_post(self, client, user1):
        r = client.post("/api/posts/99999/comments", json={"content": "teste"},
                        headers=auth_headers(user1["token"]))
        assert r.status_code == 404


# ════════════════════════════════════════════════════════════════════
# BLOCK - Usuários bloqueados
# ════════════════════════════════════════════════════════════════════

class TestBlock:
    def test_block_self_returns_400(self, client, user1):
        r = client.post(f"/api/users/{user1['username']}/block",
                        headers=auth_headers(user1["token"]))
        assert r.status_code == 400

    def test_block_nonexistent_user_returns_404(self, client, user1):
        r = client.post("/api/users/naoexiste/block",
                        headers=auth_headers(user1["token"]))
        assert r.status_code == 404

    def test_cannot_follow_self(self, client, user1):
        r = client.post(f"/api/follow/{user1['username']}",
                        headers=auth_headers(user1["token"]))
        assert r.status_code == 400


# ════════════════════════════════════════════════════════════════════
# INPUT VALIDATION - Campos maliciosos
# ════════════════════════════════════════════════════════════════════

class TestInputValidation:
    def test_oversized_display_name(self, client, user1):
        long_name = "x" * 200
        r = client.put("/api/users/profile",
                       json={"display_name": long_name, "bio": "", "email": ""},
                       headers=auth_headers(user1["token"]))
        assert r.status_code == 422  # Pydantic rejeita

    def test_oversized_bio(self, client, user1):
        long_bio = "x" * 2000
        r = client.put("/api/users/profile",
                       json={"display_name": "ok", "bio": long_bio, "email": ""},
                       headers=auth_headers(user1["token"]))
        assert r.status_code == 422

    def test_oversized_post_content(self, client, user1):
        r = client.post("/api/posts",
                        json={"content": "x" * 501},
                        headers=auth_headers(user1["token"]))
        assert r.status_code == 422

    def test_empty_post_content(self, client, user1):
        r = client.post("/api/posts",
                        json={"content": ""},
                        headers=auth_headers(user1["token"]))
        assert r.status_code == 422

    def test_username_with_special_chars_registration(self, client):
        r = client.get("/api/auth/challenge")
        assert r.status_code == 200
        chal = r.json()
        r = client.post("/api/auth/register", json={
            "username": "<script>alert('xss')</script>",
            "password": "senha123",
            "pow_challenge": chal["challenge"],
            "pow_nonce": 0,
            "math_answers": [0, 0],
        })
        # Deve ser 422 (regex do username rejeita)
        assert r.status_code == 422

    def test_login_with_long_username(self, client):
        r = client.post("/api/auth/login", json={
            "username": "x" * 1000,
            "password": "x" * 1000,
        })
        assert r.status_code == 422  # Pydantic max_length rejeita

    def test_empty_strings_in_post(self, client, user1):
        """Strings vazias em campos opcionais não devem crashar."""
        r = client.put("/api/users/profile",
                       json={"display_name": "", "bio": "", "email": ""},
                       headers=auth_headers(user1["token"]))
        assert r.status_code == 200


# ════════════════════════════════════════════════════════════════════
# RATE LIMITING
# ════════════════════════════════════════════════════════════════════

class TestRateLimiting:
    @pytest.mark.skip(reason="Requer 60+ requisições em 1 minuto; teste manual")
    def test_rate_limit_exceeded(self, client, user1):
        for _ in range(61):
            r = client.get("/api/health", headers=auth_headers(user1["token"]))
        assert r.status_code == 429


# ════════════════════════════════════════════════════════════════════
# FOLLOW - Seguir usuário inexistente
# ════════════════════════════════════════════════════════════════════

class TestFollow:
    def test_follow_nonexistent_user(self, client, user1):
        r = client.post("/api/follow/naoexiste", headers=auth_headers(user1["token"]))
        assert r.status_code == 404

    def test_follow_twice_is_idempotent(self, client, user1, user2):
        r = client.post(f"/api/follow/{user2['username']}", headers=auth_headers(user1["token"]))
        assert r.status_code == 200
        r = client.post(f"/api/follow/{user2['username']}", headers=auth_headers(user1["token"]))
        assert r.status_code == 200  # INSERT OR IGNORE

    def test_unfollow_nonexistent_user(self, client, user1):
        r = client.delete("/api/follow/naoexiste", headers=auth_headers(user1["token"]))
        assert r.status_code == 404


# ════════════════════════════════════════════════════════════════════
# LOGIN - Tentativas inválidas
# ════════════════════════════════════════════════════════════════════

class TestLogin:
    def test_login_wrong_password(self, client, user1):
        r = client.post("/api/auth/login", json={
            "username": user1["username"],
            "password": "senha_errada",
        })
        assert r.status_code == 401

    def test_login_nonexistent_user(self, client):
        r = client.post("/api/auth/login", json={
            "username": "naoexiste",
            "password": "qualquer",
        })
        assert r.status_code == 401


# ════════════════════════════════════════════════════════════════════
# HEALTH - Endpoint básico sempre funcional
# ════════════════════════════════════════════════════════════════════

class TestHealth:
    def test_health_endpoint(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
