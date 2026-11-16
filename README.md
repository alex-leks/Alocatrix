# Alocatrix

Aplicativo para **ensalamento automático de alunos**: distribui os matriculados em turmas da manhã e da tarde, respeitando capacidade e equilibrando tamanho e proporção de gênero entre as turmas. A distribuição é calculada por **otimização matemática** (programação inteira mista com OR-Tools + SCIP), não por heurísticas.

Desenvolvido para o Desafio UniSoma 2026 (caso ABC Aprendiz).

## O que faz

- Lê uma planilha `.xlsx` com as abas `Alunos` e `Turmas`.
- Corrige automaticamente erros comuns de preenchimento (por exemplo, `manha` em vez de `Manhã`, espaços extras, `feminino` em vez de `F`).
- Detecta dados faltantes ou inválidos e nomes duplicados, e deixa você decidir o que fazer com cada caso.
- Avisa quando há mais alunos do que vagas em algum turno.
- Calcula a distribuição ótima e mostra um resumo por turma com gráficos.
- Grava os resultados no próprio arquivo de entrada, nas abas `Ensalamento` e `Inconsistências`.

## Requisitos

- Python 3.12 (versão indicada no desafio)
- Bibliotecas listadas em `requirements.txt`
- `tkinter` para o botão "Procurar..." (já vem com o Python no Windows; no Linux: `sudo apt install python3-tk`). Se não estiver disponível, basta colar o caminho do arquivo no campo de texto.

## Instalação

```bash
git clone https://github.com/alex-leks/Alocatrix.git
cd Alocatrix
pip install -r requirements.txt
```

## Como executar

```bash
streamlit run app_ensalamento.py
```

O app abre no navegador. Clique em **Procurar...** (ou cole o caminho do `.xlsx`), revise as correções e pendências, e clique em **Rodar otimização**. Depois, use **Gravar resultado na planilha original** para salvar as abas de saída.

> Feche a planilha no Excel antes de gravar, senão o arquivo fica bloqueado para escrita.

## Formato da planilha de entrada

**Aba `Alunos`** (colunas identificadas pelo nome, sem diferenciar acentos ou maiúsculas):

| Nome | Sexo | Disponibilidade |
|------|------|-----------------|
| Maria da Silva | F | Manhã |
| João Souza | M | Tarde |

Valores aceitos: sexo `F`/`M` (também `feminino`, `masculino`, `fem`, `masc`); disponibilidade `Manhã`/`Tarde` (também `manha`, `m`, `t`).

**Aba `Turmas`**:

| Recursos | Quantidade |
|----------|-----------|
| turmas_manha | 2 |
| turmas_tarde | 2 |
| vagas_manha | 35 |
| vagas_tarde | 35 |

Todas as turmas do mesmo turno têm o mesmo número de vagas.

## Saída

Duas abas novas na própria planilha:

- **Ensalamento**: `Nome`, `Sexo`, `Disponibilidade`, `Turma`. As turmas seguem alfabeto contínuo: as da manhã recebem as primeiras letras (`MANHÃ - A`, `MANHÃ - B`) e as da tarde continuam a sequência (`TARDE - C`, `TARDE - D`).
- **Inconsistências**: registro de tudo que foi corrigido, preenchido pelo usuário, não alocado ou marcado como duplicidade.

## Tratamento de inconsistências

1. **Correções automáticas**: linhas vazias, linha de "Total", espaços extras e padronização de sexo/disponibilidade. Tudo é registrado no log.
2. **Dados faltantes ou inválidos**: para cada linha, você escolhe entre *não alocar* (padrão, fica registrada em `Inconsistências`) ou *completar os dados* manualmente.
3. **Nomes duplicados**: podem ser erro de cadastro ou homônimos. Por padrão todas as linhas são alocadas e a duplicidade fica registrada; você pode desmarcar as que não quer alocar.
4. **Capacidade**: se houver mais alunos que vagas totais em um turno, o app avisa e bloqueia a otimização.

## Modelo matemático

O problema é resolvido com **OR-Tools (`pywraplp`) usando o solver SCIP**, com limite de 60 segundos.

- **Variáveis**: `y[sexo, turno, sala]`, quantidade inteira de alunos de cada grupo (sexo × turno) em cada sala permitida. Trabalhar com contagens por grupo, e não com uma variável por aluno, reduz muito o tamanho do problema.
- **Restrições**:
  - todos os alunos de cada grupo são alocados;
  - alunos da manhã só vão para salas da manhã, e os da tarde só para as da tarde;
  - o número de alunos por sala não passa das vagas;
  - quebra de simetria entre salas do mesmo turno (tamanhos em ordem não crescente).
- **Função objetivo** (minimizar, com pesos): 
  - diferença entre a maior e a menor sala de cada turno (equilíbrio de quantidade);
  - desvio máximo da proporção de meninas em relação à proporção geral (`Emax`);
  - soma dos desvios de proporção de todas as salas.

  Os desvios de gênero são lineares, usando variáveis auxiliares `E[r] ≥ |N·f[r] − F·n[r]|`.
- Se o solver atingir o tempo limite, o app avisa que a solução é viável, mas não necessariamente ótima.

## Dados de teste e LGPD

Todas as planilhas de exemplo e de teste usadas neste projeto (`instancia_*.xlsx`) contêm **dados totalmente fictícios, gerados aleatoriamente por inteligência artificial**. Os nomes, sexos e disponibilidades **não correspondem a pessoas reais**, portanto não há dados pessoais e o conteúdo está de acordo com a **Lei Geral de Proteção de Dados (LGPD, Lei nº 13.709/2018)**.

Ao usar o Alocatrix com dados reais de alunos, o tratamento desses dados é de responsabilidade de quem os utiliza. O app roda localmente e não envia a planilha para nenhum servidor externo.

## Estrutura do repositório

```
app_ensalamento.py   # app Streamlit: limpeza, modelo de otimização e interface
requirements.txt     # dependências
LICENSE              # licença MIT
README.md
```

## Imagens opcionais

O app tenta carregar `unisoma.webp` e `logo_ABC.webp` na mesma pasta do script. Se os arquivos não existirem, ele roda normalmente, apenas sem os logos no cabeçalho. Elas não estão incluídas neste repositório.

## Licença

Distribuído sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.
