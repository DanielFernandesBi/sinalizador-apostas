"""Configuração do sistema — segredos e parâmetros de ambiente.

Carregada de variáveis de ambiente / `.env` (fora do git — regra 9). "Dado
ausente = abortar" (Doutrina P6) vale para configuração: a falta de qualquer
segredo obrigatório levanta `ValidationError` na carga e o processo NÃO sobe.

A carga é preguiçosa (`carregar_config()`), então importar o módulo não exige
o `.env` presente — só chamá-lo exige.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = Field(..., description="URL do projeto Supabase próprio")
    supabase_service_role_key: str = Field(
        ..., description="Service role key — acesso total, usado só via comum/db.py"
    )
    the_odds_api_key: str = Field(..., description="Chave The Odds API (L0 referência/varejo)")
    anthropic_api_key: str = Field(..., description="Chave Anthropic (L2 crivo)")
    telegram_bot_token: str = Field(..., description="Token do bot Telegram (L3)")
    telegram_chat_id: str = Field(..., description="Chat/canal privado de destino (L3)")


@lru_cache(maxsize=1)
def carregar_config() -> Config:
    """Instancia a Config uma vez. Levanta erro claro se faltar segredo obrigatório."""
    return Config()  # type: ignore[call-arg]  # campos vêm do ambiente/.env
