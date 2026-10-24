#!/usr/bin/env python3
"""Proxy local Jupyter -> runtime do Google Colab.

Uso:
    python colab_proxy.py                  # detecta a sessão ativa, inicia em segundo plano
    python colab_proxy.py --session NOME   # força uma sessão do sessions.json
    python colab_proxy.py --status         # mostra se está rodando
    python colab_proxy.py --stop           # encerra o proxy
    python colab_proxy.py --port 8900      # outra porta (vale também p/ --stop/--status)
    python colab_proxy.py --idle-timeout 30  # encerra após 30 min sem uso

Se nenhuma sessão estiver ativa, o script termina com erro (código 1).
"""
import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("COLAB_CLI_CONFIG_DIR", Path.home() / ".config" / "colab-cli"))
STATE = CONFIG_DIR / "sessions.json"
DEFAULT_PORT = 8899
PROBE_TIMEOUT = 10  # segundos, por sessão testada

SKIP = {"host", "origin", "content-length", "connection", "upgrade",
        "sec-websocket-key", "sec-websocket-version",
        "sec-websocket-extensions", "sec-websocket-protocol"}


class NoActiveSession(Exception):
    """Nenhuma sessão ativa/utilizável do Colab foi encontrada."""


# ----------------------------------------------------------------- utilidades
def pid_file(port: int) -> Path:
    return CONFIG_DIR / f"proxy-{port}.pid"


def log_file(port: int) -> Path:
    return CONFIG_DIR / f"proxy-{port}.log"


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def tail(path: Path, n: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


# ------------------------------------------------------- descoberta de sessão
def load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NoActiveSession(
            f"{STATE} não existe. Registre a sessão com o script de sincronização "
            "(ou rode 'colab sessions') e tente novamente."
        )
    except json.JSONDecodeError as ex:
        raise NoActiveSession(f"{STATE} está corrompido: {ex}")
    if not isinstance(data, dict) or not data:
        raise NoActiveSession(
            f"{STATE} não tem nenhuma sessão registrada. Inicie um runtime e rode o "
            "script de sincronização."
        )
    return data


def probe(entry) -> tuple:
    """Testa se o runtime responde com o token da entrada. Retorna (ativa, motivo)."""
    try:
        req = urllib.request.Request(
            entry["url"].rstrip("/") + "/api/kernels",
            headers={"X-Colab-Runtime-Proxy-Token": entry["token"],
                     "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
            return r.status == 200, f"HTTP {r.status}"
    except (KeyError, TypeError) as ex:
        return False, f"entrada incompleta ({ex!r})"
    except urllib.error.HTTPError as ex:  # antes de URLError (é subclasse)
        if ex.code in (401, 403):
            return False, f"HTTP {ex.code} (token expirado? rode o script de sincronização)"
        return False, f"HTTP {ex.code} (runtime provavelmente encerrado)"
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        return False, f"sem conexão ({ex})"


def resolve_session(requested=None) -> str:
    """Devolve o nome de uma sessão ativa ou levanta NoActiveSession."""
    state = load_state()
    if requested:
        if requested not in state:
            raise NoActiveSession(
                f"Sessão '{requested}' não está em {STATE}. Disponíveis: {', '.join(state)}"
            )
        candidates = [requested]
    else:
        candidates = list(state)

    active, failures = [], []
    for name in candidates:
        ok, why = probe(state[name])
        (active if ok else failures).append((name, why))

    if not active:
        detalhes = "\n".join(f"  - {n}: {w}" for n, w in failures)
        raise NoActiveSession(
            "Nenhuma sessão ativa encontrada.\n" + detalhes +
            "\nInicie um runtime (navegador ou 'colab') e rode o script de sincronização."
        )

    if len(active) > 1:
        # várias ativas: usa a de token mais recente
        def expiry(item):
            e = state[item[0]]
            return e.get("token_expires_at", "") if isinstance(e, dict) else ""
        active.sort(key=expiry, reverse=True)
        print(f"Aviso: {len(active)} sessões ativas ({', '.join(n for n, _ in active)}); "
              f"usando '{active[0][0]}'. Use --session para escolher.", file=sys.stderr)
    return active[0][0]


# --------------------------------------------------------------------- servidor
def serve(name: str, port: int, idle_min: float) -> None:
    import aiohttp
    from aiohttp import web, WSMsgType
    from yarl import URL

    last = {"t": time.time(), "ws": 0}

    def target():
        # relido a cada request: tokens renovados pelo script de sincronização valem na hora
        e = json.loads(STATE.read_text(encoding="utf-8"))[name]
        return e["url"].rstrip("/"), e["token"]

    async def handler(request: web.Request):
        last["t"] = time.time()
        base, token = target()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in SKIP}
        headers["X-Colab-Runtime-Proxy-Token"] = token

        async with aiohttp.ClientSession() as s:
            if request.headers.get("Upgrade", "").lower() == "websocket":
                protos = [p.strip() for p in
                          request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
                url = str(URL(base + request.path_qs).update_query(
                    {"colab-runtime-proxy-token": token}))
                print("WS pedido:", request.path, "protocolos:", protos, flush=True)
                try:
                    ws_cli = await s.ws_connect(url, headers=headers, protocols=protos,
                                                max_msg_size=0)
                except Exception as ex:
                    print("WS upstream FALHOU:", repr(ex), flush=True)
                    return web.Response(status=502, text=f"upstream ws: {ex!r}")

                print("WS upstream OK, protocolo:", ws_cli.protocol, flush=True)
                ws_srv = web.WebSocketResponse(
                    protocols=[ws_cli.protocol] if ws_cli.protocol else [], max_msg_size=0)
                await ws_srv.prepare(request)

                async def pump(src, dst, label):
                    n = 0
                    try:
                        async for m in src:
                            n += 1
                            last["t"] = time.time()
                            if m.type == WSMsgType.TEXT:
                                await dst.send_str(m.data)
                            elif m.type == WSMsgType.BINARY:
                                await dst.send_bytes(m.data)
                            else:
                                break
                    finally:
                        print(f"WS {label} encerrou ({n} msgs)", flush=True)
                        await dst.close()

                last["ws"] += 1
                try:
                    await asyncio.gather(pump(ws_srv, ws_cli, "vscode->colab"),
                                         pump(ws_cli, ws_srv, "colab->vscode"))
                finally:
                    last["ws"] -= 1
                    await ws_cli.close()
                return ws_srv

            body = await request.read()
            async with s.request(request.method, base + request.path_qs, headers=headers,
                                 data=body, allow_redirects=False) as r:
                data = await r.read()
                out = {k: v for k, v in r.headers.items()
                       if k.lower() not in ("content-encoding", "transfer-encoding",
                                            "content-length", "connection")}
                print(request.method, request.path, "->", r.status, flush=True)
                return web.Response(status=r.status, body=data, headers=out)

    def cleanup_pid():
        try:
            info = json.loads(pid_file(port).read_text())
            if info.get("pid") == os.getpid():
                pid_file(port).unlink()
        except Exception:
            pass

    async def watchdog():
        while True:
            await asyncio.sleep(30)
            if idle_min > 0 and last["ws"] == 0 and time.time() - last["t"] > idle_min * 60:
                print(f"Ocioso por {idle_min} min: encerrando.", flush=True)
                cleanup_pid()
                os._exit(0)

    async def on_startup(app):
        app["watchdog"] = asyncio.create_task(watchdog())

    app = web.Application()
    app.on_startup.append(on_startup)
    async def on_cleanup(app):
        cleanup_pid()

    app.on_cleanup.append(on_cleanup)
    app.router.add_route("*", "/{tail:.*}", handler)
    print(f"Proxy da sessão '{name}' em http://127.0.0.1:{port} (pid {os.getpid()})", flush=True)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


# --------------------------------------------------------------------- launcher
def start(port: int, idle_min: float, requested=None) -> int:
    if port_open(port):
        print(f"A porta {port} já está em uso (proxy já rodando?). Use --status ou --stop.")
        return 0

    name = resolve_session(requested)  # levanta NoActiveSession se não houver sessão ativa
    print(f"Sessão ativa detectada: '{name}'")

    cmd = [sys.executable, str(Path(__file__).resolve()), "--serve",
           "--session", name, "--port", str(port), "--idle-timeout", str(idle_min)]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(log_file(port), "ab")
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         close_fds=True, **kwargs)
    pid_file(port).write_text(json.dumps({"pid": p.pid, "port": port, "session": name}))

    deadline = time.time() + 15
    while time.time() < deadline:
        if p.poll() is not None:
            print(f"O proxy encerrou ao iniciar (código {p.returncode}). Log:\n{tail(log_file(port))}")
            return 1
        if port_open(port):
            print(f"Proxy rodando em segundo plano (pid {p.pid}).")
            print(f"  VS Code: Select Kernel > Existing Jupyter Server > http://127.0.0.1:{port}")
            print(f"  Log:     {log_file(port)}")
            print(f"  Parar:   python {Path(__file__).name} --stop"
                  + (f" --port {port}" if port != DEFAULT_PORT else ""))
            return 0
        time.sleep(0.3)

    print("Tempo esgotado esperando a porta abrir. Log:\n" + tail(log_file(port)))
    return 1


def stop(port: int) -> int:
    try:
        info = json.loads(pid_file(port).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"Nenhum proxy registrado na porta {port}.")
        return 1
    try:
        os.kill(info["pid"], signal.SIGTERM)  # no Windows, termina o processo
        print(f"Proxy (pid {info['pid']}, sessão '{info.get('session')}') encerrado.")
    except (ProcessLookupError, OSError):
        print("O processo já não existia.")
    pid_file(port).unlink(missing_ok=True)
    return 0


def status(port: int) -> int:
    try:
        info = json.loads(pid_file(port).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        info = {}
    up = port_open(port)
    if up:
        print(f"Rodando na porta {port} (sessão '{info.get('session', '?')}', pid {info.get('pid', '?')}).")
    else:
        print(f"Parado (nada escutando na porta {port}).")
    return 0 if up else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Proxy local Jupyter -> runtime do Colab")
    ap.add_argument("--session", help="nome em sessions.json (padrão: detecta a sessão ativa)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--idle-timeout", type=float, default=0,
                    help="minutos sem uso para encerrar (0 = nunca)")
    ap.add_argument("--stop", action="store_true", help="encerra o proxy da porta")
    ap.add_argument("--status", action="store_true", help="mostra se o proxy está rodando")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)  # uso interno
    a = ap.parse_args()

    try:
        if a.stop:
            return stop(a.port)
        if a.status:
            return status(a.port)
        if a.serve:
            serve(a.session, a.port, a.idle_timeout)
            return 0
        return start(a.port, a.idle_timeout, a.session)
    except NoActiveSession as ex:
        print(f"Erro: {ex}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as ex:  # qualquer falha inesperada vira mensagem + código 1
        print(f"Erro inesperado: {ex!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
