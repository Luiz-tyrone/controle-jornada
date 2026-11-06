# Controle de Jornada AGT LOG

Programa para Windows que lê os PDFs de jornada do rastreador, calcula as horas de cada motorista com as mesmas fórmulas da planilha de controle de jornada e gera o TXT de horas extras para o Domínio (Contábil).

## Como instalar no computador de quem vai usar

O repositório é **privado**: entre numa conta do GitHub com acesso a ele antes de baixar.

1. Abra a página **Releases** deste repositório (à direita, em "Releases").
2. Baixe o arquivo **`Controle de Jornada AGT LOG.exe`** da versão mais recente.
3. Dê dois cliques nele. Não precisa instalar o Python.
4. Na primeira vez, o programa procura a pasta de dados no servidor da empresa. Se não encontrar, use o botão **Servidor →** para escolher a pasta `DADOS DO NOVO SISTEMA QUE SUB PLANILHA`.

## O que o programa faz

- **Importar PDF**: lê o relatório de jornada do rastreador e grava no banco que fica no servidor.
- **Motoristas**: jornada diária e resumo do mês de cada motorista; duplo clique em um dia para corrigir (com motivo obrigatório).
- **Planilha do mês**: todos os motoristas lado a lado, no formato da aba RELATÓRIO da planilha.
- **Gerar TXT contábil**: gera o arquivo `HE_MMAAAA.txt` (rubricas 150 e 571, empresa 82), no mesmo layout do módulo Planilha de Horas do HUB RH. O código do funcionário no Domínio é confirmado uma vez por motorista e fica salvo.

## Para quem for alterar o código

```
py -m pip install -r requirements.txt
python teste_regras.py      # autoteste das regras (não mexe no servidor)
GERAR_EXE.bat               # gera o .exe em dist/
```

Nunca envie para este repositório bancos de dados, PDFs, planilhas ou qualquer dado de funcionários (o `.gitignore` já bloqueia esses arquivos).
