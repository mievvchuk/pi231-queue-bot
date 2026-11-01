"""Authoring only: synthesize Ukrainian guide narration, then pad each scene.

python scripts/narrate_help_videos.py OUTPUT_DIRECTORY FFMPEG [optional-topic]
Requires edge-tts. Sends only the public instructional script to the voice service.
The bot and Render do not need edge-tts, FFmpeg, or a speech API key.
"""
import asyncio
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import edge_tts


async def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    ffmpeg = sys.argv[2]
    topic = sys.argv[3] if len(sys.argv) > 3 else None
    script = json.loads(Path(__file__).with_name('help_video_narration.json').read_text(encoding='utf-8'))
    violations = []
    for name, scenes in script['guides'].items():
        if topic and topic != name:
            continue
        inputs, filters = [], []
        for index, scene in enumerate(scenes):
            signature = hashlib.sha256((script['voice'] + script['rate'] + scene['text']).encode()).hexdigest()[:10]
            clip = output / f'{name}-{index + 1}-{signature}.mp3'
            # Keeping intermediates makes repeat recordings independent of the provider.
            if not clip.exists() or not clip.stat().st_size:
                for attempt in range(3):
                    try:
                        await edge_tts.Communicate(scene['text'], script['voice'], rate=script['rate']).save(str(clip))
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(2)
            probe = subprocess.run([ffmpeg, '-hide_banner', '-i', str(clip)], capture_output=True, text=True)
            match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', probe.stderr)
            if not match:
                raise RuntimeError(f'Cannot measure {clip}: {probe.stderr}')
            hours, minutes, seconds = map(float, match.groups())
            duration = hours * 3600 + minutes * 60 + seconds
            if duration + .8 > scene['seconds']:
                violations.append(f'{name} scene {index + 1}: voice {duration:.2f}s exceeds scene {scene["seconds"]}s; shorten text, never clip words')
            inputs += ['-i', str(clip)]
            filters.append(f'[{index}:a]aresample=48000,adelay=400:all=1,apad,atrim=duration={scene["seconds"]},asetpts=PTS-STARTPTS[a{index}]')
            print(f'{name} {index + 1}: {duration:.2f}s voice / {scene["seconds"]}s scene', flush=True)
        if any(message.startswith(name + ' scene ') for message in violations):
            continue
        filters.append(''.join(f'[a{i}]' for i in range(len(scenes))) + f'concat=n={len(scenes)}:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=7[voice]')
        subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', *inputs, '-filter_complex', ';'.join(filters), '-map', '[voice]', '-ar', '48000', '-ac', '1', str(output / f'guide-{name}.wav')], check=True)
    if violations:
        raise RuntimeError('\n'.join(violations))


if __name__ == '__main__':
    asyncio.run(main())
