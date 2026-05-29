# HCL Bot — Hardcore Combat League

Bot Discord para a comunidade Hardcore Combat League (HCL) com painel interativo, estatísticas, tier list e histórico de partidas.

## Funcionalidades

- **Painel Interativo** (`!panel`) — Menu completo com botões para acessar todas as funções
- **Tier List** — Visualização por tier (Champion, S, A, B, C, D, F) com cores e emojis
- **Busca de Fighters** — Filtros por região, tier e afiliação
- **Player Lookup** — Card completo do jogador com record, streak, K/D, tier, afiliação
- **Match History** — Histórico detalhado de lutas de cada jogador
- **Latest Matches** — Partidas recentes com paginação
- **Eventos** — Lista de eventos futuros e passados
- **Stats** — Estatísticas gerais da liga (fighters, matches, distribuição por tier/afiliação)
- **Rankings** — Top 15 por wins, kills ou K/D
- **Cache Inteligente** — Cache em memória com TTL de 120s para players
- **Welcome Automático** — GIF + embed de boas-vindas para novos membros

## Tecnologias

- **Python 3.x** com asyncio
- **discord.py** v2.3+
- **aiohttp** para requisições HTTP assíncronas
- **Pillow** para redimensionamento de avatares

## Integrações

### HCL Manager API
- `https://hclmanager.replit.app/api`
- `/api/players` — Lista de jogadores
- `/api/matches` — Histórico de partidas
- `/api/events` — Eventos

## Comandos

| Comando | Descrição |
|---------|-----------|
| `!panel` | Abre o painel interativo com botões |
| `!tierlist` | Menu de seleção de tier |
| `!fighters` | Menu de filtros (região, tier, afiliação) |
| `!player <nome>` | Card completo de um fighter |
| `!history <nome>` | Histórico de lutas de um fighter |
| `!matches [n]` | Últimas partidas com paginação |
| `!events` | Lista de eventos |
| `!stats` | Estatísticas gerais da liga |
| `!top [wins\|kills\|kd]` | Rankings |
| `!refresh` | Limpa o cache e busca dados novos |
| `!help` | Lista de comandos |

## Sistema de Tiers

| Tier | Emoji | Cor |
|------|-------|-----|
| Champion | 👑 | 0xFFD700 |
| S | 🔵 | 0x00FFFF |
| A | 🟡 | 0xFFFF00 |
| B | ⚪ | 0xAAAAAA |
| C | 🟠 | 0xFF8800 |
| D | 🟢 | 0x00FF00 |
| F | 🔴 | 0xFF0000 |

## Estrutura

```
hcl-bot/
├── bot_hcl.py          # Código principal do bot
├── gif.gif             # GIF de welcome
├── requirements.txt    # Dependências
├── squarecloud.app     # Config de deploy SquareCloud
└── .git/
```

## Configuração

```bash
pip install -r requirements.txt
export DISCORD_TOKEN="seu_token_aqui"
python bot_hcl.py
```

## Cache

- **Players**: 120s TTL (evita dados obsoletos durante matchmaking)
- **Matches**: Válido até `!refresh` ou reinício
- **Events**: Válido até `!refresh` ou reinício
