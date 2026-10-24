# colab_proxy

Proxy local que permite ao **VS Code** (Jupyter) usar um runtime do **Google Colab** como kernel, através da opção *Existing Jupyter Server*.

O runtime do Colab é um servidor Jupyter atrás de um proxy que exige o token no cabeçalho `X-Colab-Runtime-Proxy-Token`. O VS Code não sabe enviar esse cabeçalho, então este script abre um servidor em `127.0.0.1` que repassa HTTP e WebSocket ao Colab, adicionando o token.

> **Não oficial.** Usa a API interna de atribuições do Colab (a mesma do `colab-cli`), que pode mudar sem aviso. Existe também a extensão oficial do Colab para VS Code, que pode atender se você não precisa reaproveitar sessões já existentes.

## Requisitos

- Python 3.9+
- `pip install aiohttp`
- Um **runtime do Colab ativo** (aberto no navegador ou iniciado pelo `colab` CLI)
- Esse runtime registrado em `~/.config/colab-cli/sessions.json` (veja [Como a sessão é encontrada](#como-a-sessão-é-encontrada))

## Uso rápido

```bash
python colab_proxy.py
```

O script:

1. lê o `sessions.json` e testa quais sessões estão realmente ativas;
2. inicia o proxy **em segundo plano**, na porta 8899;
3. **devolve o terminal** e termina. O proxy continua rodando como processo próprio.

Depois, no VS Code:

1. Abra o notebook e clique em **Select Kernel**.
2. Escolha **Select Another Kernel… → Existing Jupyter Server…**
3. Informe `http://127.0.0.1:8899`. Se pedir senha ou token, deixe em branco.
4. Escolha o kernel *Python 3 (ipykernel)*.

Se não houver sessão ativa, o script termina com código `1` e uma mensagem de erro explicando o motivo (veja [Erros](#erros)).

## Opções

| Opção | Descrição |
|---|---|
| *(nenhuma)* | Detecta a sessão ativa e inicia o proxy em segundo plano |
| `--session NOME` | Usa uma entrada específica do `sessions.json` em vez de detectar |
| `--port N` | Porta local (padrão `8899`). Vale também para `--stop` e `--status` |
| `--idle-timeout MIN` | O proxy se encerra após `MIN` minutos sem tráfego e sem WebSocket aberto (padrão `0` = nunca) |
| `--status` | Informa se o proxy está rodando, em qual porta e para qual sessão |
| `--stop` | Encerra o proxy da porta indicada |

Exemplos:

```bash
python colab_proxy.py --status
python colab_proxy.py --stop
python colab_proxy.py --idle-timeout 30          # não fica esquecido em segundo plano
python colab_proxy.py --port 8900 --session vm1  # segundo proxy, outra sessão
python colab_proxy.py --port 8900 --stop
```

## Como a sessão é encontrada

O estado das sessões fica em `~/.config/colab-cli/sessions.json`, no formato:

```json
{
  "vm0": {
    "name": "vm0",
    "token": "eyJ...",
    "url": "https://8080-gpu-t4-....prod.r.colab.dev",
    "endpoint": "gpu-t4-s-....",
    "token_expires_at": "2026-10-24T20:34:18.701810Z"
  }
}
```

Ao iniciar sem `--session`, o script faz `GET <url>/api/kernels` em cada entrada, enviando o token do arquivo:

- **HTTP 200**: sessão ativa e com token válido.
- **401/403**: token expirado (renove-o, veja abaixo).
- **Outro erro ou sem conexão**: runtime provavelmente encerrado.

Se houver mais de uma sessão ativa, o script usa a de `token_expires_at` mais recente e avisa qual escolheu. Para outra, use `--session`.

> O arquivo `~/.config/colab-cli/token.json` guarda as **credenciais OAuth** do `colab-cli` e **não** é usado por este script. Nunca o aponte como estado de sessões.

## Renovação do token

O token do runtime vale cerca de **1 hora**. O proxy relê o `sessions.json` a cada requisição, então basta atualizar o arquivo, sem reiniciar o proxy. Isso é feito pelo script de sincronização, que consulta `GET /tun/m/assignments` e grava o novo `token`, `url` e `token_expires_at`. Para longas sessões, rode-o periodicamente (a cada ~50 minutos).

## Erros

O script usa a exceção `NoActiveSession` e sai com código `1`:

| Mensagem | Causa | O que fazer |
|---|---|---|
| `sessions.json não existe` | O estado nunca foi gerado | Rode o script de sincronização ou `colab sessions` |
| `não tem nenhuma sessão registrada` | Arquivo vazio | Inicie um runtime e sincronize |
| `Sessão 'X' não está em ...` | `--session` com nome inexistente | Use um dos nomes listados na mensagem |
| `Nenhuma sessão ativa encontrada` + `HTTP 401/403` | Token expirado | Rode o script de sincronização |
| `Nenhuma sessão ativa encontrada` + `HTTP 4xx/5xx` ou `sem conexão` | Runtime encerrado ou inalcançável | Inicie outro runtime e sincronize |
| `A porta N já está em uso` | Já há um proxy (ou outro programa) na porta | `--status`, `--stop` ou `--port` diferente |

## Arquivos gerados

Em `~/.config/colab-cli/` (ou em `$COLAB_CLI_CONFIG_DIR`, se definida):

- `proxy-<porta>.pid`: pid, porta e sessão do proxy em execução
- `proxy-<porta>.log`: log das requisições e dos WebSockets (`tail -f` no Linux/macOS/Git Bash, `Get-Content -Wait` no PowerShell)

## Notas técnicas

- **Segundo plano.** No Windows o proxy é iniciado com `DETACHED_PROCESS`; em Linux/macOS, com uma sessão nova (`start_new_session`). Fechar o terminal não o encerra.
- **Subprotocolo WebSocket.** O VS Code primeiro pede `v1.kernel.websocket.jupyter.org`, que o Colab não suporta. Essa primeira conexão falha (`Client protocols ... don't overlap`) e o VS Code faz fallback para o protocolo antigo. Isso é esperado e aparece no log, sem ser um problema.
- **Kernel novo.** O VS Code cria um kernel novo na mesma VM (mesma GPU e disco). As variáveis do notebook aberto no navegador não são compartilhadas.
- **Interrupção.** Se uma célula presa não responde a *Interrupt*, é provável que o código esteja em extensão nativa (CUDA, workers do `DataLoader`). Reinicie o kernel pelo VS Code.

## Segurança

- O proxy escuta **apenas em `127.0.0.1`**. Não altere isso: quem alcançar a porta usa o seu runtime com o seu token.
- O token do runtime é uma credencial. Não o cole em logs, issues ou commits.
- Use `--idle-timeout` para que o proxy não fique esquecido em segundo plano.
- Se colab_proxy.py retorna: `A porta 8899 já está em uso (proxy já rodando?). Use --status ou --stop.`, siga os comandos a seguir:
```bash
netstat -ano | grep :8899        # última coluna é o PID (LISTENING)
tasklist | grep <PID>            # confirma que é um python.exe
taskkill //PID <PID> //F
```