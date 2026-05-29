"""
hexfeed - rotas da API
Todos os endpoints REST do servidor hexfeed.
Organizado por seções: Auth, Users, Follows, Posts, Likes, Chat, Files, DMs.
"""

import uuid, time, threading, hashlib, secrets, random, re
from collections import defaultdict
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Query, Request
from fastapi.responses import FileResponse, Response
from server.database import get_connection, close_connection, DM_UPLOAD_DIR
from server.crypto import encrypt_file_bytes, decrypt_file_bytes
from server.auth import (
    hash_password, verify_password, create_token, get_user_from_token,
    delete_token, validate_pgp_key, verify_pgp_private_key,
)
from server.models import (
    RegisterRequest, LoginRequest, CreatePostRequest, CreateCommentRequest,
    UpdateProfileRequest,
    SendChatRequest, SendDMRequest,
    UserResponse, PostResponse, CommentResponse,
    ChatMessageResponse, DMResponse, ConversationResponse,
    FileResponse as FileResp, AuthResponse, SearchResponse, ChallengeResponse,
    NotificationResponse, BlockResponse, FollowListResponse,
    CreateGroupRequest, GroupResponse, GroupMessageResponse,
    GroupMemberResponse, AddGroupMemberRequest, SendGroupMessageRequest,
)

# Cria o roteador com prefixo /api
router = APIRouter(prefix="/api")

# Diretório onde os arquivos enviados serão salvos
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
CHUNK_SIZE = 64 * 1024  # 64 KB - lê em pedaços para não estourar a RAM


async def _read_upload_stream(file: UploadFile, file_path: Path, max_size: int, request: Request | None = None):
    """Lê o upload em chunks de 64KB, escrevendo no disco.
    Aborta imediatamente se o tamanho total exceder max_size.
    Remove o arquivo parcial se der erro."""
    cl = request.headers.get("content-length") if request else None
    if cl and int(cl) > max_size:
        raise HTTPException(413, f"Arquivo muito grande (max {max_size // 1024 // 1024} MB)")

    total = 0
    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    raise HTTPException(413, f"Arquivo muito grande (max {max_size // 1024 // 1024} MB)")
                f.write(chunk)
    except BaseException:
        if file_path.exists():
            file_path.unlink()
        raise
    return total


# ════════════════════════════════════════════════════════════════════
# ANTI-BOT: rate limit, Proof-of-Work, desafio matemático
# ════════════════════════════════════════════════════════════════════

REGISTER_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
POW_CHALLENGES: dict[str, dict] = {}


def check_rate_limit(ip: str):
    now = time.time()
    window = int(get_setting("rate_window_hours", "1")) * 3600
    max_attempts = int(get_setting("max_register_attempts", "15"))
    REGISTER_ATTEMPTS[ip] = [t for t in REGISTER_ATTEMPTS[ip] if now - t < window]
    if len(REGISTER_ATTEMPTS[ip]) >= max_attempts:
        raise HTTPException(429, "Muitas tentativas de registro. Aguarde 1 hora.")


def record_attempt(ip: str):
    """Registra uma tentativa de registro para rate limiting."""
    REGISTER_ATTEMPTS[ip].append(time.time())


def generate_pow_challenge() -> str:
    """Gera um desafio PoW aleatório (32 caracteres hex)."""
    return secrets.token_hex(16)


def verify_pow(challenge: str, nonce: int, difficulty: int) -> bool:
    """
    Verifica se o nonce produz um hash SHA-256 com 'difficulty' bits zero à esquerda.
    O cliente deve encontrar um nonce que satisfaça esta condição.
    """
    data = f"{challenge}{nonce}".encode()
    h = hashlib.sha256(data).hexdigest()
    bits = bin(int(h, 16))[2:].zfill(256)
    return bits.startswith("0" * difficulty)


def generate_math_questions() -> list[dict]:
    """Gera 2 perguntas matemáticas aleatórias (soma/subtração/multiplicação)."""
    questions = []
    for _ in range(2):
        a = random.randint(1, 50)
        b = random.randint(1, 50)
        op = random.choice(["+", "-", "*"])
        ans = {"+": a + b, "-": a - b, "*": a * b}[op]
        questions.append({"q": f"{a} {op} {b}", "a": ans})
    return questions


def cleanup_challenges():
    """Remove desafios PoW expirados (mais de 5 minutos)."""
    now = time.time()
    expired = [k for k, v in POW_CHALLENGES.items() if now - v["created_at"] > 300]
    for k in expired:
        del POW_CHALLENGES[k]


def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    except Exception:
        return default
    finally:
        conn.close()


def require_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Token ausente ou inválido")
    user = get_user_from_token(auth[7:])
    if not user:
        raise HTTPException(401, "Token inválido ou expirado")
    if user.get("banned"):
        raise HTTPException(403, "Conta banida")
    return user


# ════════════════════════════════════════════════════════════════════
# FUNÇÕES AUXILIARES: conversão de dados do banco para resposta da API
# ════════════════════════════════════════════════════════════════════


def user_to_response(u: dict, current_user_id: int | None = None) -> dict:
    """
    Converte uma linha do banco (dict) para o formato de resposta da API,
    incluindo contagens de seguidores, seguindo e posts.
    """
    conn = get_connection()
    # Consulta única com subqueries para evitar 3 viagens ao banco
    counts = conn.execute(
        """SELECT
              (SELECT COUNT(*) FROM follows WHERE following_id = ?) AS follower_count,
              (SELECT COUNT(*) FROM follows WHERE follower_id = ?) AS following_count,
              (SELECT COUNT(*) FROM posts WHERE user_id = ?) AS post_count""",
        (u["id"], u["id"], u["id"]),
    ).fetchone()
    followers = counts["follower_count"]
    following = counts["following_count"]
    post_count = counts["post_count"]

    # Verifica se o usuário logado segue este usuário
    is_following = False
    is_blocked = False
    if current_user_id:
        row = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id = ? AND following_id = ?",
            (current_user_id, u["id"]),
        ).fetchone()
        is_following = row is not None
        row = conn.execute(
            "SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
            (current_user_id, u["id"]),
        ).fetchone()
        is_blocked = row is not None
    conn.close()

    return {
        "id": u["id"],
        "username": u["username"],
        "display_name": u["display_name"] or "",
        "bio": u["bio"] or "",
        "avatar_path": u["avatar_path"] or "",
        "email": u.get("email") or "",
        "created_at": u["created_at"],
        "follower_count": followers,
        "following_count": following,
        "post_count": post_count,
        "is_following": is_following,
        "is_blocked": is_blocked,
    }

        
def post_to_response(p: dict, current_user_id: int | None = None) -> dict:
    conn = get_connection()
    counts = conn.execute(
        """SELECT
        (SELECT COUNT(*) FROM likes WHERE post_id = ?) AS like_count,
        (SELECT COUNT(*) FROM comments WHERE post_id = ?) AS reply_count""",
        (p["id"], p["id"]),
    ).fetchone()
    likes = counts["like_count"]
    replies = counts["reply_count"]

    is_liked = False
    if current_user_id:
        row = conn.execute(
            "SELECT 1 FROM likes WHERE user_id = ? AND post_id = ?",
            (current_user_id, p["id"]),
        ).fetchone()
        is_liked = row is not None

    user = conn.execute(
        "SELECT * FROM users WHERE id = ?", (p["user_id"],)
    ).fetchone()

    file_id = p.get("file_id")
    file_name, file_type, file_size = _fetch_file_meta(conn, file_id)

    conn.close()
    return {
        "id": p["id"],
        "user_id": p["user_id"],
        "username": user["username"] if user else "deleted",
        "display_name": user["display_name"] if user else "",
        "content": p["content"],
        "reply_to": p["reply_to"],
        "created_at": p["created_at"],
        "edited_at": p["edited_at"],
        "like_count": likes,
        "reply_count": replies,
        "is_liked": is_liked,
        "file_id": file_id,
        "file_name": file_name,
        "file_type": file_type,
        "file_size": file_size,
    }
        
        
def _create_notification(conn, user_id: int, type: str, actor_id: int, post_id: int | None = None, comment_id: int | None = None):
    if user_id == actor_id:
        return
    conn.execute(
        "INSERT INTO notifications (user_id, type, actor_id, post_id, comment_id) VALUES (?, ?, ?, ?, ?)",
        (user_id, type, actor_id, post_id, comment_id),
    )
    conn.commit()


MENTION_RE = re.compile(r'@([a-zA-Z0-9_]+)')


def _parse_mentions(conn, text: str, actor_id: int, post_id: int | None = None, comment_id: int | None = None):
    mentioned = set(MENTION_RE.findall(text))
    for username in mentioned:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row:
            _create_notification(conn, row["id"], "mention", actor_id, post_id, comment_id)


def _check_block(conn, user_id: int, target_user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM blocks WHERE (blocker_id = ? AND blocked_id = ?) OR (blocker_id = ? AND blocked_id = ?)",
        (user_id, target_user_id, target_user_id, user_id),
    ).fetchone()
    return row is not None


def _fetch_file_meta(conn, file_id: int | None):
    if not file_id:
        return None, None, None
    f = conn.execute(
        "SELECT original_name, content_type, size FROM files WHERE id = ?", (file_id,)
    ).fetchone()
    if not f:
        return None, None, None
    return f["original_name"], f["content_type"], f["size"]


def _fetch_dm_file_name(conn, dm_file_id: int | None):
    if not dm_file_id:
        return None
    f = conn.execute(
        "SELECT original_name FROM dm_files WHERE id = ?", (dm_file_id,)
    ).fetchone()
    return f["original_name"] if f else None


def _batch_fetch_file_meta(conn, rows: list):
    files_map = {}
    dm_files_map = {}
    file_ids = {r["file_id"] for r in rows if r["file_id"]}
    if file_ids:
        ph = ",".join("?" * len(file_ids))
        for f in conn.execute(
            f"SELECT id, original_name, content_type, size FROM files WHERE id IN ({ph})",
            tuple(file_ids),
        ).fetchall():
            files_map[f["id"]] = f
    dm_file_ids = {r["dm_file_id"] for r in rows if r["dm_file_id"]}
    if dm_file_ids:
        ph = ",".join("?" * len(dm_file_ids))
        for f in conn.execute(
            f"SELECT id, original_name FROM dm_files WHERE id IN ({ph})",
            tuple(dm_file_ids),
        ).fetchall():
            dm_files_map[f["id"]] = f
    return files_map, dm_files_map


# ════════════════════════════════════════════════════════════════════
# AUTH - Cadastro, Login, Logout
# ════════════════════════════════════════════════════════════════════


@router.get("/auth/challenge", response_model=ChallengeResponse)
def get_challenge(request: Request):
    """Retorna um desafio PoW + perguntas matemáticas para o registro."""
    cleanup_challenges()
    challenge = generate_pow_challenge()
    questions = generate_math_questions()
    diff = int(get_setting("pow_difficulty", "20"))
    POW_CHALLENGES[challenge] = {
        "difficulty": diff,
        "questions": questions,
        "created_at": time.time(),
    }
    return ChallengeResponse(
        challenge=challenge,
        difficulty=diff,
        questions=[{"id": i, "q": q["q"]} for i, q in enumerate(questions)],
    )


@router.post("/auth/register", response_model=AuthResponse)
def register(body: RegisterRequest, request: Request):
    """
    Cadastra um novo usuário.
    Passos: rate limit -> PoW -> desafio matemático -> verificação PGP -> insert.
    """
    ip = request.client.host
    check_rate_limit(ip)

    # Verifica prova-de-trabalho (hashcash-style)
    challenge_data = POW_CHALLENGES.pop(body.pow_challenge, None)
    if not challenge_data:
        raise HTTPException(400, "Challenge inválido ou expirado. Solicite um novo.")
    if not verify_pow(body.pow_challenge, body.pow_nonce, challenge_data["difficulty"]):
        record_attempt(ip)
        raise HTTPException(400, "Prova de trabalho inválida. Solicite um novo desafio.")

    # Verifica respostas do desafio matemático
    expected = [q["a"] for q in challenge_data["questions"]]
    if list(body.math_answers) != expected:
        record_attempt(ip)
        raise HTTPException(400, "Respostas do desafio matemático incorretas.")

    # Conexão única para todo o registro (evita abrir/fechar duas vezes)
    conn = get_connection()
    existing = conn.execute(
    "SELECT id FROM users WHERE username = ?", (body.username,)
    ).fetchone()
    if existing:
        record_attempt(ip)
        conn.close()
        raise HTTPException(409, "Nome de usuário já existe")

    pgp_pub = (body.pgp_public_key or "").strip()
    pgp_fp = ""

    if pgp_pub:
        valid, fp_or_err = validate_pgp_key(pgp_pub)
        if not valid:
            record_attempt(ip)
            conn.close()
            raise HTTPException(400, fp_or_err)
        pgp_fp = fp_or_err

    pw_hash = hash_password(body.password)
    cursor = conn.execute(
        """INSERT INTO users (username, password_hash, pgp_public_key, pgp_fingerprint,
        pgp_private_key_hash, email)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (body.username, pw_hash, pgp_pub, pgp_fp, "", body.email or ""),
    )
    user_id = cursor.lastrowid

    token = create_token(user_id, conn=conn)
    user = dict(conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone())

    conn.close()
    return AuthResponse(
        token=token,
        user=UserResponse(**user_to_response(user, user_id)),
    )
        
        
@router.post("/auth/login", response_model=AuthResponse)
def login(body: LoginRequest, request: Request = None):
    conn = get_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?", (body.username,)
    ).fetchone()

    client_ip = request.client.host if request and request.client else ""

    if not user or not verify_password(body.password, user["password_hash"]):
        conn.execute(
            "INSERT INTO login_attempts (username, ip, success) VALUES (?, ?, 0)",
            (body.username, client_ip),
        )
        conn.commit()
        conn.close()
        raise HTTPException(401, "Usuário ou senha inválidos")

    if user["banned"]:
        conn.execute(
            "INSERT INTO login_attempts (username, ip, success) VALUES (?, ?, 0)",
            (body.username, client_ip),
        )
        conn.commit()
        conn.close()
        raise HTTPException(403, "Conta banida")

    if user["pgp_public_key"]:
        priv_key = (body.pgp_private_key or "").strip()
        if not priv_key:
            conn.execute(
                "INSERT INTO login_attempts (username, ip, success) VALUES (?, ?, 0)",
                (body.username, client_ip),
            )
            conn.commit()
            conn.close()
            raise HTTPException(401, "Chave PGP privada é necessária para esta conta")
        if not verify_pgp_private_key(priv_key, user["pgp_fingerprint"]):
            conn.execute(
                "INSERT INTO login_attempts (username, ip, success) VALUES (?, ?, 0)",
                (body.username, client_ip),
            )
            conn.commit()
            conn.close()
            raise HTTPException(401, "Chave PGP privada inválida")

    conn.execute(
        "INSERT INTO login_attempts (username, ip, success) VALUES (?, ?, 1)",
        (body.username, client_ip),
    )
    conn.commit()

    token = create_token(user["id"], conn=conn)

    conn.close()
    return AuthResponse(
        token=token,
        user=UserResponse(**user_to_response(dict(user), user["id"])),
    )
        
        
# A rota de logout está no main.py porque precisamos do Request diretamente


# ════════════════════════════════════════════════════════════════════
# USERS - Perfil, busca
# ════════════════════════════════════════════════════════════════════


@router.get("/users/me")
def get_me(user: dict = Depends(require_user)):
    """Retorna os dados do usuário logado."""
    conn = get_connection()
    u = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    conn.close()
    return user_to_response(dict(u), user["id"])
        
        
@router.get("/users/{username}")
def get_user(username: str, user: dict = Depends(require_user)):
    """Retorna os dados de um usuário específico pelo username."""
    conn = get_connection()
    u = conn.execute(
    "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not u:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    conn.close()
    return user_to_response(dict(u), user["id"])
        
        
@router.put("/users/profile")
def update_profile(body: UpdateProfileRequest, user: dict = Depends(require_user)):
    """Atualiza nome de exibição, bio e email do perfil."""
    conn = get_connection()
    conn.execute(
    "UPDATE users SET display_name = ?, bio = ?, email = ? WHERE id = ?",
    (body.display_name or "", body.bio or "", body.email or "", user["id"]),
    )
    conn.commit()
    u = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    conn.close()
    return user_to_response(dict(u), user["id"])
        
        
@router.get("/users/search/list")
def search_users(
    q: str = Query(min_length=1, description="Termo de busca"),
    limit: int = Query(20, le=50, description="Máximo de resultados"),
    user: dict = Depends(require_user),
):
    """Busca usuários por username ou nome de exibição (LIKE %termo%)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM users
        WHERE username LIKE ? OR display_name LIKE ?
        ORDER BY created_at DESC LIMIT ?""",
        (f"%{q}%", f"%{q}%", limit),
    ).fetchall()
    result = [user_to_response(dict(u), user["id"]) for u in rows]
    conn.close()
    return result
        
        
@router.post("/users/{username}/block")
def block_user(username: str, user: dict = Depends(require_user)):
    conn = get_connection()
    target = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    if target["id"] == user["id"]:
        conn.close()
        raise HTTPException(400, "Você não pode bloquear a si mesmo")
    conn.execute(
        "INSERT OR IGNORE INTO blocks (blocker_id, blocked_id) VALUES (?, ?)",
        (user["id"], target["id"]),
    )
    conn.execute(
        "DELETE FROM follows WHERE follower_id = ? AND following_id = ?",
        (user["id"], target["id"]),
    )
    conn.execute(
        "DELETE FROM follows WHERE follower_id = ? AND following_id = ?",
        (target["id"], user["id"]),
    )
    # Deleta DM entre os dois
    u1, u2 = sorted([user["id"], target["id"]])
    conv = conn.execute(
        "SELECT id FROM conversations WHERE user1_id = ? AND user2_id = ?",
        (u1, u2),
    ).fetchone()
    if conv:
        conn.execute("DELETE FROM direct_messages WHERE conversation_id = ?", (conv["id"],))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv["id"],))
        # Remove ambos de grupos compartilhados e adiciona exclusão permanente
        shared = conn.execute(
            """SELECT gm1.group_id FROM group_members gm1
            JOIN group_members gm2 ON gm1.group_id = gm2.group_id
            WHERE gm1.user_id = ? AND gm2.user_id = ?""",
            (user["id"], target["id"]),
        ).fetchall()
        for row in shared:
            gid = row["group_id"]
            conn.execute("DELETE FROM group_members WHERE group_id = ? AND user_id IN (?, ?)",
                (gid, user["id"], target["id"]))
            remaining = conn.execute("SELECT COUNT(*) FROM group_members WHERE group_id = ?", (gid,)).fetchone()[0]
            if remaining == 0:
                conn.execute("DELETE FROM group_messages WHERE group_id = ?", (gid,))
                conn.execute("DELETE FROM user_groups WHERE id = ?", (gid,))
                conn.execute(
                    "INSERT OR IGNORE INTO group_exclusion_pairs (user1_id, user2_id) VALUES (?, ?)",
                    (u1, u2),
                )
    conn.commit()
    conn.close()
    return {"status": "blocked"}
        
        
@router.post("/users/{username}/unblock")
def unblock_user(username: str, user: dict = Depends(require_user)):
    conn = get_connection()
    target = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    conn.execute(
        "DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
        (user["id"], target["id"]),
    )
    conn.commit()
    conn.close()
    return {"status": "unblocked"}
        
        
@router.get("/users/me/blocks")
def list_blocks(user: dict = Depends(require_user)):
    conn = get_connection()
    rows = conn.execute(
    """SELECT b.blocked_id, b.created_at, u.username, u.display_name
    FROM blocks b
    JOIN users u ON b.blocked_id = u.id
    WHERE b.blocker_id = ?
    ORDER BY b.created_at DESC""",
    (user["id"],),
    ).fetchall()
    conn.close()
    return [
    BlockResponse(
    blocked_id=r["blocked_id"],
    blocked_username=r["username"],
    blocked_display_name=r["display_name"] or "",
    created_at=r["created_at"],
    )
    for r in rows
        ]
        
        
# ════════════════════════════════════════════════════════════════════
# FOLLOWS - Seguir / Deixar de seguir
# ════════════════════════════════════════════════════════════════════


@router.post("/follow/{username}")
def follow_user(username: str, user: dict = Depends(require_user)):
    """Seguir um usuário."""
    conn = get_connection()
    target = conn.execute(
    "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    if target["id"] == user["id"]:
        conn.close()
        raise HTTPException(400, "Você não pode seguir a si mesmo")

    conn.execute(
        "INSERT OR IGNORE INTO follows (follower_id, following_id) VALUES (?, ?)",
        (user["id"], target["id"]),
    )
    conn.commit()
    _create_notification(conn, target["id"], "follow", user["id"])
    conn.close()
    return {"status": "followed"}
        
        
@router.delete("/follow/{username}")
def unfollow_user(username: str, user: dict = Depends(require_user)):
    """Deixar de seguir um usuário."""
    conn = get_connection()
    target = conn.execute(
    "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    conn.execute(
        "DELETE FROM follows WHERE follower_id = ? AND following_id = ?",
        (user["id"], target["id"]),
    )
    conn.commit()
    conn.close()
    return {"status": "unfollowed"}
        
        
@router.get("/users/{username}/followers")
def get_followers(
    username: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, le=50),
    user: dict = Depends(require_user),
):
    conn = get_connection()
    target = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    rows = conn.execute(
        """SELECT u.* FROM follows f
        JOIN users u ON f.follower_id = u.id
        WHERE f.following_id = ?
        ORDER BY f.created_at DESC
        LIMIT ? OFFSET ?""",
        (target["id"], limit, offset),
    ).fetchall()
    conn.close()
    return FollowListResponse(users=[
        UserResponse(**user_to_response(dict(u), user["id"])) for u in rows
    ])
        
        
@router.get("/users/{username}/following")
def get_following(
    username: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, le=50),
    user: dict = Depends(require_user),
):
    conn = get_connection()
    target = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    rows = conn.execute(
        """SELECT u.* FROM follows f
        JOIN users u ON f.following_id = u.id
        WHERE f.follower_id = ?
        ORDER BY f.created_at DESC
        LIMIT ? OFFSET ?""",
        (target["id"], limit, offset),
    ).fetchall()
    conn.close()
    return FollowListResponse(users=[
        UserResponse(**user_to_response(dict(u), user["id"])) for u in rows
    ])
        
        
# ════════════════════════════════════════════════════════════════════
# POSTS - Criar, listar, buscar, deletar
# ════════════════════════════════════════════════════════════════════


@router.post("/posts", response_model=PostResponse)
def create_post(body: CreatePostRequest, user: dict = Depends(require_user)):
    """Cria uma nova publicação (texto e/ou arquivo anexado)."""
    # Se for resposta, verifica se o post original existe
    if body.reply_to:
        conn = get_connection()
        parent = conn.execute(
        "SELECT id FROM posts WHERE id = ?", (body.reply_to,)
        ).fetchone()
        if not parent:
            conn.close()
            raise HTTPException(404, "Post original não encontrado")
            
    # Se anexar arquivo, verifica se existe e pertence ao usuário
    if body.file_id:
        conn = get_connection()
        f = conn.execute(
            "SELECT id, user_id FROM files WHERE id = ?", (body.file_id,)
        ).fetchone()
        if not f:
            conn.close()
            raise HTTPException(404, "Arquivo não encontrado")
        if f["user_id"] != user["id"]:
            conn.close()
            raise HTTPException(403, "Não é possível anexar arquivo de outro usuário")
            
    conn = get_connection()
    cursor = conn.execute(
    "INSERT INTO posts (user_id, content, reply_to, file_id) VALUES (?, ?, ?, ?)",
    (user["id"], body.content, body.reply_to, body.file_id),
    )
    conn.commit()
    _parse_mentions(conn, body.content, user["id"], post_id=cursor.lastrowid)
    p = conn.execute(
    "SELECT * FROM posts WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    conn.close()
    return PostResponse(**post_to_response(dict(p), user["id"]))
        
        
@router.get("/posts")
def get_feed(
    offset: int = Query(0, ge=0, description="Pular N posts"),
    limit: int = Query(20, le=50, description="Máximo de posts"),
    user: dict = Depends(require_user),
):
    """
    Timeline: mostra posts dos usuários que você segue + seus próprios posts.
    Ordenado do mais recente para o mais antigo.
    """
    conn = get_connection()
    rows = conn.execute(
        """SELECT p.* FROM posts p
        WHERE p.user_id IN (
        SELECT following_id FROM follows WHERE follower_id = ?
        UNION ALL SELECT ?
        )
        AND p.user_id NOT IN (
        SELECT blocked_id FROM blocks WHERE blocker_id = ?
        UNION ALL SELECT blocker_id FROM blocks WHERE blocked_id = ?
        )
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?""",
        (user["id"], user["id"], user["id"], user["id"], limit, offset),
    ).fetchall()
    result = [PostResponse(**post_to_response(dict(p), user["id"])) for p in rows]
    conn.close()
    return result
        
        
@router.get("/posts/global")
def get_global_feed(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, le=50),
    user: dict = Depends(require_user),
):
    """Timeline global: TODOS os posts do servidor, do mais recente."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT p.* FROM posts p
        WHERE p.user_id NOT IN (
        SELECT blocked_id FROM blocks WHERE blocker_id = ?
        UNION ALL SELECT blocker_id FROM blocks WHERE blocked_id = ?
        )
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?""",
        (user["id"], user["id"], limit, offset),
    ).fetchall()
    result = [PostResponse(**post_to_response(dict(p), user["id"])) for p in rows]
    conn.close()
    return result
        
        
@router.get("/posts/user/{username}")
def get_user_posts(
    username: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, le=50),
    user: dict = Depends(require_user),
):
    """Retorna todos os posts de um usuário específico."""
    conn = get_connection()
    target = conn.execute(
    "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    rows = conn.execute(
        "SELECT * FROM posts WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (target["id"], limit, offset),
    ).fetchall()
    conn.close()
    return [PostResponse(**post_to_response(dict(p), user["id"])) for p in rows]
        
        
@router.get("/posts/search")
def search_posts(
    q: str = Query(min_length=1, description="Termo de busca"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, le=50),
    user: dict = Depends(require_user),
):
    """Busca posts pelo conteúdo (LIKE %termo%)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM posts
        WHERE content LIKE ?
        ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (f"%{q}%", limit, offset),
    ).fetchall()
    result = [PostResponse(**post_to_response(dict(p), user["id"])) for p in rows]
    conn.close()
    return result
        
        
@router.get("/search")
def search_all(
    q: str = Query(min_length=1, description="Termo de busca"),
    include_users: bool = Query(True, description="Incluir usuários nos resultados"),
    include_posts: bool = Query(True, description="Incluir posts nos resultados"),
    include_files: bool = Query(False, description="Incluir arquivos nos resultados"),
    date_from: str = Query("", description="Filtrar a partir desta data (YYYY-MM-DD)"),
    date_to: str = Query("", description="Filtrar até esta data (YYYY-MM-DD)"),
    username: str = Query("", description="Filtrar por nome de usuário"),
    limit: int = Query(20, le=50),
    user: dict = Depends(require_user),
):
    """Busca unificada com filtros avançados (data, username, tipo de conteúdo)."""
    conn = get_connection()
    users_res: list = []
    posts_res: list = []
    files_res: list = []
        
    # ── Filtros comuns de data ──
    date_cond = ""
    date_params: list = []
    if date_from:
        date_cond += " AND created_at >= ?"
        date_params.append(date_from)
        if date_to:
            date_cond += " AND created_at <= ?"
            date_params.append(date_to + " 23:59:59")
        
    # ── Busca de usuários ──
    if include_users:
        rows = conn.execute(
            f"""SELECT * FROM users
            WHERE (username LIKE ? OR display_name LIKE ?)
            {date_cond}
            ORDER BY created_at DESC LIMIT ?""",
            (f"%{q}%", f"%{q}%", *date_params, limit),
        ).fetchall()
        users_res = [user_to_response(dict(u), user["id"]) for u in rows]

    # ── Busca de posts ──
    if include_posts:
        user_cond = ""
        user_params: list = []
        if username:
            u_row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            if u_row:
                user_cond = " AND user_id = ?"
                user_params.append(u_row["id"])
            else:
                user_cond = " AND 1=0"

        rows = conn.execute(
            f"""SELECT * FROM posts
            WHERE content LIKE ?
            {user_cond}
            {date_cond}
            ORDER BY created_at DESC LIMIT ?""",
            (f"%{q}%", *user_params, *date_params, limit),
        ).fetchall()
        posts_res = [PostResponse(**post_to_response(dict(p), user["id"])) for p in rows]

    # ── Busca de arquivos (apenas do usuário logado) ──
    if include_files:
        rows = conn.execute(
            f"""SELECT * FROM files
            WHERE user_id = ? AND original_name LIKE ?
            {date_cond}
            ORDER BY uploaded_at DESC LIMIT ?""",
            (user["id"], f"%{q}%", *date_params, limit),
        ).fetchall()
        files_res = [FileResp(**dict(r)) for r in rows]

    conn.close()
    return SearchResponse(users=users_res, posts=posts_res, files=files_res)
        
        
@router.delete("/posts/{post_id}")
def delete_post(post_id: int, user: dict = Depends(require_user)):
    """Deleta um post (apenas o próprio autor pode deletar)."""
    conn = get_connection()
    p = conn.execute(
    "SELECT id, user_id FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if not p:
        conn.close()
        raise HTTPException(404, "Post não encontrado")
    if p["user_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Não é possível excluir post de outro usuário")
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}
        
        
# ════════════════════════════════════════════════════════════════════
# LIKES - Curtir / Descurtir
# ════════════════════════════════════════════════════════════════════


@router.post("/posts/{post_id}/like")
def like_post(post_id: int, user: dict = Depends(require_user)):
    """Curtir um post (INSERT OR IGNORE: não duplica curtida)."""
    conn = get_connection()
    p = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not p:
        conn.close()
        raise HTTPException(404, "Post não encontrado")
    conn.execute(
        "INSERT OR IGNORE INTO likes (user_id, post_id) VALUES (?, ?)",
        (user["id"], post_id),
    )
    conn.commit()
    post_row = conn.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if post_row:
        _create_notification(conn, post_row["user_id"], "like", user["id"], post_id=post_id)
    conn.close()
    return {"status": "liked"}
        
        
@router.delete("/posts/{post_id}/like")
def unlike_post(post_id: int, user: dict = Depends(require_user)):
    """Remove a curtida de um post."""
    conn = get_connection()
    conn.execute(
    "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
    (user["id"], post_id),
    )
    conn.commit()
    conn.close()
    return {"status": "unliked"}
        
        
# ════════════════════════════════════════════════════════════════════
# COMMENTS - Comentários em posts
# ════════════════════════════════════════════════════════════════════


@router.post("/posts/{post_id}/comments", response_model=CommentResponse, status_code=201)
def create_comment(post_id: int, body: CreateCommentRequest, user: dict = Depends(require_user)):
    conn = get_connection()
    post = conn.execute("SELECT id, user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(404, "Post não encontrado")
    if _check_block(conn, user["id"], post["user_id"]):
        conn.close()
        raise HTTPException(403, "Você não pode comentar neste post")

    cursor = conn.execute(
        "INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)",
        (post_id, user["id"], body.content),
    )
    comment_id = cursor.lastrowid

    _create_notification(conn, post["user_id"], "comment", user["id"], post_id=post_id, comment_id=comment_id)
    _parse_mentions(conn, body.content, user["id"], post_id=post_id, comment_id=comment_id)
    conn.commit()

    c = conn.execute(
        """SELECT c.*, u.username, u.display_name
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.id = ?""",
        (comment_id,),
    ).fetchone()
    conn.close()
    return CommentResponse(
        id=c["id"],
        post_id=c["post_id"],
        user_id=c["user_id"],
        username=c["username"],
        display_name=c["display_name"] or "",
        content=c["content"],
        created_at=c["created_at"],
        edited_at=c["edited_at"],
            )
        
        
@router.get("/posts/{post_id}/comments")
def list_comments(
    post_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, le=50),
    user: dict = Depends(require_user),
):
    conn = get_connection()
    post = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(404, "Post não encontrado")
    rows = conn.execute(
        """SELECT c.*, u.username, u.display_name
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.post_id = ?
        ORDER BY c.created_at ASC
        LIMIT ? OFFSET ?""",
        (post_id, limit, offset),
    ).fetchall()
    conn.close()
    return [
        CommentResponse(
            id=r["id"],
            post_id=r["post_id"],
            user_id=r["user_id"],
            username=r["username"],
            display_name=r["display_name"] or "",
            content=r["content"],
            created_at=r["created_at"],
            edited_at=r["edited_at"],
        )
        for r in rows
    ]
        
        
@router.delete("/posts/{post_id}/comments/{comment_id}")
def delete_comment(post_id: int, comment_id: int, user: dict = Depends(require_user)):
    conn = get_connection()
    c = conn.execute(
    "SELECT id, user_id, post_id FROM comments WHERE id = ? AND post_id = ?",
    (comment_id, post_id),
    ).fetchone()
    if not c:
        conn.close()
        raise HTTPException(404, "Comentário não encontrado")
    if c["user_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Não é possível excluir comentário de outro usuário")
    conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}
        
        
# ════════════════════════════════════════════════════════════════════
# NOTIFICATIONS - Notificações do usuário
# ════════════════════════════════════════════════════════════════════


@router.get("/notifications")
def list_notifications(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, le=50),
    user: dict = Depends(require_user),
):
    conn = get_connection()
    rows = conn.execute(
    """SELECT n.*, u.username AS actor_username, u.display_name AS actor_display_name,
    p.content AS post_preview
    FROM notifications n
    JOIN users u ON n.actor_id = u.id
    LEFT JOIN posts p ON n.post_id = p.id
    WHERE n.user_id = ?
    ORDER BY n.created_at DESC
    LIMIT ? OFFSET ?""",
    (user["id"], limit, offset),
    ).fetchall()
    unread_count = conn.execute(
    "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0",
    (user["id"],),
    ).fetchone()[0]
    conn.close()
    return {
    "notifications": [
    NotificationResponse(
    id=r["id"],
    type=r["type"],
    actor_id=r["actor_id"],
    actor_username=r["actor_username"],
    actor_display_name=r["actor_display_name"] or "",
    post_id=r["post_id"],
    comment_id=r["comment_id"],
    post_preview=r["post_preview"],
    read=bool(r["read"]),
    created_at=r["created_at"],
    )
    for r in rows
        ],
        "unread_count": unread_count,
        }
        
        
@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user: dict = Depends(require_user)):
    conn = get_connection()
    n = conn.execute(
    "SELECT id FROM notifications WHERE id = ? AND user_id = ?",
    (notification_id, user["id"]),
    ).fetchone()
    if not n:
        conn.close()
        raise HTTPException(404, "Notificação não encontrada")
    conn.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))
    conn.commit()
    conn.close()
    return {"status": "read"}
        
        
@router.post("/notifications/read-all")
def mark_all_notifications_read(user: dict = Depends(require_user)):
    conn = get_connection()
    conn.execute(
    "UPDATE notifications SET read = 1 WHERE user_id = ? AND read = 0",
    (user["id"],),
    )
    conn.commit()
    conn.close()
    return {"status": "all_read"}
        
        
# ════════════════════════════════════════════════════════════════════
# CHAT - Mensagens do chat global
# ════════════════════════════════════════════════════════════════════


@router.get("/chat")
def get_chat_messages(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, le=100),
    user: dict = Depends(require_user),
):
    """Retorna mensagens do chat global (mais recentes, mas invertidas para exibição)."""
    conn = get_connection()
    rows = conn.execute(
    """SELECT cm.*, u.username, u.display_name
    FROM chat_messages cm
    JOIN users u ON cm.user_id = u.id
    ORDER BY cm.created_at DESC
    LIMIT ? OFFSET ?""",
    (limit, offset),
    ).fetchall()
    # Inverte a ordem para mostrar da mais antiga para a mais recente no frontend
    conn.close()
    return [
    ChatMessageResponse(
    id=r["id"],
    user_id=r["user_id"],
    username=r["username"] if not r["is_anonymous"] else "anonymous",
    display_name=r["display_name"] or "",
    content=r["content"],
    created_at=r["created_at"],
    is_anonymous=bool(r["is_anonymous"]),
    )
    for r in reversed(rows)
        ]
        
        
@router.post("/chat", response_model=ChatMessageResponse)
def send_chat(body: SendChatRequest, user: dict = Depends(require_user)):
    """Envia uma mensagem no chat global. is_anonymous oculta o remetente."""
    conn = get_connection()
    cursor = conn.execute(
    "INSERT INTO chat_messages (user_id, content, is_anonymous) VALUES (?, ?, ?)",
    (user["id"], body.content, 1 if body.is_anonymous else 0),
    )
    conn.commit()
    msg = conn.execute(
    """SELECT cm.*, u.username, u.display_name
    FROM chat_messages cm
    JOIN users u ON cm.user_id = u.id
    WHERE cm.id = ?""",
    (cursor.lastrowid,),
    ).fetchone()
    conn.close()
    return ChatMessageResponse(
    id=msg["id"],
    user_id=msg["user_id"],
    username=msg["username"] if not msg["is_anonymous"] else "anonymous",
    display_name=msg["display_name"] or "",
    content=msg["content"],
    created_at=msg["created_at"],
    is_anonymous=bool(msg["is_anonymous"]),
    )
        
        
# ════════════════════════════════════════════════════════════════════
# DMs - Conversas e mensagens diretas
# ════════════════════════════════════════════════════════════════════


def _get_or_create_conversation(conn, user1_id: int, user2_id: int) -> int:
    """Retorna o ID de uma conversa existente entre dois usuários, ou cria uma nova."""
    # Garante ordenação consistente para a UNIQUE constraint
    user1_id, user2_id = sorted([user1_id, user2_id])
    row = conn.execute(
        "SELECT id FROM conversations WHERE user1_id = ? AND user2_id = ?",
        (user1_id, user2_id),
    ).fetchone()
    if row:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO conversations (user1_id, user2_id) VALUES (?, ?)",
        (user1_id, user2_id),
    )
    conn.commit()
    return cursor.lastrowid


@router.post("/conversations")
def start_conversation(
    username: str = Query(..., description="Username do outro usuário"),
    user: dict = Depends(require_user),
):
    """Inicia (ou retorna) uma conversa com outro usuário."""
    conn = get_connection()
    other = conn.execute(
    "SELECT id, username, display_name FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not other:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    if other["id"] == user["id"]:
        conn.close()
        raise HTTPException(400, "Não é possível iniciar conversa com você mesmo")
    if _check_block(conn, user["id"], other["id"]):
        conn.close()
        raise HTTPException(403, "Você não pode iniciar conversa com este usuário")

    conv_id = _get_or_create_conversation(conn, user["id"], other["id"])
    conn.close()
    return {"id": conv_id, "username": other["username"], "display_name": other["display_name"] or ""}
        
        
@router.get("/conversations")
def list_conversations(user: dict = Depends(require_user)):
    """
    Lista as conversas do usuário logado.
    Otimizado: busca dados do outro usuário e última mensagem em uma única query,
    eliminando N+1 consultas.
    """
    conn = get_connection()
    rows = conn.execute(
    """SELECT c.id,
    CASE WHEN c.user1_id = ? THEN c.user2_id ELSE c.user1_id END AS other_id,
    u.username,
    u.display_name,
    last_msg.content AS last_message,
    last_msg.created_at AS last_message_at
    FROM conversations c
    JOIN users u ON u.id = CASE WHEN c.user1_id = ? THEN c.user2_id ELSE c.user1_id END
    LEFT JOIN direct_messages last_msg ON last_msg.id = (
    SELECT id FROM direct_messages
    WHERE conversation_id = c.id
    ORDER BY created_at DESC LIMIT 1
    )
    WHERE (c.user1_id = ? OR c.user2_id = ?)
    AND NOT EXISTS (
    SELECT 1 FROM blocks
    WHERE (blocker_id = ? AND blocked_id = u.id)
    OR (blocker_id = u.id AND blocked_id = ?)
    )
    ORDER BY c.updated_at DESC""",
    (user["id"], user["id"], user["id"], user["id"], user["id"], user["id"]),
    ).fetchall()
    result = [
    {
    "id": r["id"],
    "other_username": r["username"],
    "other_display_name": r["display_name"] or "",
    "last_message": r["last_message"],
    "last_message_at": r["last_message_at"],
    }
    for r in rows
    ]
    conn.close()
    return result
        
        
@router.get("/conversations/{conv_id}/messages")
def get_dm_messages(
    conv_id: int,
    user: dict = Depends(require_user),
):
    """Retorna as mensagens de uma conversa (com metadados dos arquivos anexados)."""
    conn = get_connection()
    conv = conn.execute(
    "SELECT * FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if not conv or (conv["user1_id"] != user["id"] and conv["user2_id"] != user["id"]):
        conn.close()
        raise HTTPException(404, "Conversa não encontrada")

    rows = conn.execute(
        """SELECT dm.*, u.username AS sender_username, u.display_name AS sender_display_name
        FROM direct_messages dm
        JOIN users u ON dm.sender_id = u.id
        WHERE dm.conversation_id = ?
        ORDER BY dm.created_at ASC""",
        (conv_id,),
    ).fetchall()

    files_map, dm_files_map = _batch_fetch_file_meta(conn, rows)

    result = []
    for r in rows:
        fi = files_map.get(r["file_id"])
        dfi = dm_files_map.get(r["dm_file_id"])
        result.append({
            "id": r["id"],
            "conversation_id": conv_id,
            "sender_id": r["sender_id"],
            "sender_username": r["sender_username"],
            "sender_display_name": r["sender_display_name"] or "",
            "content": r["content"],
            "file_id": r["file_id"],
            "file_name": fi["original_name"] if fi else None,
            "file_type": fi["content_type"] if fi else None,
            "file_size": fi["size"] if fi else None,
            "dm_file_id": r["dm_file_id"],
            "dm_file_name": dfi["original_name"] if dfi else None,
            "created_at": r["created_at"],
        })
    conn.close()
    return result
        
        
@router.post("/conversations/{conv_id}/messages", response_model=DMResponse)
def send_dm(
    conv_id: int,
    body: SendDMRequest,
    user: dict = Depends(require_user),
):
    """Envia uma mensagem direta em uma conversa (com ou sem arquivo anexado)."""
    conn = get_connection()
    conv = conn.execute(
    "SELECT * FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if not conv or (conv["user1_id"] != user["id"] and conv["user2_id"] != user["id"]):
        conn.close()
        raise HTTPException(404, "Conversa não encontrada")

    # Encontra o outro participante e checa block
    other_id = conv["user2_id"] if conv["user1_id"] == user["id"] else conv["user1_id"]
    if _check_block(conn, user["id"], other_id):
        conn.close()
        raise HTTPException(403, "Você não pode enviar mensagens nesta conversa")

    cursor = conn.execute(
        "INSERT INTO direct_messages (conversation_id, sender_id, content, file_id, dm_file_id) VALUES (?, ?, ?, ?, ?)",
        (conv_id, user["id"], body.content, body.file_id, body.dm_file_id),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (conv_id,)
    )
    conn.commit()
    msg = conn.execute(
        """SELECT dm.*, u.username AS sender_username, u.display_name AS sender_display_name
        FROM direct_messages dm
        JOIN users u ON dm.sender_id = u.id
        WHERE dm.id = ?""",
        (cursor.lastrowid,),
    ).fetchone()

    # Busca metadados dos arquivos ANTES de fechar a conexão
    file_name, file_type, file_size = _fetch_file_meta(conn, msg["file_id"])
    dm_file_name = _fetch_dm_file_name(conn, msg["dm_file_id"])

    conn.close()
    return DMResponse(
        id=msg["id"],
        conversation_id=conv_id,
        sender_id=msg["sender_id"],
        sender_username=msg["sender_username"],
        sender_display_name=msg["sender_display_name"] or "",
        content=msg["content"],
        file_id=msg["file_id"],
        file_name=file_name,
        file_type=file_type,
        file_size=file_size,
        dm_file_id=msg["dm_file_id"],
        dm_file_name=dm_file_name,
        created_at=msg["created_at"],
    )
        
        
@router.delete("/conversations/{conv_id}/messages")
def clear_conversation(conv_id: int, user: dict = Depends(require_user)):
    """Apaga TODAS as mensagens de uma conversa (limpa pra ambos os lados)."""
    conn = get_connection()
    conv = conn.execute(
    "SELECT * FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if not conv or (conv["user1_id"] != user["id"] and conv["user2_id"] != user["id"]):
        conn.close()
        raise HTTPException(404, "Conversa não encontrada")

    conn.execute("DELETE FROM direct_messages WHERE conversation_id = ?", (conv_id,))
    conn.execute(
        "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (conv_id,)
    )
    conn.commit()
    conn.close()
    return {"status": "cleared"}
        
        
# ════════════════════════════════════════════════════════════════════
# GROUPS - Conversas em grupo (DM grupal)
# ════════════════════════════════════════════════════════════════════


def _get_group_member_count(conn, group_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM group_members WHERE group_id = ?", (group_id,)
    ).fetchone()
    return row[0] if row else 0


def _is_group_member(conn, group_id: int, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
        (group_id, user_id),
    ).fetchone()
    return row is not None


def _check_group_block_exclusion(conn, new_user_id: int, group_id: int) -> bool:
    """Checks if new_user_id would be in same group as any excluded pair or blocked user."""
    existing = conn.execute(
        "SELECT user_id FROM group_members WHERE group_id = ? AND user_id != ?",
        (group_id, new_user_id),
    ).fetchall()
    for ex in existing:
        uid = ex["user_id"]
        u1, u2 = sorted([new_user_id, uid])
        row = conn.execute(
            "SELECT 1 FROM group_exclusion_pairs WHERE user1_id = ? AND user2_id = ?",
            (u1, u2),
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            "SELECT 1 FROM blocks WHERE (blocker_id = ? AND blocked_id = ?) OR (blocker_id = ? AND blocked_id = ?)",
            (new_user_id, uid, uid, new_user_id),
        ).fetchone()
        if row:
            return True
    return False


def _group_to_response(conn, g: dict, current_user_id: int) -> dict:
    member_count = _get_group_member_count(conn, g["id"])
    last = conn.execute(
        """SELECT gm.content, gm.created_at, u.username
           FROM group_messages gm
           JOIN users u ON gm.sender_id = u.id
           WHERE gm.group_id = ?
           ORDER BY gm.created_at DESC LIMIT 1""",
        (g["id"],),
    ).fetchone()
    return {
        "id": g["id"],
        "name": g["name"],
        "created_by": g["created_by"],
        "member_count": member_count,
        "last_message": last["content"] if last else None,
        "last_message_at": last["created_at"] if last else None,
        "last_sender_username": last["username"] if last else None,
        "created_at": g["created_at"],
    }


@router.post("/groups", response_model=GroupResponse)
def create_group(body: CreateGroupRequest, user: dict = Depends(require_user)):
    """Cria um grupo com até 7 membros. O criador é admin."""
    conn = get_connection()
    members = [user["username"]] + body.members
    members = list(dict.fromkeys(members))
    if len(members) > 7:
        conn.close()
        raise HTTPException(400, "Máximo de 7 membros por grupo")
    if len(members) < 2:
        conn.close()
        raise HTTPException(400, "Grupo precisa de pelo menos 2 membros")

    # Resolve usernames para IDs
    member_ids = []
    for username in members:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, f"Usuário não encontrado: {username}")
        member_ids.append(row["id"])

    # Verifica exclusões de bloco entre todos os membros
    for i in range(len(member_ids)):
        for j in range(i + 1, len(member_ids)):
            u1, u2 = sorted([member_ids[i], member_ids[j]])
            row = conn.execute(
                "SELECT 1 FROM group_exclusion_pairs WHERE user1_id = ? AND user2_id = ?",
                (u1, u2),
            ).fetchone()
            if row:
                conn.close()
                raise HTTPException(400, "Não é possível criar grupo com estes membros")
            row = conn.execute(
                "SELECT 1 FROM blocks WHERE (blocker_id = ? AND blocked_id = ?) OR (blocker_id = ? AND blocked_id = ?)",
                (member_ids[i], member_ids[j], member_ids[j], member_ids[i]),
            ).fetchone()
            if row:
                conn.close()
                raise HTTPException(400, "Não é possível criar grupo com estes membros")

    cursor = conn.execute(
        "INSERT INTO user_groups (name, created_by) VALUES (?, ?)",
        (body.name, user["id"]),
    )
    group_id = cursor.lastrowid

    for uid in member_ids:
        role = "admin" if uid == user["id"] else "member"
        conn.execute(
            "INSERT INTO group_members (group_id, user_id, role) VALUES (?, ?, ?)",
            (group_id, uid, role),
        )
    conn.commit()
    g = conn.execute("SELECT * FROM user_groups WHERE id = ?", (group_id,)).fetchone()
    result = _group_to_response(conn, dict(g), user["id"])
    conn.close()
    return GroupResponse(**result)
        
        
@router.get("/groups")
def list_groups(user: dict = Depends(require_user)):
    """Lista grupos do usuário logado."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT g.* FROM user_groups g
        JOIN group_members gm ON g.id = gm.group_id
        WHERE gm.user_id = ?
        ORDER BY g.updated_at DESC""",
        (user["id"],),
    ).fetchall()
    result = [_group_to_response(conn, dict(g), user["id"]) for g in rows]
    conn.close()
    return [GroupResponse(**r) for r in result]
        
        
@router.get("/groups/{group_id}/messages")
def get_group_messages(group_id: int, user: dict = Depends(require_user)):
    """Retorna mensagens de um grupo."""
    conn = get_connection()
    if not _is_group_member(conn, group_id, user["id"]):
        conn.close()
        raise HTTPException(403, "Você não é membro deste grupo")
    rows = conn.execute(
        """SELECT gm.*, u.username AS sender_username, u.display_name AS sender_display_name
        FROM group_messages gm
        JOIN users u ON gm.sender_id = u.id
        WHERE gm.group_id = ?
        ORDER BY gm.created_at ASC""",
        (group_id,),
    ).fetchall()
    files_map, dm_files_map = _batch_fetch_file_meta(conn, rows)
    result = []
    for r in rows:
        fi = files_map.get(r["file_id"])
        dfi = dm_files_map.get(r["dm_file_id"])
        result.append({
            "id": r["id"],
            "group_id": group_id,
            "sender_id": r["sender_id"],
            "sender_username": r["sender_username"],
            "sender_display_name": r["sender_display_name"] or "",
            "content": r["content"],
            "file_id": r["file_id"],
            "file_name": fi["original_name"] if fi else None,
            "file_type": fi["content_type"] if fi else None,
            "file_size": fi["size"] if fi else None,
            "dm_file_id": r["dm_file_id"],
            "dm_file_name": dfi["original_name"] if dfi else None,
            "created_at": r["created_at"],
        })
    conn.close()
    return [GroupMessageResponse(**r) for r in result]
        
        
@router.post("/groups/{group_id}/messages", response_model=GroupMessageResponse)
def send_group_message(group_id: int, body: SendGroupMessageRequest, user: dict = Depends(require_user)):
    """Envia mensagem em grupo."""
    conn = get_connection()
    if not _is_group_member(conn, group_id, user["id"]):
        conn.close()
        raise HTTPException(403, "Você não é membro deste grupo")
    cursor = conn.execute(
        "INSERT INTO group_messages (group_id, sender_id, content, file_id, dm_file_id) VALUES (?, ?, ?, ?, ?)",
        (group_id, user["id"], body.content, body.file_id, body.dm_file_id),
    )
    conn.execute(
        "UPDATE user_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (group_id,)
    )
    conn.commit()
    msg = conn.execute(
        """SELECT gm.*, u.username AS sender_username, u.display_name AS sender_display_name
        FROM group_messages gm JOIN users u ON gm.sender_id = u.id
        WHERE gm.id = ?""",
        (cursor.lastrowid,),
    ).fetchone()
    file_name, file_type, file_size = _fetch_file_meta(conn, msg["file_id"])
    dm_file_name = _fetch_dm_file_name(conn, msg["dm_file_id"])
    conn.close()
    return GroupMessageResponse(
        id=msg["id"], group_id=group_id,
        sender_id=msg["sender_id"], sender_username=msg["sender_username"],
        sender_display_name=msg["sender_display_name"] or "",
        content=msg["content"],
        file_id=msg["file_id"], file_name=file_name, file_type=file_type, file_size=file_size,
        dm_file_id=msg["dm_file_id"], dm_file_name=dm_file_name,
        created_at=msg["created_at"],
    )
        
        
@router.get("/groups/{group_id}/members")
def list_group_members(group_id: int, user: dict = Depends(require_user)):
    """Lista membros de um grupo."""
    conn = get_connection()
    if not _is_group_member(conn, group_id, user["id"]):
        conn.close()
        raise HTTPException(403, "Você não é membro deste grupo")
    rows = conn.execute(
        """SELECT gm.*, u.username, u.display_name
        FROM group_members gm JOIN users u ON gm.user_id = u.id
        WHERE gm.group_id = ?
        ORDER BY gm.joined_at ASC""",
        (group_id,),
    ).fetchall()
    conn.close()
    return [
        GroupMemberResponse(
            user_id=r["user_id"], username=r["username"],
            display_name=r["display_name"] or "", role=r["role"],
            joined_at=r["joined_at"],
        )
        for r in rows
    ]
        
        
@router.post("/groups/{group_id}/members")
def add_group_member(group_id: int, body: AddGroupMemberRequest, user: dict = Depends(require_user)):
    """Adiciona membro a um grupo."""
    conn = get_connection()
    g = conn.execute("SELECT * FROM user_groups WHERE id = ?", (group_id,)).fetchone()
    if not g:
        conn.close()
        raise HTTPException(404, "Grupo não encontrado")
    if not _is_group_member(conn, group_id, user["id"]):
        conn.close()
        raise HTTPException(403, "Você não é membro deste grupo")

    target = conn.execute("SELECT id FROM users WHERE username = ?", (body.username,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Usuário não encontrado")
    if _is_group_member(conn, group_id, target["id"]):
        conn.close()
        raise HTTPException(400, "Usuário já é membro do grupo")

    member_count = _get_group_member_count(conn, group_id)
    if member_count >= 7:
        conn.close()
        raise HTTPException(400, "Grupo já tem o máximo de 7 membros")

    if _check_group_block_exclusion(conn, target["id"], group_id):
        conn.close()
        raise HTTPException(400, "Não é possível adicionar este usuário ao grupo")

    conn.execute(
        "INSERT INTO group_members (group_id, user_id, role) VALUES (?, ?, 'member')",
        (group_id, target["id"]),
    )
    conn.execute("UPDATE user_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()
    return {"status": "added", "user_id": target["id"]}
        
        
@router.delete("/groups/{group_id}/members/{member_id}")
def remove_group_member(group_id: int, member_id: int, user: dict = Depends(require_user)):
    """Remove membro de um grupo. Só admin ou o próprio membro pode remover."""
    conn = get_connection()
    g = conn.execute("SELECT * FROM user_groups WHERE id = ?", (group_id,)).fetchone()
    if not g:
        conn.close()
        raise HTTPException(404, "Grupo não encontrado")
    if not _is_group_member(conn, group_id, user["id"]):
        conn.close()
        raise HTTPException(403, "Você não é membro deste grupo")
    if user["id"] != member_id:
        member_row = conn.execute(
            "SELECT role FROM group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user["id"]),
        ).fetchone()
        if not member_row or member_row["role"] != "admin":
            conn.close()
            raise HTTPException(403, "Apenas administradores podem remover outros membros")

    conn.execute(
        "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
        (group_id, member_id),
    )
    remaining = _get_group_member_count(conn, group_id)
    if remaining == 0:
        conn.execute("DELETE FROM group_messages WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM user_groups WHERE id = ?", (group_id,))
    else:
        conn.execute("UPDATE user_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()
    return {"status": "removed"}
        
        
@router.post("/groups/{group_id}/leave")
def leave_group(group_id: int, user: dict = Depends(require_user)):
    """Sai de um grupo."""
    conn = get_connection()
    if not _is_group_member(conn, group_id, user["id"]):
        conn.close()
        raise HTTPException(404, "Grupo não encontrado")
    conn.execute(
        "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
        (group_id, user["id"]),
    )
    remaining = _get_group_member_count(conn, group_id)
    if remaining == 0:
        conn.execute("DELETE FROM group_messages WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM user_groups WHERE id = ?", (group_id,))
    else:
        conn.execute("UPDATE user_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()
    return {"status": "left"}
        
        
# ════════════════════════════════════════════════════════════════════
# FILES - Upload, download, listar, deletar
# ════════════════════════════════════════════════════════════════════


@router.post("/files/upload")
async def upload_file(
    file: UploadFile = File(..., description="Arquivo para upload"),
    user: dict = Depends(require_user),
    request: Request = None,
):
    """Faz upload de um arquivo. Salva em uploads/ com nome único (UUID)."""
    ext = Path(file.filename).suffix if file.filename else ""
    storage_name = f"{uuid.uuid4().hex}{ext}"
    file_path = UPLOAD_DIR / storage_name

    total = await _read_upload_stream(file, file_path, MAX_UPLOAD_SIZE, request)

    conn = get_connection()
    cursor = conn.execute(
    """INSERT INTO files (user_id, storage_name, original_name, size, content_type)
    VALUES (?, ?, ?, ?, ?)""",
    (user["id"], storage_name, file.filename or "unnamed", total,
    file.content_type or "application/octet-stream"),
    )
    conn.commit()
    return {
    "id": cursor.lastrowid,
    "original_name": file.filename,
    "size": total,
    "content_type": file.content_type,
    "storage_name": storage_name,
    }
        
        
@router.get("/files")
def list_files(user: dict = Depends(require_user)):
    """Lista todos os arquivos enviados pelo usuário logado."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM files WHERE user_id = ? ORDER BY uploaded_at DESC",
        (user["id"],),
    ).fetchall()
    result = [FileResp(**dict(r)) for r in rows]
    conn.close()
    return result
        
        
@router.get("/files/{file_id}/download")
def download_file(file_id: int, user: dict = Depends(require_user)):
    """Baixa um arquivo pelo ID (qualquer usuário autenticado pode baixar qualquer arquivo)."""
    conn = get_connection()
    f = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if not f:
        conn.close()
        raise HTTPException(404, "Arquivo não encontrado")
    file_path = UPLOAD_DIR / f["storage_name"]
    if not file_path.exists():
        conn.close()
        raise HTTPException(404, "Arquivo não encontrado no disco")
    conn.close()
    return FileResponse(
        path=str(file_path),
        filename=f["original_name"],
        media_type=f["content_type"] or "application/octet-stream",
    )
        
        
@router.delete("/files/{file_id}")
def delete_file(file_id: int, user: dict = Depends(require_user)):
    """Deleta um arquivo (apenas o dono pode deletar). Remove do disco e do banco."""
    conn = get_connection()
    f = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if not f:
        conn.close()
        raise HTTPException(404, "Arquivo não encontrado")
    if f["user_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Não é possível excluir arquivo de outro usuário")
    file_path = UPLOAD_DIR / f["storage_name"]
    if file_path.exists():
        file_path.unlink()
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}
        
        
# ════════════════════════════════════════════════════════════════════
# AVATAR - Upload e exibição de foto de perfil
# ════════════════════════════════════════════════════════════════════

AVATAR_DIR = UPLOAD_DIR / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/users/avatar")
async def upload_avatar(
    file: UploadFile = File(..., description="Imagem de perfil"),
    user: dict = Depends(require_user),
    request: Request = None,
):
    """Envia/atualiza a foto de perfil do usuário logado (max 2MB, PNG/JPEG/GIF/WebP)."""
    ext = Path(file.filename).suffix if file.filename else ".png"
    if ext.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        raise HTTPException(400, "Formato não suportado (use PNG, JPEG, GIF ou WebP)")

    raw = await file.read(32)
    await file.seek(0)
    if ext.lower() == ".png" and not raw.startswith(b"\x89PNG"):
        raise HTTPException(400, "Invalid PNG file")
    if ext.lower() in (".jpg", ".jpeg") and not raw.startswith(b"\xff\xd8"):
        raise HTTPException(400, "Invalid JPEG file")
    if ext.lower() == ".gif" and not raw.startswith(b"GIF8"):
        raise HTTPException(400, "Invalid GIF file")
    if ext.lower() == ".webp" and not raw.startswith(b"RIFF"):
        raise HTTPException(400, "Invalid WebP file")

    avatar_name = f"user_{user['id']}{ext}"
    avatar_path = AVATAR_DIR / avatar_name

    await _read_upload_stream(file, avatar_path, 2 * 1024 * 1024, request)

    conn = get_connection()
    conn.execute("UPDATE users SET avatar_path = ? WHERE id = ?",
    (str(avatar_path), user["id"]))
    conn.commit()
    conn.close()
    return {"status": "ok", "avatar_path": f"/uploads/avatars/{avatar_name}"}
        
        
@router.get("/users/{username}/avatar")
def get_avatar(username: str):
    """Retorna a imagem de avatar de um usuário."""
    conn = get_connection()
    u = conn.execute("SELECT avatar_path FROM users WHERE username = ?", (username,)).fetchone()
    if not u or not u["avatar_path"]:
        conn.close()
        raise HTTPException(404, "Avatar não encontrado")
    p = Path(u["avatar_path"])
    if not p.exists():
        conn.close()
        raise HTTPException(404, "Arquivo de avatar não encontrado")
    ext = p.suffix.lower()
    media_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                  "gif": "image/gif", "webp": "image/webp"}.get(ext.lstrip("."), "application/octet-stream")
    conn.close()
    return Response(content=p.read_bytes(), media_type=media_type, headers={"X-Content-Type-Options": "nosniff"})
        
        
# ════════════════════════════════════════════════════════════════════
# DM FILES - Upload e download criptografados (arquivos de DM)
# ════════════════════════════════════════════════════════════════════


@router.post("/dm-files/upload")
async def upload_dm_file(
    file: UploadFile = File(..., description="Arquivo para DM"),
    user: dict = Depends(require_user),
    request: Request = None,
):
    """Faz upload de um arquivo para DM. Salva criptografado em dm_uploads/."""
    ext = Path(file.filename).suffix if file.filename else ""
    storage_name = f"{uuid.uuid4().hex}{ext}"
    file_path = DM_UPLOAD_DIR / storage_name

    # Stream para arquivo temporário (aborta se > 10MB, sem estourar RAM)
    temp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    await _read_upload_stream(file, temp_path, MAX_UPLOAD_SIZE, request)

    # Lê o temporário (máx 10MB garantido), criptografa, salva no destino
    raw = temp_path.read_bytes()
    encrypted = encrypt_file_bytes(raw)
    temp_path.unlink()
    file_path.write_bytes(encrypted)

    conn = get_connection()
    cursor = conn.execute(
    """INSERT INTO dm_files (user_id, storage_name, original_name, size, content_type)
    VALUES (?, ?, ?, ?, ?)""",
    (user["id"], storage_name, file.filename or "unnamed", len(raw),
    file.content_type or "application/octet-stream"),
    )
    conn.commit()
    return {
    "id": cursor.lastrowid,
    "original_name": file.filename,
    "size": len(raw),
    "content_type": file.content_type,
    }
        
        
@router.get("/dm-files/{file_id}/download")
def download_dm_file(file_id: int, user: dict = Depends(require_user)):
    """Baixa um arquivo de DM descriptografado."""
    conn = get_connection()
    f = conn.execute("SELECT * FROM dm_files WHERE id = ?", (file_id,)).fetchone()
    if not f:
        conn.close()
        raise HTTPException(404, "Arquivo não encontrado")
    file_path = DM_UPLOAD_DIR / f["storage_name"]
    if not file_path.exists():
        conn.close()
        raise HTTPException(404, "Arquivo não encontrado no disco")
    encrypted = file_path.read_bytes()
    try:
        decrypted = decrypt_file_bytes(encrypted)
    except Exception:
        conn.close()
        raise HTTPException(500, "Falha ao descriptografar arquivo")
    conn.close()
    return Response(
        content=decrypted,
        media_type=f["content_type"] or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{f["original_name"]}"'},
    )
        
        
# ════════════════════════════════════════════════════════════════════
# CHAT CLEANUP - Deleta mensagens antigas a cada 30 min
# ════════════════════════════════════════════════════════════════════


@router.get("/version")
def get_version():
    return {"client_min_version": "0.1.0", "server_version": "0.1.0"}


def _cleanup_chat_loop():
    """
    Loop em background que deleta mensagens do chat com mais de 30 minutos.
    Executa a cada 1800 segundos (30 min) em uma thread separada.
    """
    while True:
        time.sleep(1800)
        conn = get_connection()
        try:
            conn.execute(
                "DELETE FROM chat_messages WHERE created_at < datetime('now', '-30 minutes')"
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()


# Inicia o cleanup em uma thread separada (daemon, não bloqueia o servidor)
_thread = threading.Thread(target=_cleanup_chat_loop, daemon=True)
_thread.start()