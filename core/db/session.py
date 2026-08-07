"""The session handle the domain modules run their queries through.

``core.database`` owns the engine, the sessionmaker and ``get_session`` — it is
the facade every call site imports, and it imports the modules in this package.
Resolving it here at *call* time therefore does two things: it keeps that
one-directional import from becoming a cycle, and it keeps
``core.database.get_session`` the single seam to patch (in tests, or to swap the
transaction scope) for every DAL function at once.
"""

from contextlib import AbstractContextManager

from sqlalchemy.orm import Session


def open_session() -> AbstractContextManager[Session]:
    """Open a transactional session via ``core.database.get_session``."""
    from core.database import get_session

    return get_session()
