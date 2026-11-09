import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


def get_client() -> Client:
    """
    Cria e retorna o client do Supabase usando as credenciais do .env.
    Lança um erro claro se as variáveis não estiverem configuradas,
    em vez de deixar o supabase-py falhar com uma mensagem confusa.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_URL e/ou SUPABASE_KEY não estão definidas no .env. "
            "Copie o .env.example para .env e preencha com as credenciais do projeto."
        )
    return create_client(SUPABASE_URL, SUPABASE_KEY)