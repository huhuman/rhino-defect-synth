"""Document-level metadata and environment setup."""


def apply_document_metadata(doc, metadata):
    """
    Apply metadata (author, project info, custom keys) to the Rhino document.

    Args:
        doc: Rhino document reference.
        metadata: mapping of keys → values to attach.
    """
    raise NotImplementedError("Wire Rhino document metadata as needed.")


def ensure_document_environment(units=None, tolerances=None, named_views=None):
    """
    Set document-wide environment defaults such as units, tolerances, and views.

    Args:
        units: desired model units.
        tolerances: dict with absolute/angle tolerance values.
        named_views: optional named view setup to register.
    """
    raise NotImplementedError("Configure document units, tolerances, and views.")

