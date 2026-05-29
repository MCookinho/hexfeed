"""
hexfeed - módulo de autenticação
Funções para hash de senha, gerenciamento de tokens e validação de chaves PGP.
"""

import secrets
import time
import threading
import bcrypt
import pgpy
from server.database import get_connection, close_connection

TOKEN_TTL_DAYS = 30


def cleanup_expired_tokens():
    """Remove tokens expirados do banco de dados."""
    try:
        conn = get_connection()
        cursor = conn.execute(
            "DELETE FROM tokens WHERE created_at < datetime('now', ?)",
            (f'-{TOKEN_TTL_DAYS} days',),
        )
        deleted = cursor.rowcount
        conn.commit()
        close_connection(conn)
        if deleted:
            print(f"🧹 Limpeza: {deleted} token(s) expirado(s) removido(s)")
    except Exception:
        pass


# Agenda limpeza a cada 6 horas
def _start_token_cleanup():
    cleanup_expired_tokens()
    threading.Timer(21600, _start_token_cleanup).start()


_start_token_cleanup()


def hash_password(password: str) -> str:
    """
    Gera um hash bcrypt da senha.
    bcrypt já inclui um salt aleatório internamente.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verifica se a senha corresponde ao hash armazenado.
    """
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _execute_with_conn(conn, query: str, params: tuple, commit: bool = False):
    """
    Executa uma query usando a conexão fornecida ou criando uma nova.
    Retorna (cursor, deve_fechar_conexao).
    Interno: reduz duplicação entre create_token e get_user_from_token.
    """
    if conn:
        cursor = conn.execute(query, params)
        if commit:
            conn.commit()
        return cursor, False
    c = get_connection()
    cursor = c.execute(query, params)
    if commit:
        c.commit()
    return cursor, True


def create_token(user_id: int, conn=None) -> str:
    """
    Gera um token aleatório de 64 caracteres hexadecimais
    e salva no banco associado ao user_id.
    Se conn for passado, reusa a conexão (evita lock no SQLite).
    """
    token = secrets.token_hex(32)
    cursor, close_conn = _execute_with_conn(
        conn,
        "INSERT INTO tokens (user_id, token) VALUES (?, ?)",
        (user_id, token),
        commit=True,
    )
    if close_conn:
        close_connection(cursor.connection)
    return token


def get_user_from_token(token: str, conn=None) -> dict | None:
    """
    Busca um usuário pelo token de autenticação.
    Retorna o dicionário com dados do usuário ou None se inválido.
    """
    cursor, close_conn = _execute_with_conn(
        conn,
        """SELECT u.* FROM users u
           JOIN tokens t ON u.id = t.user_id
           WHERE t.token = ? AND t.created_at >= datetime('now', '-30 days')""",
        (token,),
    )
    row = cursor.fetchone()
    if close_conn:
        close_connection(cursor.connection)
    return dict(row) if row else None


def delete_token(token: str):
    """
    Remove um token do banco (logout).
    """
    conn = get_connection()
    conn.execute("DELETE FROM tokens WHERE token = ?", (token,))
    conn.commit()
    close_connection(conn)


def validate_pgp_key(key_text: str) -> tuple[bool, str]:
    """
    Valida uma chave PGP pública.
    Retorna (True, fingerprint) se válida,
    ou (False, mensagem_de_erro) se inválida.
    """
    try:
        key, _ = pgpy.PGPKey.from_blob(key_text)
        if key.is_public:
            fp = str(key.fingerprint)
            return True, fp
        return False, "Key is not a public key (não é uma chave pública)"
    except Exception as e:
        return False, f"Invalid PGP key: {str(e)}"


def verify_pgp_private_key(priv_key_text: str, expected_fingerprint: str) -> bool:
    """
    Verifica se a chave privada PGP corresponde ao fingerprint esperado.
    Parseia a privada, extrai o fingerprint e compara.
    A chave privada NÃO é armazenada — apenas verificada em memória.
    """
    try:
        key, _ = pgpy.PGPKey.from_blob(priv_key_text)
        if key.is_public:
            return False
        return str(key.fingerprint) == expected_fingerprint
    except Exception:
        return False
