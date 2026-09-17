"""NBS v0–5 reader and monophonic conversion. Standard library only.
Format: https://noteblock.studio/nbs
"""
from dataclasses import dataclass
from pathlib import Path
import math
import struct
from bisect import bisect_right
from collections import defaultdict
from statistics import median


@dataclass
class Note:
    tick: int
    layer: int
    instrument: int
    key: int
    velocity: int = 100
    pitch: int = 0


@dataclass
class Layer:
    name: str
    volume: int = 100


@dataclass
class Song:
    name: str
    version: int
    tempo: float
    length: int
    layers: list
    notes: list
    vanilla: int
    custom_keys: list
    warnings: list
    description: str = ''


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError(f'NBS 文件不完整或字段长度错误（位置 {self.pos}）。')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def string(self):
        length = self.number('i')
        if not 0 <= length <= 4_000_000:
            raise ValueError('NBS 文本字段长度异常。')
        data = self.take(length)
        for encoding in ('utf-8', 'gb18030', 'cp1252'):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                pass
        return data.decode('utf-8', errors='replace')

    @property
    def remaining(self):
        return len(self.data) - self.pos


def read_nbs(path):
    path = Path(path)
    if path.stat().st_size > 32_000_000:
        raise ValueError('文件超过 32 MB，请先在 Note Block Studio 中精简。')
    r = Reader(path.read_bytes())
    length = r.number('H')
    version, vanilla = 0, 10
    if length == 0:
        version, vanilla = r.number('B'), r.number('B')
        if version not in range(1, 6):
            raise ValueError(f'暂不支持 NBS v{version}，请用 Note Block Studio 另存为 v5。')
        length = r.number('H') if version >= 3 else 0
    count = r.number('H')
    if count > 10000:
        raise ValueError('音轨数量异常（超过 10000）。')
    name = r.string() or path.stem
    r.string()
    r.string()
    description = r.string()
    tempo = r.number('H') / 100
    if tempo <= 0:
        raise ValueError('NBS 速度必须大于零。')
    r.take(3 + 5 * 4)
    r.string()
    warnings = []
    if version >= 4:
        loop = r.number('B')
        r.take(3)
        if loop:
            warnings.append('文件自带的循环点未采用；是否整曲循环由界面控制。')
    notes, tick = [], -1
    while True:
        jump = r.number('H')
        if jump == 0:
            break
        tick += jump
        layer = -1
        while True:
            jump = r.number('H')
            if jump == 0:
                break
            layer += jump
            if layer >= count:
                raise ValueError('音符引用了文件声明范围之外的音轨。')
            instrument, key = r.number('B'), r.number('B')
            velocity, pitch = 100, 0
            if version >= 4:
                velocity = r.number('B')
                r.take(1)
                pitch = r.number('h')
            if key > 87 or velocity > 100:
                raise ValueError('NBS 音高或力度字段超出规范范围。')
            notes.append(Note(tick, layer, instrument, key, velocity, pitch))
            if len(notes) > 300000:
                raise ValueError('音符超过 30 万个，请先精简歌曲。')
    layers = []
    if r.remaining:
        for i in range(count):
            title = r.string() or f'音轨 {i + 1}'
            if version >= 4:
                r.take(1)  # Locked means edit-lock, not mute.
            volume = r.number('B')
            if volume > 100:
                raise ValueError('音轨音量字段异常。')
            if version >= 2:
                r.take(1)
            layers.append(Layer(title, volume))
    else:
        layers = [Layer(f'音轨 {i + 1}') for i in range(count)]
    custom = []
    if r.remaining:
        for _ in range(r.number('B')):
            r.string()
            r.string()  # Never open or execute referenced sound files.
            custom.append(r.number('B'))
            r.take(1)
    if r.remaining:
        warnings.append(f'文件末尾有 {r.remaining} 字节扩展数据，未使用。')
    return Song(name, version, tempo, max(length, tick + 1), layers,
                notes, vanilla, custom, warnings, description)


@dataclass
class Event:
    start: float
    duration: float
    key_index: int
    modifiers: tuple
    midi: int
    layer: int


@dataclass
class Plan:
    events: list
    duration: float
    summary: str
    warnings: list


def convert_legacy(song, selected=None, speed=1.0, gap=0.1, max_hold=0.8,
            base=60, transpose=0, combine_half=True, skip_drums=True):
    if not (math.isfinite(speed) and 0.1 <= speed <= 3 and
            math.isfinite(gap) and 0.1 <= gap <= 10 and
            math.isfinite(max_hold) and 0.08 <= max_hold <= 10):
        raise ValueError('速度需为 0.1～3 倍，间隔至少 0.1 秒，最长持续需为 0.08～10 秒。')
    if selected is not None and not selected:
        raise ValueError('请至少选择一条音轨，或选择“合并所有音轨”。')
    groups = {}
    ignored = custom_count = detuned = 0
    for note in song.notes:
        if selected is not None and note.layer not in selected:
            continue
        if (not note.velocity or not song.layers[note.layer].volume or
                (skip_drums and note.instrument < song.vanilla and note.instrument in (2, 3, 4))):
            ignored += 1
            continue
        pitch = note.key + 21 + note.pitch / 100
        if note.instrument >= song.vanilla:
            custom_count += 1
            index = note.instrument - song.vanilla
            if index < len(song.custom_keys):
                pitch += song.custom_keys[index] - 45
        midi = math.floor(pitch + 0.5) + transpose
        detuned += abs(pitch - round(pitch)) > 0.001
        groups.setdefault(note.tick, []).append((midi, note))
    if not groups:
        raise ValueError('所选音轨没有可播放音符（可能是空轨、静音或已过滤的打击乐）。')
    chosen = [max(items, key=lambda x: (x[0], x[1].velocity, -x[1].layer))
              for _, items in sorted(groups.items())]
    ticks = sorted(groups)
    merged = sum(len(items) - 1 for items in groups.values())
    # Global slowdown preserves relative rhythm, rather than pushing dense notes individually.
    intervals = [(b - a) / song.tempo / speed for a, b in zip(ticks, ticks[1:])]
    stretch = max(1.0, (gap + 0.08 + 0.02) / min(intervals)) if intervals else 1.0
    starts = [tick / song.tempo / speed * stretch for tick in ticks]
    mapping = _pitch_mapping(base, combine_half)
    events, folded = [], 0
    for i, (midi, note) in enumerate(chosen):
        candidates = [pitch for pitch in mapping if (pitch - midi) % 12 == 0]
        target = min(candidates, key=lambda pitch: (abs(pitch - midi), len(mapping[pitch][1])))
        folded += target != midi
        index, modifiers = mapping[target]
        available = starts[i + 1] - starts[i] - gap - 0.02 if i + 1 < len(starts) else min(0.3, max_hold)
        duration = min(max_hold, max(0.08, available))
        events.append(Event(starts[i], duration, index, modifiers, target, note.layer))
    end = max(events[-1].start + events[-1].duration + gap,
              song.length / song.tempo / speed * stretch)
    warnings = list(song.warnings)
    if custom_count:
        warnings.append(f'{custom_count} 个自定义乐器音符已按音高换成口风琴，不加载原音色。')
    if detuned:
        warnings.append(f'{detuned} 个微调音高已四舍五入到最近半音。')
    summary = (f'转换完成：{len(events)} 个单音；同刻舍弃 {merged} 音；过滤 {ignored} 音；'
               f'折叠八度 {folded} 音；实际速度 {speed / stretch:.2f} 倍；约 {end:.1f} 秒。')
    if stretch > 1.0001:
        warnings.append('音符过密，已整曲等比例减速，保证发声时长和松键间隔。')
    return Plan(events, end, summary, warnings)


def recommend_layers(song):
    """Only solo an explicitly named melody; generic piano layers often split chords."""
    active = defaultdict(list)
    for note in song.notes:
        if note.velocity and song.layers[note.layer].volume and not (
                note.instrument < song.vanilla and note.instrument in (2, 3, 4)):
            active[note.layer].append(note)
    candidates = []
    for index, notes in active.items():
        name = song.layers[index].name.lower()
        if any(word in name for word in ('bass', 'drum', 'percussion', '伴奏', '和声', 'harmony', 'strings')):
            continue
        priority = 3 if any(word in name for word in ('melody', '主旋律', '旋律', 'vocal', '人声', 'lead')) else (
            2 if any(word in name for word in ('sax', '萨克斯', 'flute', '长笛')) else 0)
        if priority and len(notes) >= 8:
            candidates.append((priority, len(notes), -index))
    if candidates:
        index = -max(candidates)[2]
        return {index}, f'按轨道名称优先选择第 {index + 1} 轨：{song.layers[index].name}（可手动改选）。'
    return set(active), '没有明确命名的旋律轨，合并有声音轨并在同刻优先保留最高音；可手动改选。'


def _sparse_indices(starts, weights, spacing):
    scores = [0.0] * (len(starts) + 1)
    previous = []
    for i, start in enumerate(starts):
        prev = bisect_right(starts, start - spacing + 1e-9, 0, i)
        previous.append(prev)
        scores[i + 1] = max(scores[i], scores[prev] + weights[i])
    indices, i = [], len(starts)
    while i:
        if scores[previous[i - 1]] + weights[i - 1] > scores[i - 1] + 1e-9:
            indices.append(i - 1)
            i = previous[i - 1]
        else:
            i -= 1
    return list(reversed(indices)), scores[-1]


def _pitch_mapping(base=60, combine_half=True):
    """Map every playable pitch, preferring equivalent keys with fewer modifiers.

    At C4, octave + semitone combinations extend the chromatic range from
    C3 through C#6 inclusive. Left and right are never combined.
    """
    mapping = {}
    for octave, modifier in [(-1, (2,)), (0, ()), (1, (8,))]:
        for index, step in enumerate((0, 2, 4, 5, 7, 9, 11, 12)):
            for half in (0, 1):
                if half and octave and not combine_half:
                    continue
                pitch = base + octave * 12 + step + half
                mods = modifier + ((32,) if half else ())
                if pitch not in mapping or len(mods) < len(mapping[pitch][1]):
                    mapping[pitch] = (index, mods)
    return mapping


def _smooth_octaves(pitches, base, combine_half):
    mapping = _pitch_mapping(base, combine_half)
    # Register changes are only needed outside the playable range. In
    # particular, do not shift an already valid C3 or C#6 phrase inward.
    if all(pitch in mapping for pitch in pitches):
        return list(pitches), mapping, 0
    center = median(pitches)
    shift = 12 * max(-2, min(2, round((base + 6 - center) / 12))) if (
        center > base + 16 or center < base - 8) else 0
    wanted = [p + shift for p in pitches]
    if all(p in mapping for p in wanted):
        return wanted, mapping, shift
    costs, paths = {}, []
    for i, pitch in enumerate(wanted):
        current, links = {}, {}
        for target in sorted(p for p in mapping if (p - pitch) % 12 == 0):
            unary = abs(target - pitch) * .16 + abs(target - (base + 6)) * .02
            if not i:
                current[target], links[target] = unary, None
                continue
            original_delta = pitch - wanted[i - 1]
            def transition(last):
                step = target - last
                direction = .3 if original_delta * step < 0 and abs(original_delta) <= 7 else 0
                fidelity = abs(step - original_delta) * .07 if abs(original_delta) <= 7 else 0
                return costs[last] + abs(step) * .05 + max(0, abs(step) - 7) * .2 + direction + fidelity
            last = min(costs, key=transition)
            current[target], links[target] = unary + transition(last), last
        costs = current
        paths.append(links)
    target = min(costs, key=costs.get)
    result = []
    for links in reversed(paths):
        result.append(target)
        target = links[target]
    return list(reversed(result)), mapping, shift


def convert(song, selected=None, speed=1.0, gap=0.1, max_hold=0.8,
            base=60, transpose=0, combine_half=True, skip_drums=True,
            strategy='smart', auto_select=False, trim_intro=False):
    if strategy not in ('smart', 'tempo', 'preserve'):
        raise ValueError('未知的转换方式。')
    if not (math.isfinite(speed) and .1 <= speed <= 3 and math.isfinite(gap) and .1 <= gap <= 10
            and math.isfinite(max_hold) and .08 <= max_hold <= 10):
        raise ValueError('速度需为 0.1～3 倍，间隔至少 0.1 秒，最长持续需为 0.08～10 秒。')
    explanation = None
    if auto_select:
        selected, explanation = recommend_layers(song)
    if selected is not None and (not selected or any(i not in range(len(song.layers)) for i in selected)):
        raise ValueError('请至少选择一条有效音轨。')
    if strategy == 'preserve':
        plan = convert_legacy(song, selected, speed, gap, max_hold, base, transpose, combine_half, skip_drums)
        if trim_intro and plan.events:
            offset = plan.events[0].start
            for event in plan.events:
                event.start -= offset
            plan.duration -= offset
            plan.summary += f' 去除开头空白后约 {plan.duration:.1f} 秒。'
        if explanation:
            plan.warnings.insert(0, explanation)
        return plan
    groups = defaultdict(list)
    ignored = detuned = custom = 0
    for note in song.notes:
        if selected is not None and note.layer not in selected:
            continue
        if (not note.velocity or not song.layers[note.layer].volume or
                (skip_drums and note.instrument < song.vanilla and note.instrument in (2, 3, 4))):
            ignored += 1
            continue
        pitch = note.key + 21 + note.pitch / 100
        if note.instrument >= song.vanilla:
            custom += 1
            index = note.instrument - song.vanilla
            if index < len(song.custom_keys):
                pitch += song.custom_keys[index] - 45
        detuned += abs(pitch - round(pitch)) > .001
        groups[note.tick].append((math.floor(pitch + .5) + transpose, note))
    if not groups:
        raise ValueError('所选音轨没有可播放音符（空轨、静音或打击乐已过滤）。')
    ticks = sorted(groups)
    chosen = [max(groups[tick], key=lambda x: (x[0], x[1].velocity, -x[1].layer)) for tick in ticks]
    starts = [tick / song.tempo / speed for tick in ticks]
    weights = []
    pitch_center = median(p for p, _ in chosen)
    for i, (pitch, note) in enumerate(chosen):
        after = starts[i + 1] - starts[i] if i + 1 < len(starts) else .6
        before = starts[i] - starts[i - 1] if i else 1
        weight = 1 + min(after, .65) * 3 + (.6 if before > .4 else 0)
        if pitch < pitch_center - 12:
            weight *= .55
        if i in (0, len(chosen) - 1):
            weight += 5
        weights.append(weight)
    # Max 15% automatic slowdown. A rare fast run cannot slow the whole song 3x.
    spacing = gap + min(.10, max_hold) + .02
    factors = (1.0, .94, .90, .85) if strategy == 'smart' else (1.0,)
    options = []
    for factor in factors:
        indices, score = _sparse_indices([t / factor for t in starts], weights, spacing)
        utility = score / sum(weights) - .8 * (1 - factor)
        options.append((utility, factor, indices))
    complete = [option for option in options if len(option[2]) == len(chosen)]
    # Prefer a small slowdown that keeps an entire melody, if one is available.
    _, factor, indices = max(complete, key=lambda x: x[1]) if complete else max(options, key=lambda x: (x[0], x[1]))
    offset = starts[indices[0]] / factor if trim_intro else 0
    final_starts = [starts[i] / factor - offset for i in indices]
    original_pitches = [chosen[i][0] for i in indices]
    mapped, mapping, shift = _smooth_octaves(original_pitches, base, combine_half)
    events = []
    for j, (i, pitch) in enumerate(zip(indices, mapped)):
        available = final_starts[j + 1] - final_starts[j] - gap - .02 if j + 1 < len(indices) else min(.3, max_hold)
        index, modifiers = mapping[pitch]
        events.append(Event(final_starts[j], min(max_hold, max(.08, available)),
                            index, modifiers, pitch, chosen[i][1].layer))
    # Avoid waiting through discarded accompaniment after the selected melody ends.
    end = events[-1].start + events[-1].duration + gap
    merged = sum(len(group) - 1 for group in groups.values())
    folded = sum(a != b for a, b in zip(original_pitches, mapped))
    warnings = list(song.warnings)
    if explanation:
        warnings.insert(0, explanation)
    if custom:
        warnings.append(f'{custom} 个自定义乐器音符按音高转换，不加载其音色。')
    if detuned:
        warnings.append(f'{detuned} 个微分音取最近半音。')
    if trim_intro and offset:
        warnings.append(f'已去除开头 {offset:.1f} 秒空白。')
    if shift:
        warnings.append(f'为适应口风琴，旋律整体调整 {shift // 12:+d} 个八度，再平滑处理超音域音符。')
    warnings.append('旋律选择依据轨名和音高，是启发式改编；不保证等同人工扒谱。')
    summary = (f'转换完成：{len(events)} 个单音；同刻舍弃 {merged} 音；删减过密 {len(chosen)-len(indices)} 音；'
               f'过滤 {ignored} 音；调整八度 {folded} 音；实际速度 {speed*factor:.2f} 倍；约 {end:.1f} 秒。')
    return Plan(events, end, summary, warnings)


def write_example(path):
    """Write a real v5 NBS: traditional Twinkle melody + bass + offbeat harmony."""
    output = bytearray()
    def number(fmt, value):
        output.extend(struct.pack('<' + fmt, value))
    def string(value):
        data = value.encode('utf-8')
        number('i', len(data))
        output.extend(data)
    number('H', 0)
    number('B', 5)
    number('B', 16)
    number('H', 96)
    number('H', 3)
    for value in ['Twinkle - 3 tracks', 'Codex demo', 'Traditional',
                  'Track 1 melody; track 2 bass; track 3 offbeat harmony.']:
        string(value)
    number('H', 500)
    output.extend(bytes([0, 10, 4]))
    for _ in range(5):
        number('i', 0)
    string('')
    output.extend(bytes([0, 0]))
    number('H', 0)
    notes, tick = [], 0
    scale = [60, 62, 64, 65, 67, 69, 71]
    for phrase in ['1155665', '4433221', '5544332', '5544332', '1155665', '4433221']:
        for i, digit in enumerate(phrase):
            notes.append((tick, 0, 0, scale[int(digit) - 1] - 21))
            tick += 4 if i == 6 else 2
    for tick in range(0, 96, 8):
        notes.append((tick, 1, 1, (48 if tick % 16 == 0 else 55) - 21))
        notes.append((tick + 3, 2, 0, 64 - 21))
    previous_tick = -1
    by_tick = {}
    for note in notes:
        by_tick.setdefault(note[0], []).append(note)
    for tick, items in sorted(by_tick.items()):
        number('H', tick - previous_tick)
        previous_tick, previous_layer = tick, -1
        for _, layer, instrument, key in sorted(items):
            number('H', layer - previous_layer)
            previous_layer = layer
            output.extend(bytes([instrument, key, 100, 100]))
            number('h', 0)
        number('H', 0)
    number('H', 0)
    for name in ['Melody - main tune', 'Bass - low notes', 'Harmony - offbeat']:
        string(name)
        output.extend(bytes([0, 100, 100]))
    number('B', 0)
    Path(path).write_bytes(output)
