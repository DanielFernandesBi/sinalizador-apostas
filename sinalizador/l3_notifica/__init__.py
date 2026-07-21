"""L3 — notificação (Telegram) dos sinais confirmados e dos alertas.

Núcleo testável (`cartao`, `notifica`) depende só do Protocol `Bot` e da fachada
do `Banco` — sem rede nem token. `BotTelegram` é a implementação real, amarrada no
`cli`. Responsabilidades:
  - re-checar o preço no envio (E4.2): se a odd atual < a mínima aceitável, o
    cartão NÃO é enviado (janela fechou);
  - montar o cartão do sinal (E4.1) e registrá-lo em `notificacoes` (E4.4);
  - entregar os alertas pendentes (daemon mudo, drawdown, erro do L2 — E4.3).
"""
