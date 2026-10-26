import logging

from voxos.utils import logging as logging_utils


def test_configure_voxos_logging_reuses_host_root_handler() -> None:
    root = logging.Logger("test-root")
    root.addHandler(logging.NullHandler())
    namespace = logging.Logger("test-voxos")

    logging_utils._configure_voxos_logger(
        voxos_logger=namespace,
        root_logger=root,
        level=logging.INFO,
    )

    assert namespace.level == logging.INFO
    assert namespace.handlers == []
    assert namespace.propagate is True


def test_configure_voxos_logging_adds_one_namespace_handler_without_root() -> None:
    root = logging.Logger("test-root")
    namespace = logging.Logger("test-voxos")

    logging_utils._configure_voxos_logger(
        voxos_logger=namespace,
        root_logger=root,
        level=logging.INFO,
    )
    logging_utils._configure_voxos_logger(
        voxos_logger=namespace,
        root_logger=root,
        level=logging.INFO,
    )

    assert namespace.level == logging.INFO
    assert len(namespace.handlers) == 1
    assert namespace.handlers[0].get_name() == logging_utils.VOXOS_CONSOLE_HANDLER_NAME
    assert namespace.handlers[0].level == logging.INFO
    assert namespace.propagate is False
