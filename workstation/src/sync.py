import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from colab_cli.client import Client, Prod

# O CLI usa ~/.config/colab-cli em qualquer SO (ver auth.py: TOKEN_CONFIG_PATH).
# COLAB_CLI_CONFIG_DIR permite sobrescrever sem editar o script.
CONFIG_DIR = Path(os.environ.get("COLAB_CLI_CONFIG_DIR", Path.home() / ".config" / "colab-cli"))
CREDS_PATH = CONFIG_DIR / "token.json"      # OAuth (somente leitura)
STATE_PATH = CONFIG_DIR / "sessions.json"   # estado local com os nomes (vm0, vm1, ...)

log = logging.getLogger("sync")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def mask_tokens(state: dict) -> dict:
    """Cópia do estado com os tokens truncados, para o log."""
    masked = json.loads(json.dumps(state))
    for entry in masked.values():
        tok = entry.get("token")
        if tok:
            entry["token"] = tok[:12] + "...<oculto>"
    return masked


def load_state(path: Path) -> dict:
    if not path.exists():
        log.info("%s não existe; começando com estado vazio.", path)
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    # escrita atômica: grava em temporário e substitui
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def next_name(state: dict) -> str:
    """vm{N}, onde N = nº de sessões já registradas; avança se o nome estiver ocupado."""
    n = len(state)
    while f"vm{n}" in state:
        n += 1
    return f"vm{n}"


def build_entry(name, a, info) -> dict:
    expires = datetime.now(timezone.utc) + timedelta(seconds=info.token_expires_in_seconds)
    return {
        "name": name,
        "token": info.token,
        "url": info.url,
        "endpoint": a.endpoint,
        "token_expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "variant": a.variant.name,
        "accelerator": a.accelerator.value,
        "machine_shape": a.machine_shape.name,
        "kernel_id": None,
        "session_id": None,
        "last_execution": None,
        "running": None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Registra sessões Colab ativas no sessions.json do colab-cli.")
    p.add_argument("--show-tokens", action="store_true", help="não ocultar tokens no log")
    args = p.parse_args()
    setup_logging()

    log.info("Credenciais: %s | Estado: %s", CREDS_PATH, STATE_PATH)
    if not CREDS_PATH.exists():
        log.error("Credenciais OAuth não encontradas. Rode 'colab sessions' uma vez para autenticar.")
        return 1

    creds = Credentials.from_authorized_user_file(str(CREDS_PATH))
    if not creds.valid:
        creds.refresh(Request())
    client = Client(Prod(), AuthorizedSession(creds))

    assignments = client.list_assignments()
    log.info("Sessões ativas no servidor: %d", len(assignments))

    state = load_state(STATE_PATH)
    by_endpoint = {v["endpoint"]: k for k, v in state.items()}
    changed = False

    for a in assignments:
        info = a.runtime_proxy_info
        if a.endpoint in by_endpoint:
            name = by_endpoint[a.endpoint]
            fresh = build_entry(name, a, info)
            for k in ("token", "url", "token_expires_at"):
                state[name][k] = fresh[k]
            log.info("Sessão '%s' (%s) já registrada: token/url/validade renovados.", name, a.endpoint)
        else:
            name = next_name(state)
            state[name] = build_entry(name, a, info)
            log.info("Sessão nova (%s) registrada como '%s'.", a.endpoint, name)
        changed = True

    if not changed:
        log.info("Nenhuma sessão ativa; nada a gravar.")
        return 0

    save_state(STATE_PATH, state)

    logged = state if args.show_tokens else mask_tokens(state)
    log.info("JSON gravado em %s:\n%s", STATE_PATH, json.dumps(logged, indent=2))
    log.info("Informações das sessões gravadas com sucesso no state de sessões (%s).", STATE_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
