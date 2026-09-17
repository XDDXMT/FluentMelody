"""Melody selection and bounded timing edits for a monophonic instrument.

Model likelihoods describe individual notes; track roles and continuity retain
phrases and rests instead of filling every accompaniment onset with a key press.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from math import log
from statistics import median


LEAD_WORDS = ('melody','vocal','lead','主旋律','人声','主奏')
BACKING_WORDS = ('bass','伴奏','harmony','和声','鼓','drum','chord')


def gate_length(note):
    value = getattr(note,'gate_duration',None)
    return note.duration if value is None else value


def onset_groups(notes):
    groups = []
    for note in notes:
        if groups and note.start-groups[-1][0].start <= .012:
            groups[-1].append(note)
        else:
            groups.append([note])
    return groups


def project_timing(starts,spacing):
    """Least-squares separation (PAVA), without cumulative timing drift."""
    blocks = []
    for i,start in enumerate(starts):
        blocks.append([start-i*spacing,1,i,i+1])
        while len(blocks)>1 and blocks[-2][0]/blocks[-2][1]>blocks[-1][0]/blocks[-1][1]:
            right = blocks.pop(); left = blocks.pop()
            blocks.append([left[0]+right[0],left[1]+right[1],left[2],right[3]])
    result = [0.0]*len(starts)
    for total,count,a,b in blocks:
        level = max(0.0,total/count)
        for i in range(a,b):
            result[i] = level+i*spacing
    return result


@dataclass
class MelodySelection:
    notes: list
    weights: list[float]
    primary_layers: set[int]


def select_melody(notes,names,probabilities=None,prefer_named=True):
    """Select note objects without changing their pitch, time or durations."""
    if not notes:
        return MelodySelection([],[],set())
    groups = onset_groups(notes)
    single_line = all(len(g)==1 for g in groups) and len({n.layer for n in notes})==1
    by_layer = defaultdict(list)
    for note in notes:
        by_layer[note.layer].append(note)
    model = probabilities is not None and len(probabilities)==len(notes)
    raw_probabilities = sorted(max(0.,min(1.,float(p))) for p in (probabilities or []))
    if model and (not raw_probabilities or raw_probabilities[-1]<.08):
        model = False
    ceiling = max(.12,raw_probabilities[int((len(raw_probabilities)-1)*.95)]) if model else 1.
    likelihood = {id(n):max(0.,min(1.,float(p)/ceiling)) for n,p in zip(notes,probabilities or [])}
    explicit,backing = set(),set()
    for layer in by_layer:
        name = names[layer].lower()
        if any(word in name for word in BACKING_WORDS):
            backing.add(layer)
        elif prefer_named and any(word in name for word in LEAD_WORDS):
            explicit.add(layer)
    # Rank whole parts before selecting notes. A sustained pedal is not a long
    # physical key press and must not make a bass part look like a vocal line.
    track_quality = {}
    for layer,part in by_layer.items():
        grouped = onset_groups(part)
        single = sum(len(g)==1 for g in grouped)/len(grouped)
        center = median(n.midi for n in part)
        gate = median(min(2.,gate_length(n)) for n in part)
        probability = median(likelihood.get(id(n),.5) for n in part)
        score = (center-60)*.045 + single*1.2 + min(gate,.8)*.35
        if model:
            score += (probability-.5)*2.2
        if layer in explicit:
            score += 4
        if layer in backing:
            score -= 1.5
        track_quality[layer] = score
    primary = set(explicit)
    if not primary and len(by_layer)>1:
        ranked = sorted(track_quality,key=track_quality.get,reverse=True)
        if track_quality[ranked[0]]-track_quality[ranked[1]]>=.55:
            primary.add(ranked[0])
    trusted_primary = {layer for layer in primary if
                       max(n.midi for n in by_layer[layer])-min(n.midi for n in by_layer[layer])<=30
                       and all(len(g)==1 for g in onset_groups(by_layer[layer]))}
    lead_notes = sorted((n for n in notes if n.layer in primary),key=lambda n:n.start)
    lead_starts = [n.start for n in lead_notes]
    typical_gap = median([b-a for a,b in zip(lead_starts,lead_starts[1:]) if b-a>.04] or [.5])
    phrase_rest = min(1.6,max(.65,typical_gap*2.2))
    # Suppress accompaniment during a lead phrase, but retain instrumental
    # sections before the vocal entry and in sufficiently long interludes.
    prepared = []
    for group in groups:
        leads = [n for n in group if n.layer in primary]
        if single_line:
            candidates = group
        elif leads:
            candidates = leads
        else:
            t = group[0].start
            at = bisect_right(lead_starts,t)
            previous = lead_notes[at-1] if at else None
            following = lead_notes[at] if at<len(lead_notes) else None
            held_lead = previous is not None and t < previous.start+gate_length(previous)+.1
            short_rest = previous is not None and following is not None and (
                following.start-previous.start-gate_length(previous) < phrase_rest*1.5)
            if primary and (held_lead or short_rest):
                continue
            candidates = group
        prepared.append(candidates)
    if not prepared:
        prepared = groups
    context_times = [g[0].start for g in groups]
    context_tops = [max(n.midi for n in g) for g in groups]
    context_confidence = [max(likelihood.get(id(n),.5) for n in g) for g in groups]
    anchors = sorted((n for n in notes if likelihood.get(id(n),0)>=.65),key=lambda n:n.start)
    anchor_times = [n.start for n in anchors]
    repeated = set()
    for before,current,after in zip(groups,groups[1:],groups[2:]):
        if after[0].start-before[0].start<=1.6:
            common = {n.midi for n in before}&{n.midi for n in after}
            repeated.update(id(n) for n in current if n.midi in common)
    # A small beam follows a melodic line through chord choices. Skipping is
    # an explicit option when model evidence indicates accompaniment.
    beams = [(0.0,None,None)]  # score, last note, linked path
    for candidates in prepared:
        t = candidates[0].start
        high = max(n.midi for n in candidates)
        a,b = bisect_left(context_times,t-4),bisect_right(context_times,t+4)
        local_pitches = sorted(context_tops[a:b])
        local_confidence = sorted(context_confidence[a:b])
        register = local_pitches[int((len(local_pitches)-1)*.65)]
        active_model = model and local_confidence[int((len(local_confidence)-1)*.65)]>=.55
        mandatory = (bool(explicit) or single_line or not model or
                     bool(trusted_primary.intersection(n.layer for n in candidates)))
        anchor_index = bisect_left(anchor_times,t-.013)
        previous_anchor = anchors[anchor_index-1] if anchor_index else None
        next_index = bisect_right(anchor_times,t+.013)
        next_anchor = anchors[next_index] if next_index<len(anchors) else None
        ranked = sorted(candidates,key=lambda n:(likelihood.get(id(n),.5),n.midi),reverse=True)[:12]
        expanded = list(beams) if model and not mandatory else []
        for note in ranked:
            p = likelihood.get(id(note),.5)
            if model and not mandatory:
                reward = 3.2*p-.9
            else:
                reward = 1.6 + (p-.5)*1.8
            reward += min(gate_length(note),1.0)*.35
            reward += max(-.9,(note.midi-high)*.055)
            reward += .6 if note.layer in primary else -.45 if note.layer in backing else 0
            if model and not mandatory:
                # The published model targets vocal melody. In instrumental
                # passages use it softly; within an active lead register rescue
                # plausible low-score notes rather than cutting the phrase.
                if not active_model:
                    reward += .95
                elif note.midi>=register-7:
                    reward += .85
                if id(note) in repeated and note.midi>=register-7:
                    reward += .9
                if (previous_anchor is not None and next_anchor is not None
                        and t-previous_anchor.start<1.3 and next_anchor.start-t<1.3
                        and note.midi<min(previous_anchor.midi,next_anchor.midi)-9 and p<.4):
                    reward -= 1.4
            best = None
            for score,last,path in beams:
                penalty = 0.
                if last is not None:
                    delta = t-last.start
                    if delta < .04:
                        continue
                    phrase = delta < 2.5
                    jump = abs(note.midi-last.midi)
                    if phrase:
                        penalty += max(0,jump-5)*.035 + max(0,jump-12)*.055
                        if not active_model:
                            penalty *= .4
                        if note.layer!=last.layer:
                            penalty += .4
                        overlap = min(gate_length(last),2.)-delta
                        if overlap>.08 and note.layer!=last.layer:
                            penalty += min(1.1,overlap*.9)
                    if active_model and not mandatory and p<.2:
                        penalty += .5
                item = (score+reward-penalty,note,(note,path))
                if best is None or item[0]>best[0]:
                    best = item
            if best is not None:
                expanded.append(best)
        if expanded:
            # Deduplicate paths ending in the same note; retain a rest path.
            unique = {}
            for state in expanded:
                key = id(state[1])
                if key not in unique or state[0]>unique[key][0]:
                    unique[key] = state
            beams = sorted(unique.values(),key=lambda s:s[0],reverse=True)[:16]
    selected = []
    path = max(beams,key=lambda s:s[0])[2]
    while path is not None:
        note,path = path
        selected.append(note)
    selected.reverse()
    if not selected:
        selected = [max(notes,key=lambda n:(likelihood.get(id(n),.5),n.midi))]
    weights = [1. + min(gate_length(n),1.)*2. + likelihood.get(id(n),.5)*3.
               + (3. if n.layer in primary else 0.)
               + (2. if i in (0,len(selected)-1) else 0.) for i,n in enumerate(selected)]
    return MelodySelection(selected,weights,primary)
