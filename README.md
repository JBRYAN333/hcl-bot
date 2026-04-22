# HCL Bot - Hardcore Combat League

Bot Discord para a comunidade Hardcore Combat League (HCL) com sistema completo de gerenciamento de ligas, estatísticas e rankings.

## Descrição

Bot desenvolvido em Python para a comunidade Hardcore Combat League, uma liga competitiva organizada, que fornece acesso a estatísticas detalhadas de jogadores, histórico de partidas, eventos programados e sistema de tier list através de comandos no Discord.

## Funcionalidades

- **Sistema de Players**: Consulta completa de jogadores com win/loss records, afiliações e avatares
- **Histórico de Matches**: Acesso a partidas anteriores com resultados detalhados
- **Eventos Calendarizados**: Visualização de eventos futuros e passados da liga
- **Tier List Dinâmica**: Sistema de tiers (Champion, S, A, B, C, D, F) com cores e emojis
- **Cache Inteligente**: Otimização de performance com cache em memória
- **Welcome System**: Mensagem de boas-vindas com GIF personalizado para novos membros
- **Geração de Imagens**: Sistema para criar imagens com avatares e informações de jogadores
- **API Integration**: Conexão completa com sistema de gerenciamento HCL

## Tecnologias

- **Python 3.x** com asyncio para concorrência
- **discord.py** v2.3.0+ para integração robusta com Discord
- **aiohttp** para requisições HTTP assíncronas à API HCL
- **PIL (Pillow)** para manipulação e geração de imagens
- **Base64** para encoding/decoding de dados de imagem
- **SquareCloud** para deploy em nuvem (pasta SquareCloud incluída)

## Integrações Principais

### HCL Manager API
- **URL Base**: `https://hclmanager.replit.app`
- **API Endpoint**: `https://hclmanager.replit.app/api`
- **Endpoints Disponíveis**:
  - `/api/players` - Lista completa de jogadores
  - `/api/matches` - Histórico de partidas
  - `/api/events` - Eventos calendarizados

### Discord Features
- Sistema de comandos com prefixo `!`
- Embeds coloridos por tier
- Welcome automático em canal específico
- Suporte a upload de imagens/avatares

## Sistema de Tiers HCL

| Tier | Emoji | Cor (Hex) | Descrição | Nível |
|------|-------|-----------|-----------|-------|
| Champion | 👑 | 0xFFD700 (Ouro) | Campeão da liga | Top 1 |
| S | 🔵 | 0x00FFFF (Ciano) | Elite - Top players | Alto |
| A | 🟡 | 0xFFFF00 (Amarelo) | Avançado - Skilled | Intermediário-Alto |
| B | ⚪ | 0xAAAAAA (Cinza) | Intermediário | Médio |
| C | 🟠 | 0xFF8800 (Laranja) | Iniciante+ | Baixo-Intermediário |
| D | 🟢 | 0x00FF00 (Verde) | Iniciante | Básico |
| F | 🔴 | 0xFF0000 (Vermelho) | Novato | Entry Level |

## Comandos Disponíveis

### Comandos de Consulta
- `!players` - Lista todos os jogadores com estatísticas básicas
- `!player [nome]` - Estatísticas detalhadas de um jogador específico
- `!matches` - Histórico de partidas recentes
- `!events` - Eventos programados da liga
- `!tierlist` - Ranking completo por tier
- `!search [termo]` - Busca jogadores por nome ou afiliação

### Comandos de Utilidade
- `!help` - Lista de comandos disponíveis
- `!ping` - Verifica latência do bot
- `!invite` - Link de convite do bot
- Sistema automático de welcome para novos membros

## Estrutura do Projeto

```
HCL Bot Discord/
├── bot_hcl.py              # Código principal do bot (43.5KB)
├── scrapper.py             # Versão alternativa/backup (44.4KB)
├── hcl_notbook.py          # Notebook para análise de dados (47.5KB)
├── hcl_full_data_2026-03-24_16-00.json  # Dump completo de dados (5.9MB)
├── hcl_tierlist_full.json  # Tier list em formato JSON (53 bytes)
├── gif.gif                 # GIF para mensagem de welcome
├── SquareCloud/            # Configuração completa para deploy
│   ├── config.json        # Configuração do deploy
│   └── outros arquivos de deploy
└── .git/                  # Controle de versão Git
```

## Configuração e Deploy

### Dependências
```bash
pip install discord.py aiohttp pillow
```

### Variáveis de Ambiente
```bash
# Token do Discord Bot
export DISCORD_TOKEN="seu_token_aqui"

# Configuração opcional para Windows
set asyncio.WindowsSelectorEventLoopPolicy
```

### Execução Local
```bash
python bot_hcl.py
```

### Deploy na SquareCloud
O projeto está configurado para deploy na SquareCloud com:
- Configuração de recursos (CPU, RAM)
- Configuração de rede e portas
- Scripts de inicialização
- Monitoramento de saúde

## Sistema de Cache

O bot implementa cache triplo para máxima performance:

1. **Cache de Players** (`players_cache`): Dados de jogadores
2. **Cache de Matches** (`matches_cache`): Histórico de partidas  
3. **Cache de Events** (`events_cache`): Eventos calendarizados

**Benefícios**:
- Redução de chamadas à API externa
- Resposta instantânea a comandos frequentes
- Atualização apenas quando necessário
- Economia de banda e recursos

## Casos de Uso da Liga HCL

### Para Organizadores
- Gerenciamento centralizado de estatísticas
- Divulgação automática de eventos
- Sistema de tier para classificação de jogadores
- Ferramentas para scouting e análise

### Para Jogadores
- Acompanhamento de próprio progresso
- Comparação com outros jogadores
- Informação sobre eventos futuros
- Reconhecimento por achievements

### Para Comunidade
- Transparência nos rankings
- Engajamento através de competição saudável
- Centralização de informações da liga
- Fácil acesso a dados históricos

## Recursos Avançados

### Geração de Imagens
- Sistema para criar cards de jogadores
- Combinação de avatar + informações
- Suporte a base64 encoding/decoding
- Integração com Pillow para manipulação

### Sistema de Welcome
- GIF personalizado para novos membros
- Mensagem configurável de boas-vindas
- Integração com intents do Discord
- Canal específico para welcome messages

### Suporte Multiplataforma
- Configuração específica para Windows
- Suporte a diferentes event loop policies
- Compatibilidade com vários sistemas operacionais

## Roadmap e Melhorias Futuras

1. **Integração com Twitch** - Alertas de stream de jogadores
2. **Sistema de Apostas** - Preditores para partidas futuras
3. **Estatísticas Avançadas** - K/D ratio, win streaks, etc.
4. **Mobile App** - Aplicativo complementar para estatísticas
5. **API Pública** - Para desenvolvedores criarem ferramentas adicionais
6. **Sistema de Torneios** - Gerenciamento completo de brackets

## Contribuição

O projeto está aberto para contribuições que:
- Melhorem performance ou estabilidade
- Adicionem funcionalidades úteis para a comunidade HCL
- Corrijam bugs ou problemas de compatibilidade
- Melhorem documentação ou experiência do usuário

## Contato e Suporte

Para suporte, reportar bugs ou sugerir features:
- Discord: Servidor da comunidade HCL
- GitHub: Issues no repositório do projeto
- Email: sufocoprojeto@gmail.com

---

*Bot desenvolvido para fortalecer a comunidade Hardcore Combat League e promover competição saudável no cenário competitivo.*