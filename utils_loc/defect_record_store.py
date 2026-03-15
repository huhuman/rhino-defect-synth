"""Persist defect placement payloads in Rhino document metadata."""

import json

import rhinoscriptsyntax as rs
import scriptcontext as sc


_DOC_SECTION = "codex_defect_records"
_DOC_ENTRY = "payload_json"
_DOC_KEY = "codex::defect_records::payload_json"


def _set_document_text(value):
    text = str(value or "")
    if not text:
        return False

    try:
        rs.SetDocumentData(_DOC_SECTION, _DOC_ENTRY, text)
        return True
    except Exception:
        pass

    strings = getattr(sc.doc, "Strings", None)
    if strings is None:
        return False

    for setter in (
        lambda: strings.SetString(_DOC_KEY, text),
        lambda: strings.SetString(_DOC_SECTION, _DOC_ENTRY, text),
    ):
        try:
            result = setter()
        except Exception:
            continue
        if result is not False:
            return True
    return False


def _get_document_text():
    try:
        value = rs.GetDocumentData(_DOC_SECTION, _DOC_ENTRY)
        if value:
            return value
    except Exception:
        pass

    strings = getattr(sc.doc, "Strings", None)
    if strings is None:
        return None

    for getter in (
        lambda: strings.GetValue(_DOC_KEY),
        lambda: strings.GetValue(_DOC_SECTION, _DOC_ENTRY),
    ):
        try:
            value = getter()
        except Exception:
            continue
        if value:
            return value
    return None


def store_defect_record_payload(payload):
    if not isinstance(payload, dict) or not payload:
        return False
    try:
        text = json.dumps(payload, sort_keys=True)
    except Exception:
        return False
    return _set_document_text(text)


def load_defect_record_payload_from_document():
    text = _get_document_text()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None
