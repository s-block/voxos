from voxos.attachments.telemetry import normalized_byte_bucket


def test_normalized_byte_bucket_uses_bounded_low_cardinality_labels() -> None:
    assert normalized_byte_bucket(255 * 1024) == "under_256_kib"
    assert normalized_byte_bucket(256 * 1024) == "256_kib_to_1_mib"
    assert normalized_byte_bucket(1024 * 1024) == "1_mib_to_5_mib"
    assert normalized_byte_bucket(5 * 1024 * 1024) == "5_mib_or_more"
