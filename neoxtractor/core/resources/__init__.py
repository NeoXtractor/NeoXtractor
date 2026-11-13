"""NeoXtractor resource decoders (content layer).

Entry-point convention: a protocol module is a plain ``.py`` file at this
package root exposing ``FORMAT`` + ``probe()`` (+ ``read()``; optionally
``convert()``/``TARGETS``).  Supporting code lives in private subpackages
whose names start with an underscore; those are imported by their owning
entry file and are never discovered directly.

The file-like input contract (all probe/read functions): the argument is a
seekable binary stream exposing ``read``/``seek``/``tell`` with ``seek``
supporting ``io.SEEK_END``; a ``bytes`` argument is also accepted.  ``probe``
reads only a header; ``read`` may consume the whole stream.

Callers must not import this package directly -- go through
``neoxtractor.core.processors``.
"""
