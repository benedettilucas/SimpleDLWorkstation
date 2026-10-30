import argparse
import json
import platform
import sys
from importlib import metadata
from pathlib import Path


def create_environment_yaml() -> str:
    packages: dict[str, tuple[str, str]] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            raise ValueError("Uma distribuição instalada não informa o nome do pacote.")

        normalized_name = name.lower().replace("_", "-").replace(".", "-")
        version = distribution.version
        existing = packages.get(normalized_name)
        if existing and existing[1] != version:
            raise ValueError(
                f"Versões conflitantes para o pacote {name}: "
                f"{existing[1]} e {version}."
            )
        packages[normalized_name] = (name, version)

    dependencies = [
        f"  - python={platform.python_version()}",
        "  - pip",
        "  - pip:",
    ]
    dependencies.extend(
        f"      - {json.dumps(f'{name}=={version}')}"
        for name, version in sorted(packages.values(), key=lambda item: item[0].lower())
    )

    return "\n".join(
        ["name: colab", "channels:", "  - defaults", "dependencies:", *dependencies, ""]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exporta os pacotes instalados em um ambiente Conda YAML."
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("environment.yml"),
        help="Arquivo de saída (use '-' para imprimir no terminal).",
    )
    args = parser.parse_args()

    environment_yaml = create_environment_yaml()
    if str(args.output) == "-":
        sys.stdout.write(environment_yaml)
    else:
        args.output.write_text(environment_yaml, encoding="utf-8")
        print(f"Ambiente exportado para {args.output}")


if __name__ == "__main__":
    main()
