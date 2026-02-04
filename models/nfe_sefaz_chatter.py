# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Utilitários para registro de eventos SEFAZ no chatter e ações de UI.

Funções que recebem um documento (record) para postar mensagens e
retornar ações de janela; sem dependência de modelos próprios do módulo.
"""

import base64


def post_sefaz_event(document, event_type, cstat, xmotivo, xml_content=None, body_extra=None):
    """
    Registra evento SEFAZ no documento via chatter (message_post),
    anexando o XML da resposta quando disponível.

    :param document: l10n_br_fiscal.document
    :param event_type: str (ex: "Consulta", "Autorização", "Cancelamento")
    :param cstat: str (código de status SEFAZ)
    :param xmotivo: str (motivo da resposta)
    :param xml_content: str ou bytes, XML bruto da resposta (opcional)
    :param body_extra: str, texto adicional para o corpo da mensagem (opcional)
    """
    body = f"{event_type}\ncStat: {cstat or '-'} | xMotivo: {xmotivo or '-'}"
    if body_extra:
        body += f"\n\n{body_extra}"

    attachment_ids = []
    if xml_content:
        if isinstance(xml_content, bytes):
            xml_content = xml_content.decode("utf-8", errors="replace")
        filename = f"sefaz_{event_type.lower().replace(' ', '_')}_{document.nfe_key or document.id}.xml"
        attachment = document.env["ir.attachment"].create({
            "name": filename,
            "datas": base64.b64encode(xml_content.encode("utf-8")),
            "mimetype": "application/xml",
            "res_model": document._name,
            "res_id": document.id,
        })
        attachment_ids = [attachment.id]

    document.message_post(
        body=body,
        attachment_ids=attachment_ids,
        message_type="comment",
        subtype_xmlid="mail.mt_note",
    )


def action_reload_form(document):
    """
    Retorna ação para atualizar o formulário atual (recarregar a view),
    sem abrir nova janela. O cliente reaplica a ação atual e recarrega os dados.
    Utilizado após: Consultar NF-e, Cancelar, Carta de Correção, Emitir.
    Preferir retornar display_notification com params.next = action_reload_form(doc)
    para mostrar mensagem ao usuário antes do reload.
    """
    return {
        "type": "ir.actions.client",
        "tag": "reload",
    }
