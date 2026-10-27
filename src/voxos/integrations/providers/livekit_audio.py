from livekit import rtc


def phone_audio_track_publish_options() -> rtc.TrackPublishOptions:
    return rtc.TrackPublishOptions(
        source=rtc.TrackSource.SOURCE_MICROPHONE,
    )
