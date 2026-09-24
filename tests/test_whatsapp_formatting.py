from __future__ import annotations

from app.application.whatsapp import (
    WHATSAPP_MAX_MESSAGE_CHARS,
    WHATSAPP_MAX_MESSAGES,
    split_whatsapp_messages,
)


def test_split_whatsapp_messages_returns_single_short_message() -> None:
    assert split_whatsapp_messages("  Oi! Como posso ajudar?  ") == ["Oi! Como posso ajudar?"]


def test_split_whatsapp_messages_splits_long_paragraphs() -> None:
    first_paragraph = "Primeira parte com contexto suficiente. " * 8
    second_paragraph = "Segunda parte com proximos passos claros. " * 8

    messages = split_whatsapp_messages(f"{first_paragraph}\n\n{second_paragraph}")

    assert len(messages) == 2
    assert messages[0].startswith("Primeira parte")
    assert messages[1].startswith("Segunda parte")


def test_split_whatsapp_messages_splits_long_single_paragraph_by_sentence() -> None:
    paragraph = (
        ("Primeira frase com bastante contexto para a pessoa entender o que aconteceu. " * 4)
        + ("Segunda frase com uma orientacao pratica e direta para continuar. " * 4)
        + ("Terceira frase fechando com uma pergunta curta para avancar. " * 4)
    )

    messages = split_whatsapp_messages(paragraph)

    assert len(messages) > 1
    assert all(len(message) <= WHATSAPP_MAX_MESSAGE_CHARS for message in messages)


def test_split_whatsapp_messages_preserves_list_blocks_when_possible() -> None:
    text = "- primeiro passo\n- segundo passo\n- terceiro passo"

    assert split_whatsapp_messages(text) == [text]


def test_split_whatsapp_messages_strips_document_markdown_but_keeps_whatsapp_formatting() -> None:
    text = """
# Titulo interno
*Importante*: responda com _clareza_.
| Campo | Valor |
| --- | --- |
| Nome | Pessoa Exemplo |
---
- item preservado
1. passo preservado
"""

    message = split_whatsapp_messages(text)[0]

    assert "# Titulo interno" not in message
    assert message.startswith("Titulo interno")
    assert "| --- | --- |" not in message
    assert "\n---" not in message
    assert "*Importante*" in message
    assert "_clareza_" in message
    assert "- item preservado" in message
    assert "1. passo preservado" in message


def test_split_whatsapp_messages_caps_to_four_messages() -> None:
    paragraphs = [
        f"Bloco {index} " + ("com conteudo suficiente para formar mensagens separadas. " * 8)
        for index in range(1, 9)
    ]

    messages = split_whatsapp_messages("\n\n".join(paragraphs))

    assert len(messages) == WHATSAPP_MAX_MESSAGES
    assert messages[0].startswith("Bloco 1")
    assert "Bloco 8" in messages[-1]


def test_split_whatsapp_messages_returns_empty_list_for_blank_text() -> None:
    assert split_whatsapp_messages(" \n\n  ") == []
