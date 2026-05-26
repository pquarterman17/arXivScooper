"""Patent source providers.

Each provider exposes ``fetch_patent(number, *, api_key=None, http=None)``
returning a :class:`scq.patents.normalize.Patent`. The ``http`` parameter
is an injectable callable ``(url, headers) -> dict`` so the network leg
can be mocked in tests and routed through the local proxy in the browser.

PatentsView (USPTO) ships first; EPO and Google providers (Phase 3) plug
in behind the same signature.
"""

from __future__ import annotations

from . import patentsview

__all__ = ["patentsview"]
