from voxos.integrations.providers.twilio.codec import (
    pcm16_bytes_to_ulaw_bytes,
    ulaw_bytes_to_pcm16_bytes,
)


def test_ulaw_codec_round_trips_silence_shape() -> None:
    pcm_silence = (0).to_bytes(2, byteorder="little", signed=True) * 160
    ulaw = pcm16_bytes_to_ulaw_bytes(pcm_silence)
    decoded = ulaw_bytes_to_pcm16_bytes(ulaw)

    assert len(ulaw) == 160
    assert len(decoded) == len(pcm_silence)
