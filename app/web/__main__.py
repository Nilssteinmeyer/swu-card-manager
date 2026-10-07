"""Entry point: python -m app.web

gevent monkey-patching MUST happen before any other imports (especially
torch / requests / urllib) — otherwise greenlets block on native sockets.
"""
from gevent import monkey

monkey.patch_all()

from app.web.app import main

if __name__ == "__main__":
    main()
