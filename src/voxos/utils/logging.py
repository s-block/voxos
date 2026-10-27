import logging

VOXOS_LOGGER_NAME = "voxos"
VOXOS_CONSOLE_HANDLER_NAME = "voxos-console"
VOXOS_LOG_FORMAT = "%(levelname)s:%(name)s:%(message)s"


def configure_voxos_logging(level: int = logging.INFO) -> None:
    _configure_voxos_logger(
        voxos_logger=logging.getLogger(VOXOS_LOGGER_NAME),
        root_logger=logging.getLogger(),
        level=level,
    )


def _configure_voxos_logger(
    *,
    voxos_logger: logging.Logger,
    root_logger: logging.Logger,
    level: int,
) -> None:
    voxos_logger.setLevel(level)
    managed_handlers = tuple(
        handler for handler in voxos_logger.handlers if handler.get_name() == VOXOS_CONSOLE_HANDLER_NAME
    )
    if root_logger.handlers:
        for handler in managed_handlers:
            voxos_logger.removeHandler(handler)
            handler.close()
        voxos_logger.propagate = True
        return
    if managed_handlers:
        for handler in managed_handlers:
            handler.setLevel(level)
        voxos_logger.propagate = False
        return
    handler = logging.StreamHandler()
    handler.set_name(VOXOS_CONSOLE_HANDLER_NAME)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(VOXOS_LOG_FORMAT))
    voxos_logger.addHandler(handler)
    voxos_logger.propagate = False
