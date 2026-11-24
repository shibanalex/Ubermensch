#!/usr/bin/env python3
import os
import sys
import json
import zipfile
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

SOLVER_JS = Path(__file__).parent / "solver.js"

def detect_instrument(text):
    text = (text or "").lower()
    # Explicit exclusions
    if any(w in text for w in ["piano", "клавиш", "фортепиано", "ф-но", "harpsichord", "organ", "keyboard"]):
        return "keyboard"
    if any(w in text for w in ["contrabass", "double bass", "doublebass", "kontrabass", "контрабас", "basso"]):
        return "bass"
    if any(w in text for w in ["violoncello", "violoncell", "cello", "violoncelle", "виолончел"]):
        return "cello"
    if any(w in text for w in ["viola", "bratsche", "альт", "alto"]):
        return "viola"
    if any(w in text for w in ["violin", "violine", "скрипк"]):
        return "violin"
    return None

def parse_score(mscz_path):
    mscz_path = Path(mscz_path)
    if not mscz_path.exists():
        raise FileNotFoundError(f"File not found: {mscz_path}")

    # Read XML
    xml_data = None
    if mscz_path.suffix == ".mscz":
        with zipfile.ZipFile(mscz_path, 'r') as z:
            for name in z.namelist():
                if name.endswith(".mscx"):
                    xml_data = z.read(name)
                    break
    elif mscz_path.suffix == ".mscx":
        xml_data = mscz_path.read_bytes()

    if not xml_data:
        raise ValueError(f"Could not read MSCX XML from {mscz_path}")

    root = ET.fromstring(xml_data)
    score_elem = root.find("Score") if root.tag != "Score" else root

    # 1. Map Part / Instrument to Staves
    parts_info = {}
    current_staff_idx = 1
    for part in score_elem.iter("Part"):
        pname = ""
        name_elem = part.find("name")
        if name_elem is not None and name_elem.text:
            pname = name_elem.text

        inst_key = None
        for inst in part.iter("Instrument"):
            iid = inst.find("instrumentId")
            long_n = inst.find("longName")
            short_n = inst.find("shortName")
            track_n = inst.find("trackName")
            full_text = " ".join(filter(None, [
                iid.text if iid is not None else "",
                long_n.text if long_n is not None else "",
                short_n.text if short_n is not None else "",
                track_n.text if track_n is not None else "",
                pname
            ]))
            inst_key = detect_instrument(full_text)
            if inst_key:
                break

        staves_in_part = list(part.findall("Staff"))
        count = len(staves_in_part) if staves_in_part else 1
        for s in range(count):
            parts_info[str(current_staff_idx)] = {
                "staff_num": current_staff_idx,
                "part_name": pname or f"Staff {current_staff_idx}",
                "instrument": inst_key
            }
            current_staff_idx += 1

    # 2. Extract multiple stops by iterating over each Staff
    raw_stops = []
    division = 480

    for staff_elem in score_elem.findall("Staff"):
        staff_id = staff_elem.attrib.get("id", "1")
        inst_info = parts_info.get(staff_id, {
            "staff_num": int(staff_id),
            "part_name": f"Staff {staff_id}",
            "instrument": None
        })

        # Skip non-string instruments (e.g. piano, winds)
        if inst_info["instrument"] == "keyboard":
            continue
        if inst_info["instrument"] is None:
            # If not explicitly identified as string, skip if looks like piano/organ
            p_lower = inst_info["part_name"].lower()
            if any(w in p_lower for w in ["piano", "клавиш", "фортепиано", "ф-но"]):
                continue

        current_tick = 0
        sig_n = 4
        sig_d = 4

        for m_idx, m_elem in enumerate(staff_elem.findall("Measure")):
            m_num = m_idx + 1
            for sig in m_elem.iter("TimeSig"):
                n = sig.find("sigN")
                d = sig.find("sigD")
                if n is not None and n.text: sig_n = int(n.text)
                if d is not None and d.text: sig_d = int(d.text)

            measure_dur_ticks = int(sig_n * (1920 / sig_d))

            for v_elem in m_elem.findall("voice"):
                tick_offset = 0
                for child in v_elem:
                    dur_type = child.findtext("durationType") or "quarter"
                    dur_map = {"whole": 1920, "half": 960, "quarter": 480, "eighth": 240, "16th": 120, "32nd": 60}
                    ticks = dur_map.get(dur_type, 480)
                    dots = len(child.findall("dots"))
                    if dots == 1: ticks = int(ticks * 1.5)
                    elif dots == 2: ticks = int(ticks * 1.75)

                    if child.tag == "Chord":
                        notes = child.findall("Note")
                        if len(notes) > 1:
                            pitches = []
                            note_eids = []
                            for n in notes:
                                p = n.find("pitch")
                                tpc = n.find("tpc")
                                eid = n.attrib.get("id") or n.findtext("eid") or ""
                                if p is not None:
                                    pitches.append({
                                        "pitch": int(p.text),
                                        "tpc": int(tpc.text) if tpc is not None else -1
                                    })
                                    note_eids.append(eid)

                            beat = 1.0 + (tick_offset / (division * 4 / sig_d))

                            inst = inst_info["instrument"]
                            if not inst:
                                inst = "violin" if pitches[0]["pitch"] >= 55 else "cello"

                            raw_stops.append({
                                "measure": m_num,
                                "beat": round(beat, 2),
                                "tick": current_tick + tick_offset,
                                "staff_num": int(staff_id),
                                "staff_name": inst_info["part_name"],
                                "instrument": inst,
                                "pitches": pitches,
                                "eids": note_eids
                            })
                    tick_offset += ticks

            current_tick += measure_dur_ticks

    return raw_stops, parts_info, mscz_path.name

def evaluate_stops_with_node(raw_stops, score_name):
    raw_stops.sort(key=lambda s: (s["tick"], s["staff_num"]))

    node_script = f"""
const fs = require('fs');
const solver = require('{SOLVER_JS}');

const stops = {json.dumps(raw_stops)};
let counts = [0, 0, 0];
let rows = [];
let lines = [];

for (let s of stops) {{
    let r = solver.solveStop(s.instrument, s.pitches, {{
        durationQ: 1, deltaQ: 1, tempo: 120, dynamic: '', technique: 'arco', rapidRun: 1
    }});
    counts[r.severity]++;
    let names = s.pitches.map(n => solver.pitchName(n.pitch, n.tpc));
    let explanation = (r.reasons || []).concat(r.chosen ? (r.chosen.reasons || []) : []);
    let assignment = solver.assignmentDescription(r);
    if (assignment) explanation.push(assignment);

    let location = 'M' + s.measure + ' / beat ' + s.beat;
    let staffLabel = s.staff_name + ' (' + s.instrument + ')';
    let notesLabel = '[' + names.join(', ') + ']';

    let row = {{
        measure: s.measure,
        beat: s.beat,
        location: location,
        staffLabel: staffLabel,
        instrument: s.instrument,
        notesLabel: notesLabel,
        classification: r.label,
        severity: r.severity,
        details: explanation.join(' '),
        eids: s.eids
    }};
    rows.push(row);
    lines.push(location + ' | ' + staffLabel + ' | ' + notesLabel + ' | ' + r.label + '\\n  ' + row.details);
}}

let summary = 'Total ' + stops.length + '   Playable: ' + counts[0] + '   Difficult / Broken: ' + counts[1] + '   Impossible: ' + counts[2];
let report = 'STRING STOPS CHECKER 1.0\\nScore: {score_name}\\n' + summary + '\\n\\n' + lines.join('\\n\\n');

console.log(JSON.stringify({{
    counts: {{ total: stops.length, playable: counts[0], difficult: counts[1], impossible: counts[2] }},
    summary: summary,
    rows: rows,
    report: report
}}));
"""
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)

def main():
    score_path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "/home/allegro/Партитуры/Trio.mscz"
    raw_stops, parts, name = parse_score(score_path)
    evaluated = evaluate_stops_with_node(raw_stops, name)

    out_file = Path("/tmp/string_stops_active.json")
    with open(out_file, "w") as f:
        json.dump(evaluated, f, indent=2)

    print(f"Analyzed {score_path}: {evaluated['summary']}")
    print(f"Results written to {out_file}")

if __name__ == "__main__":
    main()
