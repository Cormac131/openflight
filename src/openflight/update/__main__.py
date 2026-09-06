"""Allow ``python -m openflight.update`` as an alias for ``openflight-update``."""

from .cli import main

raise SystemExit(main())
