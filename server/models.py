"""
hexfeed - modelos de dados
Classes Pydantic para validar dados que entram e saem da API.
"""

from pydantic import BaseModel, Field, model_validator
from typing import Optional


class RegisterRequest(BaseModel):
    """Requisição de cadastro: valida username, senha, PoW, desafio matemático."""
    username: str = Field(
        ...,
        min_length=3, max_length=30,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Nome de usuário: 3-30 chars, apenas letras/números/_",
    )
    password: str = Field(..., min_length=6, description="Senha: mínimo 6 caracteres")
    pgp_public_key: Optional[str] = Field("", description="Chave PGP pública (opcional)")
    pgp_private_key: Optional[str] = Field("", description="Chave PGP privada (reservada)")
    email: Optional[str] = Field("", description="Email (opcional)")
    pow_challenge: str = Field(..., description="Challenge PoW recebido do servidor")
    pow_nonce: int = Field(..., description="Nonce resolvido pelo cliente")
    math_answers: list[int] = Field(..., min_length=2, max_length=2, description="Respostas das 2 perguntas")


class ChallengeResponse(BaseModel):
    """Resposta com desafio PoW e perguntas matemáticas para o registro."""
    challenge: str
    difficulty: int
    questions: list[dict]


class LoginRequest(BaseModel):
    """Requisição de login: username + senha + chave PGP (se necessário)."""
    username: str = Field(..., min_length=1, max_length=30)
    password: str = Field(..., min_length=1, max_length=128)
    pgp_private_key: Optional[str] = Field("", description="Chave PGP privada")


class CreatePostRequest(BaseModel):
    """Requisição de criação de post: conteúdo textual e/ou arquivo anexado."""
    content: str = Field("", max_length=500)
    reply_to: Optional[int] = Field(None)
    file_id: Optional[int] = Field(None)

    @model_validator(mode="after")
    def check_content_or_file(self):
        """Garante que ao menos content ou file_id seja fornecido."""
        if not self.content and not self.file_id:
            raise ValueError("content or file_id must be provided")
        return self


class UpdateProfileRequest(BaseModel):
    """Requisição de atualização de perfil."""
    display_name: Optional[str] = Field("", max_length=50)
    bio: Optional[str] = Field("", max_length=500)
    email: Optional[str] = Field("", max_length=100)


class SendChatRequest(BaseModel):
    """Requisição de envio de mensagem no chat global."""
    content: str = Field(..., min_length=1, max_length=500)
    is_anonymous: bool = False


class SendDMRequest(BaseModel):
    """Requisição de envio de mensagem direta (com ou sem arquivo)."""
    content: str = Field(..., min_length=1, max_length=2000)
    file_id: Optional[int] = None
    dm_file_id: Optional[int] = None


class UserResponse(BaseModel):
    """Resposta com dados públicos de um usuário."""
    id: int
    username: str
    display_name: str
    bio: str
    avatar_path: str
    created_at: str
    follower_count: int = 0
    following_count: int = 0
    post_count: int = 0
    is_following: bool = False
    is_blocked: bool = False


class PostResponse(BaseModel):
    """Resposta com dados de uma publicação, incluindo metadados do autor e arquivo."""
    id: int
    user_id: int
    username: str
    display_name: str
    content: str
    reply_to: Optional[int]
    created_at: str
    edited_at: Optional[str]
    like_count: int = 0
    reply_count: int = 0
    is_liked: bool = False
    file_id: Optional[int] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None


class ChatMessageResponse(BaseModel):
    """Resposta com dados de uma mensagem do chat global."""
    id: int
    user_id: int
    username: str
    display_name: str
    content: str
    created_at: str
    is_anonymous: bool = False


class DMResponse(BaseModel):
    """Resposta com dados de uma mensagem direta, incluindo arquivos anexados."""
    id: int
    conversation_id: int
    sender_id: int
    sender_username: str
    sender_display_name: str
    content: str
    file_id: Optional[int] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    dm_file_id: Optional[int] = None
    dm_file_name: Optional[str] = None
    created_at: str


class ConversationResponse(BaseModel):
    """Resposta com dados de uma conversa de DM (resumo)."""
    id: int
    other_username: str
    other_display_name: str
    last_message: Optional[str] = None
    last_message_at: Optional[str] = None
    unread: int = 0


class FileResponse(BaseModel):
    """Resposta com metadados de um arquivo enviado."""
    id: int
    original_name: str
    size: int
    content_type: str
    uploaded_at: str


class SearchResponse(BaseModel):
    """Resposta da busca unificada: usuários, posts e arquivos."""
    users: list[UserResponse] = []
    posts: list[PostResponse] = []
    files: list[FileResponse] = []


class AuthResponse(BaseModel):
    """Resposta de autenticação: token JWT-like + dados do usuário."""
    token: str
    user: UserResponse


class CreateCommentRequest(BaseModel):
    """Requisição de criação de comentário."""
    content: str = Field(..., min_length=1, max_length=500)


class CommentResponse(BaseModel):
    """Resposta com dados de um comentário."""
    id: int
    post_id: int
    user_id: int
    username: str
    display_name: str
    content: str
    created_at: str
    edited_at: Optional[str] = None


class NotificationResponse(BaseModel):
    """Resposta com dados de uma notificação."""
    id: int
    type: str
    actor_id: int
    actor_username: str
    actor_display_name: str
    post_id: Optional[int] = None
    comment_id: Optional[int] = None
    post_preview: Optional[str] = None
    read: bool = False
    created_at: str


class BlockResponse(BaseModel):
    """Resposta com dados de um block."""
    blocked_id: int
    blocked_username: str
    blocked_display_name: str
    created_at: str


class FollowListResponse(BaseModel):
    """Resposta da lista de seguidores/seguindo."""
    users: list[UserResponse]


class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    members: list[str] = Field(..., min_length=1, max_length=7)


class GroupResponse(BaseModel):
    id: int
    name: str
    created_by: int
    member_count: int = 0
    last_message: Optional[str] = None
    last_message_at: Optional[str] = None
    last_sender_username: Optional[str] = None
    created_at: str


class GroupMessageResponse(BaseModel):
    id: int
    group_id: int
    sender_id: int
    sender_username: str
    sender_display_name: str
    content: str
    file_id: Optional[int] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    dm_file_id: Optional[int] = None
    dm_file_name: Optional[str] = None
    created_at: str


class GroupMemberResponse(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: str
    joined_at: str


class AddGroupMemberRequest(BaseModel):
    username: str = Field(..., min_length=1)


class SendGroupMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    file_id: Optional[int] = None
    dm_file_id: Optional[int] = None
