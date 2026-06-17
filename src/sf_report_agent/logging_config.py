import logging


def configure_logging(*, verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy_logger in ("urllib3", "simple_salesforce"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
