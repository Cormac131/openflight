"""Allow ``python -m openflight.provisioning`` as well as the console script."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
