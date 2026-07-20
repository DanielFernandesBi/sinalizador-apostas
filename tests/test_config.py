"""Testes da config por camada: universais obrigatórios, resto cobrado no uso."""
import pytest

from sinalizador.comum.config import Config, SegredoAusenteError


def _cfg(**over):
    base = {"supabase_url": "https://x.supabase.co", "supabase_service_role_key": "sb-secret"}
    base.update(over)
    return Config(**base)  # kwargs sobrepõem .env/ambiente


def test_universais_carregam_sem_os_demais():
    cfg = _cfg()
    assert cfg.supabase_url.endswith("supabase.co")
    assert cfg.the_odds_api_key is None and cfg.oddspapi_api_key is None


def test_exigir_devolve_quando_presente():
    cfg = _cfg(the_odds_api_key="odds-123")
    assert cfg.exigir("the_odds_api_key") == "odds-123"
    assert cfg.exigir("supabase_url").endswith("supabase.co")


def test_exigir_falha_alto_quando_ausente():
    cfg = _cfg()  # sem the_odds_api_key
    with pytest.raises(SegredoAusenteError) as exc:
        cfg.exigir("the_odds_api_key")
    assert "the_odds_api_key" in str(exc.value)


def test_exigir_oddspapi_ausente_por_padrao():
    with pytest.raises(SegredoAusenteError):
        _cfg().exigir("oddspapi_api_key")
