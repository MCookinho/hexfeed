import os, sys, tempfile, pytest
from pathlib import Path

os.environ["HEXFEED_TESTING"] = "1"

import server.database as dbmod
import server.auth as authmod
from server.database import init_db, get_connection, close_connection
from server.auth import hash_password, create_token

_test_dir = Path(tempfile.mkdtemp(prefix="hexfeed_test_"))
dbmod.DB_DIR = _test_dir
dbmod.DB_PATH = _test_dir / "hexfeed.db"
dbmod.DM_UPLOAD_DIR = _test_dir / "dm_uploads"

from server.main import app
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def test_dir():
    yield _test_dir
    import shutil
    shutil.rmtree(_test_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_db():
    db_file = dbmod.DB_PATH
    if db_file.exists():
        db_file.unlink()
    dm_dir = dbmod.DM_UPLOAD_DIR
    if dm_dir.exists():
        import shutil
        shutil.rmtree(str(dm_dir), ignore_errors=True)

    dbmod._connections.clear()
    init_db()

    conn = get_connection()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-1")
    conn.close()

    yield

    for c in list(dbmod._connections):
        close_connection(c)
    dbmod._connections.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def user1(client):
    """Cria usuário 'teste1' com token conhecido e retorna {token, id, username}."""
    # Desabilita PoW pulando a verificação via injeção de token direto
    conn = get_connection()
    pw_hash = hash_password("senha123")
    conn.execute(
        "INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
        ("teste1", pw_hash, "Teste Um"),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM users WHERE username = ?", ("teste1",)).fetchone()
    uid = row["id"]
    token = create_token(uid, conn=conn)
    close_connection(conn)
    return {"token": token, "id": uid, "username": "teste1"}


@pytest.fixture
def user2(client):
    """Segundo usuário para testes de interação."""
    conn = get_connection()
    pw_hash = hash_password("senha456")
    conn.execute(
        "INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
        ("teste2", pw_hash, "Teste Dois"),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM users WHERE username = ?", ("teste2",)).fetchone()
    uid = row["id"]
    token = create_token(uid, conn=conn)
    close_connection(conn)
    return {"token": token, "id": uid, "username": "teste2"}


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
