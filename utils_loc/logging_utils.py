"""Helpers for consistent timestamped console output."""

import builtins
from datetime import datetime


_ORIGINAL_PRINT = builtins.print
_INSTALLED = False


def _timestamp_prefix():
    return "[{}]".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def _timestamped_print(*args, **kwargs):
    if args:
        prefix = _timestamp_prefix() + " "
        _ORIGINAL_PRINT(prefix, *args, sep=kwargs.get("sep", " "), end=kwargs.get("end", "\n"), file=kwargs.get("file"), flush=kwargs.get("flush", False))
        return
    _ORIGINAL_PRINT(_timestamp_prefix(), end=kwargs.get("end", "\n"), file=kwargs.get("file"), flush=kwargs.get("flush", False))


def install_timestamped_print():
    global _INSTALLED
    if _INSTALLED:
        return
    builtins.print = _timestamped_print
    _INSTALLED = True

