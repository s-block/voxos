ULAW_BIAS = 0x84
ULAW_CLIP = 32635
INT16_MIN = -32768
INT16_MAX = 32767


def ulaw_bytes_to_pcm16_bytes(payload: bytes) -> bytes:
    samples = bytearray(len(payload) * 2)
    offset = 0
    for value in payload:
        sample = ulaw_byte_to_pcm16(value)
        samples[offset : offset + 2] = sample.to_bytes(
            2,
            byteorder="little",
            signed=True,
        )
        offset += 2
    return bytes(samples)


def pcm16_bytes_to_ulaw_bytes(payload: bytes) -> bytes:
    encoded = bytearray(len(payload) // 2)
    for index in range(0, len(payload) - 1, 2):
        sample = int.from_bytes(
            payload[index : index + 2],
            byteorder="little",
            signed=True,
        )
        encoded[index // 2] = pcm16_to_ulaw_byte(sample)
    return bytes(encoded)


def ulaw_byte_to_pcm16(value: int) -> int:
    inverted = (~value) & 0xFF
    sign = inverted & 0x80
    exponent = (inverted >> 4) & 0x07
    mantissa = inverted & 0x0F
    sample = ((mantissa << 3) + ULAW_BIAS) << exponent
    sample -= ULAW_BIAS
    if sign:
        sample = -sample
    return clamp_int16(sample)


def pcm16_to_ulaw_byte(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    magnitude = -sample if sample < 0 else sample
    magnitude = min(magnitude, ULAW_CLIP) + ULAW_BIAS

    exponent = 7
    exponent_mask = 0x4000
    while exponent > 0 and not magnitude & exponent_mask:
        exponent -= 1
        exponent_mask >>= 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def clamp_int16(value: int) -> int:
    if value < INT16_MIN:
        return INT16_MIN
    if value > INT16_MAX:
        return INT16_MAX
    return value
