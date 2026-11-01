"""Check shipped media without requiring FFmpeg or contacting a speech service."""
import json
import re
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'queue_bot' / 'mini_app' / 'static'
SCRIPT = json.loads((ROOT / 'scripts' / 'help_video_narration.json').read_text(encoding='utf-8'))


def boxes(data):
    offset = 0
    while offset < len(data):
        size, kind = struct.unpack_from('>I4s', data, offset)
        header = 8
        if size == 1:
            size = struct.unpack_from('>Q', data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = len(data) - offset
        assert size >= header and offset + size <= len(data), 'Truncated MP4 box'
        yield kind, data[offset + header:offset + size]
        offset += size


@pytest.mark.parametrize('topic', SCRIPT['guides'])
def test_narrated_guides_have_audio_complete_duration_and_synchronized_captions(topic):
    scenes = SCRIPT['guides'][topic]
    assert sum(scene['seconds'] for scene in scenes) == 40
    movie = list(boxes((STATIC / f'guide-{topic}.mp4').read_bytes()))
    kinds = [kind for kind, _ in movie]
    assert kinds.index(b'moov') < kinds.index(b'mdat'), 'Fast-start metadata must precede video'
    metadata = list(boxes(dict(movie)[b'moov']))
    header = dict(metadata)[b'mvhd']
    # Both versions of the ISO BMFF movie header are supported.
    offset = 20 if header[0] == 1 else 12
    scale = struct.unpack_from('>I', header, offset)[0]
    duration = struct.unpack_from('>Q' if header[0] == 1 else '>I', header, offset + 4)[0] / scale
    assert 39.9 <= duration <= 40.1
    handlers = set()
    for kind, track in metadata:
        if kind == b'trak':
            media = dict(boxes(dict(boxes(track))[b'mdia']))
            handlers.add(media[b'hdlr'][8:12])
            if media[b'hdlr'][8:12] == b'soun':
                sample_table = dict(boxes(dict(boxes(media[b'minf']))[b'stbl']))
                assert b'mp4a' in sample_table[b'stsd'], 'Voice must use browser-compatible AAC'
    assert handlers == {b'vide', b'soun'}, 'Silent exports must not replace narrated guides'
    captions = (STATIC / f'guide-{topic}.vtt').read_text(encoding='utf-8')
    assert captions.startswith('WEBVTT')
    assert len(re.findall(r' --> ', captions)) == len(scenes)
    assert '00:00:40.000' in captions
    for scene in scenes:
        assert scene['text'] in captions, 'Captions must match the spoken narration'
