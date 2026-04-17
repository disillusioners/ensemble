# Runtime hook to fix tiktoken namespace package in frozen environment
import sys
import os

if getattr(sys, 'frozen', False):
    import tiktoken_ext
    # Add the bundled tiktoken_ext path so pkgutil can find the plugins
    bundled_tiktoken_ext = os.path.join(sys._MEIPASS, 'tiktoken_ext')
    if os.path.isdir(bundled_tiktoken_ext):
        if bundled_tiktoken_ext not in tiktoken_ext.__path__:
            tiktoken_ext.__path__.append(bundled_tiktoken_ext)
