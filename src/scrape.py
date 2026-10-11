"""
Etapa 1 — coleta de dados de tabuademares.com/br/paraiba/joao-pessoa.

Objetivo: gravar em data/raw/ os CSVs que as Etapas 2 e 3 vão consumir.

    mares_2025.csv       tábua de marés do ano inteiro (12 meses, ~4 marés por dia)
    mares_2026.csv       tábua dos 3 últimos meses (ago-out), perto da janela de previsão
    mares_previsao.csv   marés do mês corrente, para cruzar com a previsão
    ondas.csv            altura de onda hora a hora (~7 dias à frente)
    vento.csv            velocidade do vento hora a hora (~7 dias à frente)

Colunas: marés (data, hora, altura_m, tipo, coeficiente, lua_idade); ondas e
vento (data, hora, direcao, valor, unidade). Os seletores foram descobertos no
HTML salvo em data/amostras/ (ver docstrings de cada parser).

Rodar com: uv run python src/scrape.py
"""

import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup


URL_BASE = "https://tabuademares.com/br/paraiba/joao-pessoa"
URL_ONDAS = f"{URL_BASE}/previsao/ondas"
URL_VENTO = f"{URL_BASE}/previsao/vento"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

PAUSA = 1.5

MESES = {
    m: i
    for i, m in enumerate(
        ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"],
        start=1,
    )
}

DIR_RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def baixar_html(url: str, dados_post: dict | None = None) -> str:
    """Baixa uma página e devolve o HTML.

    Use POST (passando `dados_post`) quando a página só devolver o conteúdo
    que você quer em resposta a um formulário; GET no resto.

    `resp.raise_for_status()` falha alto quando o servidor recusa.
    """
    if dados_post is None:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    else:
        resp = requests.post(url, headers=HEADERS, data=dados_post, timeout=30)
    print(f"{resp.request.method} {url} -> {resp.status_code}, {len(resp.text)} caracteres")
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def _numero(texto: str) -> float | None:
    """Primeiro número de um texto, aceitando vírgula decimal ('1,1 m' -> 1.1)."""
    m = re.search(r"-?\d+(?:,\d+)?", texto)
    return float(m.group().replace(",", ".")) if m else None


def parsear_mares(html: str, ano: int, mes: int) -> list[dict]:
    """Extrai da tábua mensal uma linha por evento de maré.

    Cada dia tem cerca de 4 eventos (duas altas, duas baixas). Além de
    horário e altura, a página traz informação de coeficiente de maré e de
    fase da lua — decida o que vale a pena capturar.

    Colunas: data, hora, altura_m, tipo ('alta'|'baixa'), coeficiente,
    lua_idade (índice 0-29 do ícone da lua, ver nota abaixo).
    """
    soup = BeautifulSoup(html, "lxml")
    linhas = []
    for tr in soup.select("table#tabla_mareas tr[onclick]"):
        y, m, d = map(int, re.search(r"Day\('(\d+)-(\d+)-(\d+)'\)", tr["onclick"]).groups())
        if (y, m) != (ano, mes):
            continue
        data = date(y, m, d)

        coef = _numero(tr.select_one(".tabla_mareas_coeficiente_numero").get_text(" ", strip=True))

        icone = tr.select_one(".tabla_mareas_luna span")
        lua = next(int(c[7:]) for c in icone["class"] if re.fullmatch(r"icon-hs\d+", c))

        for celula in tr.select("td.tabla_mareas_marea"):
            if celula.select_one(".tabla_mareas_marea_hora") is None:
                continue
            hora = celula.select_one(".tabla_mareas_marea_hora").get_text(strip=True)
            altura = _numero(celula.select_one(".tabla_mareas_marea_altura_numero").text)
            tipo = "alta" if celula.select_one(".tabla_mareas_marea_pleamar") else "baixa"
            linhas.append(
                {
                    "data": data.isoformat(),
                    "hora": f"{int(hora.split(':')[0]):02d}:{hora.split(':')[1]}",
                    "altura_m": altura,
                    "tipo": tipo,
                    "coeficiente": coef,
                    "lua_idade": lua,
                }
            )
    return linhas


def parsear_previsao(html: str, ano: int) -> list[dict]:
    """Extrai leituras horárias das páginas de previsão de onda e de vento.

    As duas páginas têm o mesmo layout: um bloco por dia, com uma linha por
    hora dentro. Uma função só deve dar conta das duas.

    Atenção à data: o bloco mostra dia e mês abreviado, sem o ano.

    Colunas: data, hora, direcao, valor, unidade (m para onda, km/h para vento).
    `ano` é o ano do primeiro bloco; se o mês voltar de DEZ para JAN, soma 1.
    """
    soup = BeautifulSoup(html, "lxml")
    linhas = []
    mes_anterior = 0
    for bloco in soup.select("div.f_cont"):
        dia = int(bloco.select_one(".f_circulo .dia").text)
        mes = MESES[bloco.select_one(".f_circulo .mes").text.strip().upper()]
        if mes < mes_anterior:
            ano += 1
        mes_anterior = mes
        data = date(ano, mes, dia)

        for h in bloco.select("div.f_temp_horas"):
            hora, direcao = [d.get_text(strip=True) for d in h.select("div.f_temp_hora")]
            texto = h.select_one(".grafico_temp_barra_relleno").get_text(" ", strip=True)
            unidade = "".join(c for c in re.sub(r"[\d,.\s]", "", texto)).lower()
            linhas.append(
                {
                    "data": data.isoformat(),
                    "hora": f"{int(hora.split(':')[0]):02d}:{hora.split(':')[1]}",
                    "direcao": direcao,
                    "valor": _numero(texto),
                    "unidade": unidade,
                }
            )
    return linhas


def coletar_mares(ano: int, meses: list[int]) -> pd.DataFrame:
    """Junta a tábua de marés dos `meses` de `ano` num DataFrame.

    Decisão do projeto: poucos meses (3) em vez do ano inteiro, para respeitar
    o servidor e o tempo disponível. O loop é o mesmo para 1 ou 12 meses.
    """
    linhas = []
    for i, mes in enumerate(meses):
        if i > 0:
            time.sleep(PAUSA)
        html = baixar_html(URL_BASE, {"fecha": f"{ano}-{mes:02d}-01"})
        novas = parsear_mares(html, ano, mes)
        if not novas:
            raise RuntimeError(f"Nenhuma maré encontrada para {ano}-{mes:02d}")
        linhas += novas
    return pd.DataFrame(linhas)


def main(ano_completo: int = 2025, janela: bool = True) -> None:
    """Grava os CSVs em data/raw/.

    `ano_completo`: ano da tábua de marés inteira (12 meses). Precisa ser um ano
    passado: o site só serve até o mês seguinte ao atual (pedir mais cai no último
    mês disponível e o parse volta vazio).
    `janela=False` pula a coleta da janela atual (3 meses de 2026, mês corrente,
    ondas e vento): serve para coletar só o ano completo sem refazer a previsão,
    que muda a cada dia.
    """
    DIR_RAW.mkdir(parents=True, exist_ok=True)
    hoje = datetime.now()

    saidas = {}
    saidas[f"mares_{ano_completo}.csv"] = coletar_mares(ano_completo, list(range(1, 13)))

    if janela:
        meses = [m for m in range(hoje.month - 2, hoje.month + 1) if m >= 1]
        time.sleep(PAUSA)
        saidas[f"mares_{hoje.year}.csv"] = coletar_mares(hoje.year, meses)

        time.sleep(PAUSA)
        html = baixar_html(URL_BASE)
        saidas["mares_previsao.csv"] = pd.DataFrame(parsear_mares(html, hoje.year, hoje.month))

        for nome, url in [("ondas.csv", URL_ONDAS), ("vento.csv", URL_VENTO)]:
            time.sleep(PAUSA)
            saidas[nome] = pd.DataFrame(parsear_previsao(baixar_html(url), hoje.year))

    for nome, df in saidas.items():
        df.to_csv(DIR_RAW / nome, index=False)
        print(f"{nome}: {len(df)} linhas")


if __name__ == "__main__":
    main(janela="--so-ano-completo" not in sys.argv)
