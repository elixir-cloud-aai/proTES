import logging
import os

from foca.foca import foca

logger = logging.getLogger(__name__)


def main():
    app = foca(
        os.path.join(
            os.path.dirname(__file__),
            "config.yaml",
        )
    )
    app.run(port=app.port)


if __name__ == '__main__':
    main()