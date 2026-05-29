"""
hexfeed - módulo de banco de dados
Gerencia a conexão SQLite e cria as tabelas do sistema.
Cada chamada de get_connection() abre uma nova conexão.
O banco fica em data/hexfeed.db e usa WAL mode para performance.
"""

import atexit
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Define onde o arquivo do banco e diretórios de upload vão ficar
DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "hexfeed.db"
DM_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "dm_uploads"

_connections: list[sqlite3.Connection] = []


def _close_all():
    for c in _connections:
        try:
            c.close()
        except Exception:
            pass
    _connections.clear()


atexit.register(_close_all)


def get_connection() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    _connections.append(conn)
    return conn


def close_connection(conn: sqlite3.Connection | None):
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass
    finally:
        try:
            _connections.remove(conn)
        except ValueError:
            pass


@contextmanager
def conn_ctx():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        close_connection(conn)


def init_db():
    """
    Cria todas as tabelas se elas não existirem ainda.
    Também aplica migrações incrementais (ALTER TABLE) para bancos existentes.

    Tabelas:
      - users:        dados dos usuários (username, hash, pgp, perfil)
      - tokens:       sessões de login (token -> user_id)
      - posts:        publicações dos usuários
      - likes:        curtidas (user_id + post_id)
      - follows:      seguidores (follower_id -> following_id)
      - chat_messages: mensagens do chat global
      - files:        arquivos enviados em posts
      - conversations: pares de DM entre dois usuários
      - direct_messages: mensagens de DM
      - dm_files:     arquivos criptografados de DM
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            pgp_public_key  TEXT DEFAULT '',
            pgp_fingerprint TEXT DEFAULT '',
            pgp_private_key_hash TEXT DEFAULT '',
            email           TEXT DEFAULT '',
            display_name    TEXT DEFAULT '',
            bio             TEXT DEFAULT '',
            avatar_path     TEXT DEFAULT '',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tokens (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            token      TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS posts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            content    TEXT NOT NULL,
            reply_to   INTEGER DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            edited_at  TIMESTAMP DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (reply_to) REFERENCES posts(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS likes (
            user_id    INTEGER NOT NULL,
            post_id    INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, post_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS follows (
            follower_id  INTEGER NOT NULL,
            following_id INTEGER NOT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (follower_id, following_id),
            FOREIGN KEY (follower_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (following_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            content       TEXT NOT NULL,
            is_anonymous  INTEGER DEFAULT 0,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS files (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            storage_name  TEXT NOT NULL,
            original_name TEXT NOT NULL,
            size          INTEGER NOT NULL,
            content_type  TEXT DEFAULT 'application/octet-stream',
            uploaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id   INTEGER NOT NULL,
            user2_id   INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user1_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (user2_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user1_id, user2_id)
        );

        CREATE TABLE IF NOT EXISTS direct_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender_id       INTEGER NOT NULL,
            content         TEXT NOT NULL,
            file_id         INTEGER DEFAULT NULL,
            dm_file_id      INTEGER DEFAULT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE SET NULL
        );

        -- Índices para buscas rápidas (cobertura de consultas comuns)
        CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_messages(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id);
        CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
    """)

    # Migrações incrementais para bancos existentes (colunas adicionadas em versões posteriores)
    # Cada ALTER TABLE ignora erro se a coluna já existir

    # Coluna is_anonymous no chat global
    try:
        cursor.execute("ALTER TABLE chat_messages ADD COLUMN is_anonymous INTEGER DEFAULT 0")
    except Exception:
        pass

    # Coluna file_id nos posts (arquivo anexado à publicação)
    try:
        cursor.execute("ALTER TABLE posts ADD COLUMN file_id INTEGER DEFAULT NULL REFERENCES files(id) ON DELETE SET NULL")
    except Exception:
        pass

    # Coluna dm_file_id em direct_messages (arquivo criptografado de DM)
    try:
        cursor.execute("ALTER TABLE direct_messages ADD COLUMN dm_file_id INTEGER DEFAULT NULL")
    except Exception:
        pass

    # Tabela de arquivos criptografados de DM
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dm_files (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            storage_name    TEXT NOT NULL,
            original_name   TEXT NOT NULL,
            size            INTEGER NOT NULL,
            content_type    TEXT DEFAULT 'application/octet-stream',
            uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # ── Migrações admin: banned, settings, announcements, security ──
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN banned_at TIMESTAMP DEFAULT NULL")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN banned_by TEXT DEFAULT ''")
    except Exception:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            active INTEGER DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            success INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            username TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Tabelas: comentários, blocks, notificações ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            content    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            edited_at  TIMESTAMP DEFAULT NULL,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blocks (
            blocker_id  INTEGER NOT NULL,
            blocked_id  INTEGER NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (blocker_id, blocked_id),
            FOREIGN KEY (blocker_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (blocked_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            type       TEXT NOT NULL,
            actor_id   INTEGER NOT NULL,
            post_id    INTEGER DEFAULT NULL,
            comment_id INTEGER DEFAULT NULL,
            read       INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_notifications_user
        ON notifications(user_id, read, created_at DESC)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_comments_post
        ON comments(post_id, created_at)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_exclusion_pairs (
            user1_id  INTEGER NOT NULL,
            user2_id  INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user1_id, user2_id),
            FOREIGN KEY (user1_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (user2_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_groups (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_id  INTEGER NOT NULL,
            user_id   INTEGER NOT NULL,
            role      TEXT DEFAULT 'member',
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (group_id, user_id),
            FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id        INTEGER NOT NULL,
            sender_id       INTEGER NOT NULL,
            content         TEXT NOT NULL,
            file_id         INTEGER DEFAULT NULL,
            dm_file_id      INTEGER DEFAULT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
            FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE SET NULL
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_group_messages_group ON group_messages(group_id, created_at)
    """)

    # Valores padrão de configuração, se não existirem
    defaults = [
        ("pow_difficulty", "20"),
        ("max_register_attempts", "15"),
        ("rate_window_hours", "1"),
        ("chat_cleanup_mins", "30"),
        ("max_post_length", "500"),
        ("max_avatar_size_mb", "2"),
    ]
    for k, v in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    close_connection(conn)

    DM_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
