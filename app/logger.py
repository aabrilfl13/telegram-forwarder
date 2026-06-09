import logging


class _ColorFormatter(logging.Formatter):
    _COLORS = {
        logging.DEBUG: "\033[35m",  # magenta
        logging.INFO: "\033[32m",  # green
        logging.WARNING: "\033[33m",  # yellow
        logging.ERROR: "\033[31m",  # red
        logging.CRITICAL: "\033[38;5;214m",  # orange
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelno, "")
        record.levelname = f"{color}[{record.levelname}]{self._RESET}"
        return super().format(record)


def setup_logging(name: str) -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(
        _ColorFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)
    logging.getLogger("pyrogram").setLevel(logging.INFO)
    logging.getLogger("pyrogram.types.messages_and_media.message").setLevel(logging.ERROR)
    logging.getLogger("pyrogram.types.user_and_chats.user").setLevel(logging.ERROR)
    return logging.getLogger(name)
