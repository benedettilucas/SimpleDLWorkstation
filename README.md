# SimpleDLWorkstation
Repositório para instanciação de Virtual Machine (VM) usando servidores do Google Colab. A ideia deste repositório é simplificar o uso de computação em nuvem integrando um serviço gratuito do Google Colab com a praticidade da IDE do VS Code.

Pré-requisitos:
- [x] **VS Code**
- [x] **Git bash**
- [x] **Google Drive Desktop**
- [x] **ambiente virtual - conda/venv** (Opcional)
---

### <a id="passo-1"></a>Passo 1: instalação das dependências
- Instalar Google Drive para desktop: `https://ipv4.google.com/intl/pt-BR/drive/download/`
- Criar no Google Drive, uma pasta para ser seu ambiente de trabalho (workstation).
- Abrir o VS Code e selecionar o ambiente localmente (O padrão do Google Drive Desktop cria um disco `G:` na aba meu computador.)

### Passo 2: instalação das dependências
Após abrir o ambiente de trabalho no VS Code, deve-se instalar a dependência do `google-colab-cli`:
```bash
$ conda create -n ws python # opcional, mas é uma boa prática
$ conda activate ws
$ git clone https://github.com/benedettilucas/SimpleDLWorkstation.git
$ cd SimpleDLWorkstation/
$ pip install -e .
```

---

### Passo 3: instanciação da VM e execução de script python:
```bash
$ colab new -s vm --gpu t4 # cria uma sessao de VM com GPU T4
$ colab drivemount -s vm # monta o drive na sessao criada
$ colab exec -s vm -f scripts/check_vm.py # roda o script python e retorna as especificações de hardware da VM
$ colab exec -s vm -f scripts/launcher.py --env REPO=LightGlue # acessa e persiste o acesso a um pasta do Google Drive
$ echo 'pip install -e .' | colab exec -s vm # instala pacote 
$ colab ssh -s vm # acessa o shell da VM
$ colab stop -s vm # encerra a sessão da VM
```

## Alternativa: Simples integração do VS Code com Google Colab para uso de notebooks:
Após realizar o [Passo 1](#passo-1), acesse o Quick Open do VS Code usando o comando `Ctrl+P` e digite:
```bash
ext install Google.colab
```
Crie um notebook.ipynb e selecione o kernel de execução conforme descrito a seguir:

<img src="assets/hello-world.gif" alt="Hello World" width="600">

Na opção **'New Colab Server - CPU, GPU, TPU'**, é possível selecionar outros hardwares, como a GPU T4 gratuita. Para finalizar a sessão, basta acessar com `Ctrl+P` e digitar: `Colab: Remove Server`.

---

Links úteis:
- https://marketplace.visualstudio.com/items?itemName=Google.colab
- https://github.com/googlecolab/google-colab-cli