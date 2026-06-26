"""Helpers for consistent timestamped console output."""

import builtins
import sys
from datetime import datetime


_ORIGINAL_PRINT_ATTR = "_codex_original_print"
_TIMESTAMPED_MARKER_ATTR = "_codex_timestamped_print"


def _fallback_print(*args, **kwargs):
    file_obj = kwargs.get("file")
    if file_obj is None:
        file_obj = sys.stdout
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")
    flush = kwargs.get("flush", False)
    text = sep.join(str(arg) for arg in args)
    file_obj.write(text + end)
    if flush:
        try:
            file_obj.flush()
        except Exception:
            pass


def _resolve_original_print():
    original = getattr(builtins, _ORIGINAL_PRINT_ATTR, None)
    if callable(original):
        return original

    current = builtins.print
    wrapped = getattr(current, _ORIGINAL_PRINT_ATTR, None)
    if callable(wrapped):
        setattr(builtins, _ORIGINAL_PRINT_ATTR, wrapped)
        return wrapped

    if (
        getattr(current, "__module__", None) == __name__
        and getattr(current, "__name__", None) == "_timestamped_print"
    ):
        setattr(builtins, _ORIGINAL_PRINT_ATTR, _fallback_print)
        return _fallback_print

    setattr(builtins, _ORIGINAL_PRINT_ATTR, current)
    return current


_ORIGINAL_PRINT = _resolve_original_print()


def _timestamp_prefix():
    return "[{}]".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def _timestamped_print(*args, **kwargs):
    if args:
        prefix = _timestamp_prefix() + " "
        _ORIGINAL_PRINT(prefix, *args, sep=kwargs.get("sep", " "), end=kwargs.get("end", "\n"), file=kwargs.get("file"), flush=kwargs.get("flush", True))
        return
    _ORIGINAL_PRINT(_timestamp_prefix(), end=kwargs.get("end", "\n"), file=kwargs.get("file"), flush=kwargs.get("flush", True))


def install_timestamped_print():
    if getattr(builtins.print, _TIMESTAMPED_MARKER_ATTR, False):
        return
    setattr(_timestamped_print, _TIMESTAMPED_MARKER_ATTR, True)
    setattr(_timestamped_print, _ORIGINAL_PRINT_ATTR, _ORIGINAL_PRINT)
    builtins.print = _timestamped_print
