"""A small, declarative music language: no generated Python or shell is accepted."""
from __future__ import annotations

import collections
import json
import math

from .ai_adapters import ProviderError, SCHEMA

ROLES = {"melody": "主旋律", "accompaniment": "伴奏", "bass": "低音",
         "harmony": "和声", "countermelody": "副旋律"}


def _number(value):
    return type(value) in {int, float} and math.isfinite(value)


def _schema_validate(value, schema):
    """Validate the deliberately small schema without permissive coercion."""
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]):
            raise ProviderError("AI 输出包含不支持的字段或缺少编曲字段。")
        for key, child in schema["properties"].items():
            _schema_validate(value[key], child)
    elif kind == "array":
        if not isinstance(value, list) or len(value) > schema["maxItems"]:
            raise ProviderError("AI 编曲超出复杂度限制。")
        for item in value:
            _schema_validate(item, schema["items"])
    elif kind == "string":
        if not isinstance(value, str) or value not in schema.get("enum", []):
            raise ProviderError("AI 返回了不支持的声部类型。")
    elif kind in {"integer", "number"}:
        if not _number(value) or (kind == "integer" and type(value) is not int):
            raise ProviderError("AI 乐谱含有无效数字。")
        if not schema.get("minimum", -1e6) <= value <= schema.get("maximum", 1e6):
            raise ProviderError("AI 乐谱数字超出允许范围。")


def _midi(note):
    return int(round(note.key + 21 + getattr(note, "pitch", 0) / 100))


def summarize(song, players, instruction, previous=None):
    """Bound prompt size while retaining all sections and representative melodies."""
    from fluentmelody.core.music import source_notes
    source = source_notes(song)
    groups = collections.defaultdict(list)
    for note in source:
        groups[note.layer].append(note)
    if len(groups) > 64:
        raise ProviderError("这首曲子超过 64 个有效音轨，请先选择需要的音轨。")
    duration = max((n.start + n.duration for n in source), default=0)
    layers = []
    for layer_id, notes in sorted(groups.items()):
        notes.sort(key=lambda n: (n.start, n.midi))
        pitches = sorted(n.midi for n in notes)
        # Eight evenly spread short phrases, so intros/interludes are represented.
        sample_indices = sorted({min(len(notes) - 1, int(i * (len(notes) - 1) / 7)) for i in range(8)})
        samples = [[round(notes[i].start, 3), notes[i].midi] for i in sample_indices]
        bins = [0] * 16
        for n in notes:
            bins[min(15, int(n.start / max(1, duration) * 16))] += 1
        name = song.layers[layer_id].name if layer_id < len(song.layers) else ""
        layers.append({"id": layer_id, "name": str(name)[:48], "count": len(notes),
                       "range": [pitches[0], pitches[-1]], "median": pitches[len(pitches) // 2],
                       "start": round(notes[0].start, 3),
                       "end": round(notes[-1].start, 3),
                       "activity_16_sections": bins, "samples": samples})
    # A melodic phrase near the beginning, plus samples across the full timeline.
    ordered = sorted(source, key=lambda n: (n.start, -n.midi))
    sample = ordered[:min(96, len(ordered))]
    if len(ordered) > 96:
        sample += [ordered[int(i * (len(ordered) - 1) / 63)] for i in range(64)]
    context = {"players": players, "preference": instruction, "song_name": song.name[:80],
               "duration": round(duration, 3), "layers": layers,
               "score_samples": [[round(n.start, 3), n.layer, n.midi] for n in sample]}
    if previous:
        # Previous plans are bounded machine-validated numeric/enum data.
        context["previous_plan"] = previous
    if len(json.dumps(context, ensure_ascii=False)) > 40000:
        # Preserve every layer and remove some samples instead of dropping late sections.
        context.pop("previous_plan", None)
        context["score_samples"] = context["score_samples"][:32]
        for layer in layers:
            layer["samples"] = layer["samples"][::2]
    return context


def apply_directives(song, directives, players):
    from fluentmelody.core.music import score_to_arrangement, validate_arrangement, source_notes

    _schema_validate(directives, SCHEMA)
    if len(directives["parts"]) != players:
        raise ProviderError("AI 返回的声部数量与演奏人数不一致。")
    source = source_notes(song)
    source_end = max(n.start for n in source)
    song_duration = max(n.start + n.duration for n in source)
    available = {n.layer for n in source}
    speed = directives["speed"]
    score = {"name": str(song.name)[:100] + " · AI 合奏版", "tracks": []}

    def check_layers(layer_ids):
        if len(set(layer_ids)) != len(layer_ids) or not set(layer_ids).issubset(available):
            raise ProviderError("AI 引用了不存在或重复的音轨。")

    for index, part in enumerate(directives["parts"]):
        check_layers(part["layer_ids"])
        previous_end = -1
        for section in part["sections"]:
            check_layers(section["layer_ids"])
            if not 0 <= section["start"] < section["end"] <= song_duration + 1:
                raise ProviderError("AI 段落时间超出原曲范围。")
            if section["start"] < previous_end or not -24 <= section["transpose"] <= 24:
                raise ProviderError("AI 段落重叠或移调幅度过大。")
            previous_end = section["end"]
        grouped = collections.defaultdict(list)
        for note in source:
            start = note.start
            layer_ids, transpose = part["layer_ids"], part["transpose"]
            for section in part["sections"]:
                if section["start"] <= start < section["end"]:
                    layer_ids, transpose = section["layer_ids"], section["transpose"]
                    break
            if note.layer in layer_ids:
                pitch = note.midi + transpose
                if 0 <= pitch <= 127:
                    grouped[start].append((pitch, getattr(note, "duration", None)))
        starts = sorted(grouped)
        notes = []
        for i, start in enumerate(starts):
            if i % part["stride"]:
                continue
            values = sorted(grouped[start], key=lambda value: value[0])
            if part["selection"] == "top":
                values = values[-1:]
            elif part["selection"] == "bottom":
                values = values[:1]
            for pitch, source_duration in values:
                duration = (starts[i + 1] - start if i + 1 < len(starts) else .5)
                if source_duration is not None and _number(source_duration) and source_duration > 0:
                    duration = source_duration
                duration = min(15, max(.03, duration / speed))
                notes.append({"start": start / speed, "duration": duration, "midi": pitch})
        for note in part["notes"]:
            if not 0 <= note["start"] <= song_duration + 1 or not .03 <= note["duration"] <= 2 or not 0 <= note["midi"] <= 127:
                raise ProviderError("AI 新写音符不符合时间、音域或时值要求。")
            notes.append({"start": note["start"] / speed, "duration": note["duration"] / speed, "midi": note["midi"]})
        if len(notes) > 50000:
            raise ProviderError("AI 编曲音符过多。")
        notes.sort(key=lambda n: (n["start"], -n["midi"]))
        score["tracks"].append({"name": f"{ROLES[part['role']]} · 演奏者 {index + 1}", "notes": notes})
    if not any(track["notes"] for track in score["tracks"]):
        raise ProviderError("AI 编曲没有有效音符。")
    # For long songs, reject accidental conversion of a small sample only.
    last_note = max(n["start"] for track in score["tracks"] for n in track["notes"])
    if source_end > 30 and last_note < source_end / speed * .7:
        raise ProviderError("AI 编曲丢失了较多后半段旋律，已退回次数。")
    try:
        plan = score_to_arrangement(score, players=players)
        plan = validate_arrangement(plan, players=players)
    except (TypeError, ValueError) as exc:
        raise ProviderError("AI 编曲未通过单音、间隔或音域校验；本次次数已退回。") from exc
    if any(not track["events"] for track in plan["tracks"]):
        raise ProviderError("AI 没有为每位演奏者写出有效声部；本次次数已退回。")
    plan["summary"] = f"AI 按原曲完整时间线编排了 {players} 个声部，统一速度 {speed:.2f} 倍。"
    return plan
