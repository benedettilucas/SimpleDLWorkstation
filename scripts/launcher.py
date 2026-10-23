import os
from pathlib import Path
import subprocess

REPOSITORIES_ROOT = Path("/content/drive/MyDrive/Documentos/Trabalho/workstation_isi/cv-factory")
def _shell_command(comando):
    """
    Executa um comando de terminal e retorna a sua saída de texto.
    """
    try:
        # shell=True permite passar o comando como uma string exata do terminal
        # capture_output=True captura o stdout (sucesso) e stderr (erro)
        # text=True decodifica o resultado de bytes para string
        resultado = subprocess.run(
            comando, 
            shell=True, 
            capture_output=True, 
            text=True, 
            check=True
        )
        print(f"{resultado.stdout}", end="")
        
    except subprocess.CalledProcessError as e:
        print(f"--- Erro ao executar: '{comando}' ---")
        print(f"Código de erro: {e.returncode}")
        print(e.stderr)

def main() -> None:
    repo_name = os.environ.get("REPO")
    if not repo_name:
        raise SystemExit(
            "Set the repository name with `colab exec --env REPO=LightGlue`."
        )

    repositories_root = REPOSITORIES_ROOT.resolve()
    repo_path = (repositories_root / repo_name).resolve()
    if not repo_path.is_relative_to(repositories_root):
        raise SystemExit(f"Invalid repository name: {repo_name}")
    if not repo_path.is_dir():
        raise SystemExit(
            f"Repository directory not found: {repo_path}. "
            "Check that Google Drive is mounted with `colab drivemount`."
        )

    os.chdir(repo_path)
    print(f"Working directory: {repo_path}")
    _shell_command("ls -l")
    print("-"*20)
    print(os.getpid())
    print(os.getcwd())
    



if __name__ == "__main__":
    main()
