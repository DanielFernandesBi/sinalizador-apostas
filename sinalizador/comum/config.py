"""Configuração do sistema — segredos e parâmetros de ambiente.

Carregada de variáveis de ambiente / `.env` (fora do git — regra 9). "Dado
ausente = abortar" (Doutrina P6) vale para configuração — mas **por camada**:
só os segredos UNIVERSAIS (o acesso ao Supabase, que toda camada usa) são
obrigatórios na carga. Os demais são opcionais no schema e cobrados **no ponto de
uso** por `exigir(...)`: assim o daemon do L0 não precisa da chave do Telegram, e
o do L3 não precisa da The Odds API — cada processo falha alto só pelo segredo que
ELE consome, nunca pelos das outras camadas.

A carga é preguiçosa (`carregar_config()`), então importar o módulo não exige o
`.env` presente — só chamá-lo exige (e só os universais).

PRECEDÊNCIA (importante): o **`.env` do projeto vence a variável de ambiente do
SO**. O padrão do pydantic-settings é o contrário (env do SO > `.env`), o que já
sequestrou este projeto uma vez — uma variável global `SUPABASE_URL` de OUTRO
projeto apontava para outro Supabase e o `.env` correto era ignorado (host morto →
`getaddrinfo failed`). Aqui cada projeto é dono do seu `.env`: variável global de
outro projeto não interfere. Em servidor sem `.env` (VPS/CI), a env do SO segue
valendo (a fonte `.env` fica vazia). `init` (kwargs, usados em teste) continua no
topo.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class SegredoAusenteError(RuntimeError):
    """Um segredo exigido por esta camada não está no ambiente/.env (P6)."""


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # `.env` ANTES da env do SO: o projeto é dono da sua config, imune à
        # interferência de variáveis globais de outros projetos (ver docstring).
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)

    # Universais — toda camada fala com o banco. Obrigatórios na carga.
    supabase_url: str = Field(..., description="URL do projeto Supabase próprio")
    supabase_service_role_key: str = Field(
        ..., description="Service role key — acesso total, usado só via comum/db.py"
    )

    # Por camada — opcionais no schema, cobrados no ponto de uso via `exigir`.
    the_odds_api_key: Optional[str] = Field(None, description="The Odds API (L0 referência/varejo)")
    anthropic_api_key: Optional[str] = Field(None, description="Anthropic (L2 crivo)")
    telegram_bot_token: Optional[str] = Field(None, description="Bot Telegram (L3)")
    telegram_chat_id: Optional[str] = Field(None, description="Canal privado de destino (L3)")
    # Experimental (sonda de avaliação PC-VENUE) — nunca obrigatório.
    oddspapi_api_key: Optional[str] = Field(None, description="OddsPapi (sonda de avaliação)")

    def exigir(self, campo: str) -> str:
        """Valor do segredo `campo` ou falha alto se ausente (P6, por camada)."""
        valor = getattr(self, campo, None)
        if not valor:
            raise SegredoAusenteError(
                f"segredo obrigatório para esta camada ausente: {campo!r} — "
                f"defina-o no .env"
            )
        return valor


@lru_cache(maxsize=1)
def carregar_config() -> Config:
    """Instancia a Config uma vez. Levanta erro claro se faltar segredo UNIVERSAL."""
    return Config()  # type: ignore[call-arg]  # campos vêm do ambiente/.env
