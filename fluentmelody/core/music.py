"""Bounded MIDI/NBS import, playable mono voices and portable NBS export.

No AI response is executed. Musical scores pass the same strict validator as
local imports before they can reach the keyboard player.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import struct
import wave
from array import array

from .nbs_engine import Song, Layer, Note, read_nbs, _smooth_octaves, _sparse_indices
from .melody_selection import select_melody, project_timing

MAX_FILE = 32_000_000
MAX_NOTES = 50_000
MAX_DURATION = 1800
MAX_PLAYERS = 8
MIN_HOLD = .08
MODIFIERS = {2: 'left', 8: 'right', 32: 'middle'}
STEPS = (0, 2, 4, 5, 7, 9, 11, 12)
META_PREFIX = 'FluentMelody/1\n'


@dataclass
class TimedNote:
    start: float
    duration: float
    midi: int
    layer: int
    velocity: int = 100
    gate_duration: float | None = None


def _finite(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label}必须是数字。')
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{label}需在 {minimum}～{maximum} 之间。')
    return value


def _integer(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{label}需为 {minimum}～{maximum} 的整数。')
    return value


def _label(value, default='歌曲', limit=160):
    if not isinstance(value, str):
        raise ValueError('名称必须是文字。')
    return ''.join(c for c in value if c.isprintable())[:limit] or default


def _midi_read(path):
    """SMF type 0/1, tempo changes, running status, sustain and SMPTE timing."""
    data = Path(path).read_bytes()
    if len(data) < 14 or data[:4] != b'MThd':
        raise ValueError('这不是有效的 MIDI 文件。')
    header_length = struct.unpack_from('>I', data, 4)[0]
    if header_length < 6 or 8 + header_length > len(data):
        raise ValueError('MIDI 文件头不完整。')
    kind, count, division = struct.unpack_from('>HHH', data, 8)
    if kind not in (0, 1) or not 1 <= count <= 512 or not division:
        raise ValueError('支持 MIDI 格式 0/1，最多 512 条轨道。格式 2 请先合并为格式 1。')
    tempos = [(0, 500_000)]
    events, names, programs = [], {}, {}
    position = 8 + header_length
    total_events = 0
    for track in range(count):
        if position + 8 > len(data) or data[position:position + 4] != b'MTrk':
            raise ValueError('MIDI 音轨数据缺失。')
        length = struct.unpack_from('>I', data, position + 4)[0]
        position += 8
        end = position + length
        if end > len(data):
            raise ValueError('MIDI 音轨被截断。')
        tick, running = 0, None

        def variable():
            nonlocal position
            value = 0
            for _ in range(4):
                if position >= end:
                    raise ValueError('MIDI 可变长度字段被截断。')
                byte = data[position]
                position += 1
                value = (value << 7) | (byte & 127)
                if byte < 128:
                    return value
            raise ValueError('MIDI 可变长度字段超出规范。')

        while position < end:
            tick += variable()
            if tick > 2 ** 40 or position >= end:
                raise ValueError('MIDI 时间或事件异常。')
            status = data[position]
            if status & 128:
                position += 1
                if status < 0xf0:
                    running = status
            elif running is not None:
                status = running
            else:
                raise ValueError('MIDI 连续事件缺少状态字节。')
            total_events += 1
            if total_events > 1_000_000:
                raise ValueError('MIDI 事件过多，请先精简歌曲。')
            if status == 0xff:
                if position >= end:
                    raise ValueError('MIDI 元事件被截断。')
                meta = data[position]
                position += 1
                size = variable()
                if position + size > end:
                    raise ValueError('MIDI 元事件长度异常。')
                payload = data[position:position + size]
                position += size
                if meta == 0x51 and len(payload) == 3:
                    tempo = int.from_bytes(payload, 'big')
                    if not tempo:
                        raise ValueError('MIDI 速度不能为零。')
                    tempos.append((tick, tempo))
                elif meta == 3:
                    for encoding in ('utf-8', 'gb18030', 'latin1'):
                        try:
                            names[track] = payload.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            pass
                elif meta == 0x2f:
                    break
            elif status in (0xf0, 0xf7):
                size = variable()
                if position + size > end:
                    raise ValueError('MIDI 系统消息长度异常。')
                position += size
                running = None
            elif status < 0xf0:
                command, channel = status >> 4, status & 15
                size = 1 if command in (12, 13) else 2
                if position + size > end:
                    raise ValueError('MIDI 音符事件被截断。')
                params = data[position:position + size]
                if any(v > 127 for v in params):
                    raise ValueError('MIDI 数据字节超出范围。')
                position += size
                if command in (8, 9, 11):
                    events.append((tick, track, channel, command, params[0], params[1]))
                elif command == 12:
                    programs[track, channel] = params[0]
            else:
                raise ValueError('MIDI 含不支持的系统事件。')
        events.append((tick, track, -1, -1, 0, 0))
        position = end

    if division & 0x8000:
        fps = 256 - (division >> 8)
        ticks_per_frame = division & 255
        if fps not in (24, 25, 29, 30) or not ticks_per_frame:
            raise ValueError('MIDI SMPTE 时基无效。')
        rate = (29.97 if fps == 29 else fps) * ticks_per_frame
        seconds = lambda tick: tick / rate
    else:
        dedup = {}
        for tick, tempo in tempos:
            dedup[tick] = tempo
        points = sorted(dedup)
        offsets = [0.0]
        for a, b in zip(points, points[1:]):
            offsets.append(offsets[-1] + (b - a) * dedup[a] / division / 1e6)

        def seconds(tick):
            index = bisect_right(points, tick) - 1
            return offsets[index] + (tick - points[index]) * dedup[points[index]] / division / 1e6

    active = defaultdict(deque)
    sustained = defaultdict(list)
    pedals = defaultdict(bool)
    raw, drum_count = [], 0

    def finish(key, start, velocity, end_tick, release_tick=None):
        nonlocal drum_count
        track, channel, pitch = key
        if channel == 9:
            drum_count += 1
            return
        start_seconds = seconds(start)
        duration = max(.02, seconds(end_tick) - start_seconds)
        if start_seconds + duration > MAX_DURATION:
            raise ValueError('歌曲超过 30 分钟，请先截取需要的片段。')
        gate = max(.02,seconds(end_tick if release_tick is None else release_tick)-start_seconds)
        raw.append((start_seconds, duration, pitch, (track, channel), velocity,gate))
        if len(raw) > MAX_NOTES:
            raise ValueError('歌曲超过 5 万个音符，请先精简。')

    for tick, track, channel, command, a, b in sorted(events, key=lambda e: e[0]):
        key, chan = (track, channel, a), (track, channel)
        if command == 9 and b:
            active[key].append((tick, b))
        elif command == 8 or (command == 9 and not b):
            if active[key]:
                start, velocity = active[key].popleft()
                if pedals[chan]:
                    sustained[chan].append((key, start, velocity,tick))
                else:
                    finish(key, start, velocity, tick)
        elif command == 11 and a in (64, 120, 123):
            down = a == 64 and b >= 64
            pedals[chan] = down
            if not down:
                for pending_key, start, velocity,release_tick in sustained.pop(chan, []):
                    finish(pending_key, start, velocity, tick,release_tick)
            if a in (120, 123):
                for pending_key in list(active):
                    if pending_key[:2] == chan:
                        for start, velocity in active.pop(pending_key):
                            finish(pending_key, start, velocity, tick)
        elif command == -1:
            for pending_key in list(active):
                if pending_key[0] == track:
                    for start, velocity in active.pop(pending_key):
                        finish(pending_key, start, velocity, tick)
            for pending_chan in list(sustained):
                if pending_chan[0] == track:
                    for pending_key, start, velocity,release_tick in sustained.pop(pending_chan):
                        finish(pending_key, start, velocity, tick,release_tick)
    if not raw:
        raise ValueError('MIDI 没有可演奏的音符；打击乐通道已过滤。')
    layer_keys = sorted({entry[3] for entry in raw})
    layer_map = {key: i for i, key in enumerate(layer_keys)}
    layers = []
    for track, channel in layer_keys:
        label = names.get(track, f'MIDI 音轨 {track + 1}')
        program = programs.get((track, channel), 0)
        if 64 <= program <= 71:
            label += ' · 萨克斯'
        elif 72 <= program <= 79:
            label += ' · 长笛'
        layers.append(Layer(_label(f'{label} / 通道 {channel + 1}')))
    timed = [TimedNote(s, d, p, layer_map[k], max(1, round(v / 127 * 100)),gate)
             for s, d, p, k, v,gate in sorted(raw)]
    tempo = 1000.0
    notes = [Note(round(n.start * tempo), n.layer, 0, n.midi - 21, n.velocity) for n in timed]
    warnings = [f'已保留 MIDI 速度变化及延音踏板；过滤 {drum_count} 个打击乐音符。']
    song = Song(Path(path).stem, 5, tempo,
                math.ceil(max(n.start + n.duration for n in timed) * tempo),
                layers, notes, 16, [], warnings)
    song.timed_notes = timed
    song.source_format = 'midi'
    if not division & 0x8000:
        last_tick = max(e[0] for e in events)
        dominant = max(enumerate(points),key=lambda item:
                      (points[item[0]+1] if item[0]+1<len(points) else last_tick)-item[1])[1]
        song.seconds_per_beat = dedup[dominant]/1e6
    return song


def read_music(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE:
        raise ValueError('文件超过 32 MB，请先精简。')
    if path.suffix.lower() in ('.mid', '.midi'):
        return _midi_read(path)
    if path.suffix.lower() != '.nbs':
        raise ValueError('请选择 .nbs、.mid 或 .midi 文件。')
    song = read_nbs(path)
    if len(song.notes) > MAX_NOTES or song.length / song.tempo > MAX_DURATION:
        raise ValueError('歌曲最多 5 万音符、30 分钟。')
    song.source_format = 'nbs'
    if song.description.startswith(META_PREFIX):
        try:
            metadata = json.loads(song.description[len(META_PREFIX):])
            durations = metadata['durations']
            if len(durations) != len(song.layers):
                raise ValueError('音轨不匹配')
            timed = []
            groups = defaultdict(list)
            for note in song.notes:
                groups[note.layer].append(note)
            for layer in range(len(song.layers)):
                notes = sorted(groups[layer], key=lambda n: n.tick)
                if len(notes) != len(durations[layer]):
                    raise ValueError('音符不匹配')
                for note, duration in zip(notes, durations[layer]):
                    duration = _finite(duration, '持续时长', .01, MAX_DURATION)
                    timed.append(TimedNote(note.tick / song.tempo, duration, note.key + 21, layer, note.velocity))
            song.timed_notes = sorted(timed, key=lambda n: (n.start, n.layer))
            song.arranged = True
        except (KeyError, TypeError, ValueError, IndexError):
            song.warnings.append('演奏时长扩展数据无效，按普通 NBS 读取。')
    return song


def _source_notes(song, selected=None):
    if selected is not None:
        if not isinstance(selected, (list, set, tuple)) or not selected:
            raise ValueError('请至少选择一条音轨。')
        for index in selected:
            _integer(index, '音轨编号', 0, len(song.layers) - 1)
        selected = set(selected)
    if hasattr(song, 'timed_notes'):
        result = [TimedNote(n.start, n.duration, n.midi, n.layer, n.velocity,getattr(n,'gate_duration',None))
                  for n in song.timed_notes if (selected is None or n.layer in selected)
                  and n.velocity and song.layers[n.layer].volume]
    else:
        by_layer = defaultdict(list)
        for note in song.notes:
            if selected is not None and note.layer not in selected:
                continue
            if not note.velocity or not song.layers[note.layer].volume:
                continue
            if note.instrument < song.vanilla and note.instrument in (2, 3, 4):
                continue
            by_layer[note.layer].append(note)
        result = []
        for layer, notes in by_layer.items():
            notes.sort(key=lambda n: n.tick)
            ticks = sorted({n.tick for n in notes})
            next_tick = {a: b for a, b in zip(ticks, ticks[1:])}
            for note in notes:
                pitch = note.key + 21 + note.pitch / 100
                custom_index = note.instrument - song.vanilla
                if 0 <= custom_index < len(song.custom_keys):
                    pitch += song.custom_keys[custom_index] - 45
                duration = min(.8, max(.08, (next_tick.get(note.tick, note.tick + song.tempo * .4) - note.tick) / song.tempo - .1))
                result.append(TimedNote(note.tick / song.tempo, duration, math.floor(pitch + .5), layer, note.velocity))
    if not result:
        raise ValueError('所选音轨没有可演奏音符。')
    if len(result) > MAX_NOTES:
        raise ValueError('音符超过 5 万个。')
    return sorted(result, key=lambda n: (n.start, -n.midi, n.layer))


def _voices_to_plan(voices, names, song_name, gap, warnings, summary):
    tracks = []
    for index, voice in enumerate(voices):
        voice.sort(key=lambda n: (n.start, -n.midi))
        events = []
        if voice:
            pitches, mapping, _ = _smooth_octaves([n.midi for n in voice], 60, True)
            for i, (note, pitch) in enumerate(zip(voice, pitches)):
                hold = min(15.0, max(.01, note.duration))
                if note.gate_duration is not None and hold>max(3.,note.gate_duration*8):
                    hold = max(note.gate_duration,min(1.2,hold))
                if i + 1 < len(voice):
                    hold = min(hold, voice[i + 1].start - note.start - gap - .015)
                if hold < .01:
                    raise ValueError('音符分配产生了无法演奏的间隔。')
                key, modifiers = mapping[pitch]
                events.append({'start': round(note.start, 6), 'duration': round(hold, 6),
                               'key_index': key, 'modifiers': [MODIFIERS[m] for m in modifiers],
                               'midi': pitch, 'layer': note.layer})
        tracks.append({'name': _label(names[index], f'演奏者 {index + 1}'), 'events': events})
    duration = max((e['start'] + e['duration'] + gap for t in tracks for e in t['events']), default=0)
    empty = sum(not track['events'] for track in tracks)
    if empty:
        warnings = [*warnings, f'原曲可分配的独立声部有限，{empty} 个声部暂无音符；可减少人数或使用 AI 补写伴奏。']
    plan = {'name': _label(song_name), 'duration': round(duration, 6), 'summary': summary,
            'warnings': warnings, 'tracks': tracks, 'gap': gap}
    return validate_arrangement(plan)


def arrange(song, players=1, selected=None, speed=1.0, gap=.1, *, use_ai=True):
    """Arrange playable voices, optionally using the local melody model.

    Disabling AI leaves the musical rules and instrument constraints active.
    Existing exported arrangements keep their timing without model inference.
    """
    if not isinstance(use_ai, bool):
        raise ValueError('AI 自动改编开关必须是开启或关闭。')
    players = _integer(players, '演奏人数', 1, MAX_PLAYERS)
    speed = _finite(speed, '速度', .1, 3)
    gap = _finite(gap, '松键间隔', .1, 2)
    notes = _source_notes(song, selected)
    for note in notes:
        note.start /= speed
        note.duration /= speed
        if note.gate_duration is not None:
            note.gate_duration /= speed
    if max(n.start + n.duration for n in notes) > MAX_DURATION:
        raise ValueError('减速后的歌曲超过 30 分钟，请截取片段。')
    warnings = list(song.warnings)
    spacing = gap + MIN_HOLD + .02
    voices = [[] for _ in range(players)]
    names = ['主旋律'] + ['伴奏 / 低声部'] + [f'和声 {i}' for i in range(1, players - 1)]
    if getattr(song, 'arranged', False) and len(song.layers) == players and selected is None:
        for n in notes:
            voices[n.layer].append(n)
        if all(all(b.start - a.start >= a.duration + gap - 1e-6
                   for a, b in zip(v, v[1:])) for v in voices):
            return _voices_to_plan(voices, [l.name for l in song.layers], song.name, gap, warnings,
                                   '读取已有演奏编排，保留音轨、节奏及持续时长。')
        voices = [[] for _ in range(players)]

    probabilities = None
    model_note = 'AI 自动改编已关闭，使用普通音轨与乐句规则转换。'
    if use_ai:
        try:
            from .melody_model import predict_melody
            beat = max(.125,min(4.,getattr(song,'seconds_per_beat',.5)/speed))
            probabilities = predict_melody(notes,seconds_per_beat=beat)
            model_note = '已使用本地旋律识别模型；歌曲不会上传。'
        except (ImportError,OSError,ValueError,RuntimeError,MemoryError) as exc:
            model_note = f'本地模型暂不可用，已使用音轨与乐句分析：{str(exc)[:120]}'
    selection = select_melody(notes,[layer.name for layer in song.layers],probabilities,prefer_named=selected is None)
    melody,weights = selection.notes,selection.weights
    # Resolve dense alternatives against a musical grid, not just whichever
    # note happens to win a nearly tied salience score (which can shift a whole
    # phrase onto the offbeat). NBS convention uses four ticks per quarter note.
    beat_duration = getattr(song,'seconds_per_beat',4/song.tempo)/speed
    if .125<=beat_duration<=4 and melody:
        origin = notes[0].start
        for i,note in enumerate(melody):
            phase = (note.start-origin)/beat_duration
            if abs(phase-round(phase))*beat_duration<.025:
                weights[i] += .65
            elif abs(phase*2-round(phase*2))*beat_duration/2<.025:
                weights[i] += .35
    groups = []
    for note in notes:
        if groups and note.start - groups[-1][0].start <= .012:
            groups[-1].append(note)
        else:
            groups.append([note])
    factor = 1.0
    timing_adjustment = 0.0
    if players == 1:
        options = []
        for option in (1, .94, .9, .85):
            starts = [n.start/option for n in melody]
            for threshold in (0,spacing-.04,spacing):
                if threshold==0:
                    kept,score = list(range(len(melody))),sum(weights)
                else:
                    kept,score = _sparse_indices(starts,weights,threshold)
                adjusted = project_timing([starts[i] for i in kept],spacing)
                correction = max((abs(t-starts[i]) for i,t in zip(kept,adjusted)),default=0)
                if correction<=.050001:
                    utility = score/max(1,sum(weights))-.8*(1-option)-correction*.08
                    options.append((utility,option,kept,adjusted,correction))
                    break
        complete = [option for option in options if len(option[2])==len(melody)]
        _,factor,kept,adjusted,timing_adjustment = max(complete,key=lambda p:p[1]) if complete else max(options,key=lambda p:p[0])
        for index,start in zip(kept,adjusted):
            n = melody[index]
            voices[0].append(TimedNote(start,n.duration/factor,n.midi,n.layer,n.velocity,
                                       n.gate_duration/factor if n.gate_duration is not None else None))
    else:
        kept, _ = _sparse_indices([n.start for n in melody], weights, spacing)
        chosen = {id(melody[i]) for i in kept}
        voices[0] = [melody[i] for i in kept]
        for group in groups:
            candidates = [n for n in group if id(n) not in chosen]
            # Musical bass first, then the remaining chord tones from high to low.
            candidates.sort(key=lambda n: n.midi)
            ordered = candidates[:1] + list(reversed(candidates[1:]))
            seen = set()
            for note in ordered:
                if note.midi in seen:
                    continue
                seen.add(note.midi)
                available = [i for i in range(1, players)
                             if not voices[i] or note.start - voices[i][-1].start >= spacing - 1e-8]
                if not available:
                    continue
                target = min(available, key=lambda i: (
                    (abs(note.midi - voices[i][-1].midi) + (0 if note.layer == voices[i][-1].layer else 2))
                    if voices[i] else (0 if i == 1 else 3 + i)))
                voices[target].append(note)
    count = sum(len(v) for v in voices)
    if not count:
        raise ValueError('没有可演奏的音符。')
    warnings.append(model_note)
    warnings.append('按乐句保留旋律与休止；无人声的前奏、间奏和尾奏会选择器乐旋律。')
    if timing_adjustment>.001:
        warnings.append(f'为保留密集旋律，局部起音最多微调 {timing_adjustment*1000:.0f} 毫秒，整曲不累积延迟。')
    if factor < 1:
        warnings.append(f'为保留旋律，整曲额外减速 {(1 - factor) * 100:.0f}%。')
    if getattr(song, 'source_format', '') == 'nbs' and not getattr(song, 'arranged', False):
        warnings.append('普通 NBS 没有按键持续时长，已按相邻音符及休止推定。')
    summary = f'{players} 人编排，共 {count} 个音符；删减 {len(notes) - count} 个重叠或过密音；实际速度 {speed * factor:.2f} 倍。'
    return _voices_to_plan(voices, names[:players], song.name, gap, warnings, summary)


def validate_arrangement(plan, players=None):
    if not isinstance(plan, dict):
        raise ValueError('编排必须是对象。')
    tracks = plan.get('tracks')
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= MAX_PLAYERS:
        raise ValueError('编排需要 1～8 条演奏声部。')
    if players is not None and len(tracks) != _integer(players, '人数', 1, MAX_PLAYERS):
        raise ValueError('编排声部数量与演奏人数不符。')
    gap = _finite(plan.get('gap', .1), '松键间隔', .1, 2)
    normalized, total, end = [], 0, 0.0
    for index, track in enumerate(tracks):
        if not isinstance(track, dict) or not isinstance(track.get('events'), list):
            raise ValueError('声部必须包含音符列表。')
        events, previous_end = [], -gap
        for event in track['events']:
            total += 1
            if total > MAX_NOTES:
                raise ValueError('编排超过 5 万音符。')
            if not isinstance(event, dict):
                raise ValueError('音符格式错误。')
            start = _finite(event.get('start'), '开始时间', 0, MAX_DURATION)
            duration = _finite(event.get('duration'), '持续时长', .01, 15)
            key = _integer(event.get('key_index'), '琴键编号', 0, 7)
            pitch = _integer(event.get('midi'), '音高', 0, 127)
            layer = _integer(event.get('layer', index), '来源音轨', 0, 10000)
            modifiers = event.get('modifiers', [])
            if not isinstance(modifiers, (tuple, list)) or len(modifiers) > 2:
                raise ValueError('鼠标修饰键格式错误。')
            try:
                modifiers = [MODIFIERS.get(m, m) for m in modifiers]
                if len(set(modifiers)) != len(modifiers) or any(m not in ('left', 'right', 'middle') for m in modifiers):
                    raise ValueError('未知或重复的鼠标修饰键。')
            except TypeError:
                raise ValueError('鼠标修饰键格式错误。') from None
            if 'left' in modifiers and 'right' in modifiers:
                raise ValueError('不能同时按左右鼠标键。')
            expected = 60 + STEPS[key] + (-12 if 'left' in modifiers else 12 if 'right' in modifiers else 0) + (1 if 'middle' in modifiers else 0)
            if expected != pitch:
                raise ValueError('音高与琴键/鼠标修饰键不一致。')
            if start < previous_end + gap - 1e-6:
                raise ValueError(f'第 {index + 1} 声部有重叠音符，或松键间隔不足 {gap:.2f} 秒。')
            previous_end = start + duration
            end = max(end, previous_end + gap)
            if end > MAX_DURATION:
                raise ValueError('编排超过 30 分钟。')
            events.append({'start': round(start, 6), 'duration': round(duration, 6), 'key_index': key,
                           'modifiers': modifiers, 'midi': pitch, 'layer': layer})
        normalized.append({'name': _label(track.get('name', f'演奏者 {index + 1}')), 'events': events})
    if not total:
        raise ValueError('编排没有音符。')
    duration = _finite(plan.get('duration', end), '歌曲总时长', end - 1e-6, MAX_DURATION)
    warnings = plan.get('warnings', [])
    if not isinstance(warnings, list) or len(warnings) > 100 or any(not isinstance(w, str) for w in warnings):
        raise ValueError('编排提示格式错误。')
    return {'name': _label(plan.get('name', '歌曲')), 'duration': round(max(end, duration), 6),
            'summary': _label(plan.get('summary', ''), '编排已就绪', 1000),
            'warnings': [_label(w, '提示', 1000) for w in warnings], 'tracks': normalized, 'gap': gap}


def score_to_arrangement(score, players=None):
    """AI only needs seconds, duration and MIDI pitch, never native key codes."""
    if not isinstance(score, dict) or not isinstance(score.get('tracks'), list):
        raise ValueError('AI 编曲缺少 tracks 音轨列表。')
    tracks = score['tracks']
    _integer(len(tracks), 'AI 音轨数量', 1, MAX_PLAYERS)
    if players is not None and players != len(tracks):
        raise ValueError('AI 返回的声部数量与请求人数不一致。')
    voices, names, count, dropped = [], [], 0, 0
    for index, track in enumerate(tracks):
        if not isinstance(track, dict) or not isinstance(track.get('notes'), list):
            raise ValueError('AI 声部缺少 notes。')
        voice = []
        for note in track['notes']:
            count += 1
            if count > MAX_NOTES or not isinstance(note, dict):
                raise ValueError('AI 音符数量或格式异常。')
            voice.append(TimedNote(_finite(note.get('start'), 'AI 开始时间', 0, MAX_DURATION),
                                   _finite(note.get('duration'), 'AI 持续时长', .01, 15),
                                   _integer(note.get('midi'), 'AI 音高', 0, 127), index))
        voice.sort(key=lambda n: (n.start, -n.midi))
        indices, _ = _sparse_indices([n.start for n in voice], [1 + min(n.duration, 2) for n in voice], .2)
        dropped += len(voice) - len(indices)
        voices.append([voice[i] for i in indices])
        names.append(track.get('name', f'演奏者 {index + 1}'))
    return _voices_to_plan(voices, names, score.get('name', 'AI 编曲'), .1,
                          [f'已校验 AI 乐谱并删减 {dropped} 个过密或重叠音符。'], 'AI 编曲已转换为可演奏声部。')


def source_score(song):
    """Portable, data-only source notes for arranging providers."""
    tracks = [{'name': _label(layer.name), 'layer': index, 'notes': []} for index, layer in enumerate(song.layers)]
    duration = 0
    for note in _source_notes(song):
        duration = max(duration, note.start + note.duration)
        tracks[note.layer]['notes'].append({'start': round(note.start, 6),
                                          'duration': round(note.duration, 6), 'midi': note.midi})
    return {'name': _label(song.name), 'duration': round(duration, 6), 'tracks': tracks}


def source_notes(song):
    """Return fresh timed-note data, including original source layer ids."""
    return _source_notes(song)


def write_arrangement(plan, path):
    plan = validate_arrangement(plan)
    tempo = min(100, max(1, int(65533 / max(1, plan['duration']))))
    notes, durations = [], []
    for layer, track in enumerate(plan['tracks']):
        track_durations = []
        starts = [round(e['start'] * tempo) for e in track['events']]
        for i, event in enumerate(track['events']):
            duration = event['duration']
            if i + 1 < len(starts):
                duration = min(duration, (starts[i + 1] - starts[i]) / tempo - plan['gap'] - .002)
            if duration < .01:
                raise ValueError('NBS 时间精度不足以表示这个编排。')
            track_durations.append(round(duration, 6))
            notes.append((starts[i], layer, event['midi'] - 21))
        durations.append(track_durations)
    metadata = META_PREFIX + json.dumps({'durations': durations}, ensure_ascii=False, separators=(',', ':'))
    output = bytearray()

    def number(fmt, value):
        output.extend(struct.pack('<' + fmt, value))

    def string(value):
        encoded = value.encode('utf-8')
        number('i', len(encoded))
        output.extend(encoded)

    number('H', 0)
    output.extend(bytes((5, 16)))
    number('H', math.ceil(plan['duration'] * tempo))
    number('H', len(plan['tracks']))
    for value in (plan['name'], 'FluentMelody', '', metadata):
        string(value)
    number('H', tempo * 100)
    output.extend(bytes((0, 10, 4)))
    output.extend(bytes(20))
    string('')
    output.extend(bytes(4))
    groups = defaultdict(list)
    for tick, layer, key in notes:
        groups[tick].append((layer, key))
    previous_tick = -1
    for tick, group in sorted(groups.items()):
        number('H', tick - previous_tick)
        previous_tick, previous_layer = tick, -1
        for layer, key in sorted(group):
            number('H', layer - previous_layer)
            previous_layer = layer
            output.extend(bytes((0, key, 100, 100)))
            number('h', 0)
        number('H', 0)
    number('H', 0)
    for track in plan['tracks']:
        string(track['name'])
        output.extend(bytes((0, 100, 100)))
    number('B', 0)
    path = Path(path)
    path.write_bytes(output)
    return path


def render_preview(plan, path, track=None, sample_rate=22050):
    """A quiet additive reed tone; streams PCM blocks to keep memory bounded."""
    plan = validate_arrangement(plan)
    if track is not None:
        _integer(track, '预览声部', 0, len(plan['tracks']) - 1)
    events = sorted((e for i, t in enumerate(plan['tracks']) if track is None or i == track
                     for e in t['events']), key=lambda e: e['start'])
    rate = _integer(sample_rate, '采样率', 8000, 48000)
    # Preview is intentionally bounded; full MIDI/NBS export is never truncated.
    length = min(plan['duration'] + .2, 180)
    position, pending, active = 0, 0, []
    path = Path(path)
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        while position < int(length * rate):
            size = min(2048, int(length * rate) - position)
            ending = (position + size) / rate
            while pending < len(events) and events[pending]['start'] < ending:
                active.append(events[pending])
                pending += 1
            samples = array('h')
            amplitude = 9000 / max(1, len(plan['tracks']) ** .5)
            for offset in range(size):
                t, value = (position + offset) / rate, 0.0
                for event in active:
                    elapsed = t - event['start']
                    if not 0 <= elapsed <= event['duration'] + .035:
                        continue
                    envelope = min(1, elapsed / .012) * min(1, max(0, (event['duration'] + .035 - elapsed) / .04))
                    phase = elapsed * math.tau * 440 * 2 ** ((event['midi'] - 69) / 12)
                    value += envelope * (math.sin(phase) + .18 * math.sin(phase * 2) + .06 * math.sin(phase * 3)) * amplitude
                samples.append(max(-32767, min(32767, round(value))))
            handle.writeframes(samples.tobytes())
            position += size
            active = [e for e in active if e['start'] + e['duration'] + .035 > position / rate]
    return path
