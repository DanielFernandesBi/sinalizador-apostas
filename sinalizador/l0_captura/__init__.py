"""L0 — captura de odds (referência, varejo) e vigia de heartbeats.

Núcleo testável (the_odds_api, mapeamento, persistencia, captura, cobertura,
vigia) depende só de objetos injetados — sem Supabase nem rede. O `cli` amarra
tudo à infraestrutura real (Banco + ClienteOddsAPI) e roda na máquina do Daniel
ou no VPS.
"""
