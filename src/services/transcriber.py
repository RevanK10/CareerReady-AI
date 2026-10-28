import io

import speech_recognition as sr


class TranscriptionError(Exception):
    """Raised when recorded audio can't be turned into text."""


def transcribe_audio(wav_bytes: bytes) -> str:
    """Transcribe a WAV recording (as produced by st.audio_input) to text."""
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(io.BytesIO(wav_bytes)) as source:
            audio = recognizer.record(source)
        return recognizer.recognize_google(audio).strip()
    except sr.UnknownValueError as e:
        raise TranscriptionError("Couldn't make out any speech. Try again, or type your answer.") from e
    except sr.RequestError as e:
        raise TranscriptionError("The speech service is unavailable. Type your answer instead.") from e
    except (ValueError, EOFError) as e:
        raise TranscriptionError("That recording couldn't be read. Try recording again.") from e
