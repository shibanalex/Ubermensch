// Pure ES5 String Stops Solver
// BEGIN SOLVER: pure ES5 JavaScript, also executed verbatim by the Node tests.
    function instrumentProfile(key) {
        var profiles = {
            violin: {name: "Violin", open: [55,62,69,76], length: 328, comfortable: 85,
                maximum: 125, maxFret: 36, frame: 5},
            viola: {name: "Viola", open: [48,55,62,69], length: 375, comfortable: 83,
                maximum: 112, maxFret: 32, frame: 5},
            cello: {name: "Cello", open: [36,43,50,57], length: 690, comfortable: 110,
                maximum: 155, maxFret: 32, frame: 3},
            bass: {name: "Double Bass", open: [28,33,38,43], length: 1050, comfortable: 120,
                maximum: 175, maxFret: 28, frame: 2}
        };
        return profiles[key] || null;
    }

    function pitchName(pitch, tpc) {
        if (typeof tpc === "number" && tpc >= -1 && tpc <= 33) {
            var letter = ["F","C","G","D","A","E","B"][(tpc + 1) % 7];
            var alteration = Math.floor((tpc + 1) / 7) - 2;
            var natural = {C:0,D:2,E:4,F:5,G:7,A:9,B:11}[letter];
            var octave = Math.round((pitch - natural - alteration) / 12) - 1;
            return letter + ["bb","b","","#","##"][alteration + 2] + octave;
        }
        return ["C","C#","D","Eb","E","F","F#","G","Ab","A","Bb","B"]
            [((pitch % 12) + 12) % 12] + (Math.floor(pitch / 12) - 1);
    }

    function detectInstrument(text) {
        text = String(text || "").toLowerCase().replace(/<[^>]*>/g, " ");
        if (/clarinet|bassoon|saxophone|flute|trombone|tuba/.test(text)) return "";
        if (/contrabass|double[ ._-]*bass|doublebass|kontrabass|контрабас|\bbasso\b/.test(text)) return "bass";
        if (/violoncello|violoncell|cello|violoncelle|виолончел/.test(text)) return "cello";
        if (/viola|bratsche|viole\b|альт/.test(text)) return "viola";
        if (/violin|violino|violon\b|geige|скрип|\bvln\b/.test(text)) return "violin";
        // Exact orchestral instrument identifiers, rather than ambiguous "bass" labels.
        return "";
    }

    function normalizeDynamic(text) {
        text = String(text || "").replace(/dynamicPiano/g,"p").replace(/dynamicForte/g,"f")
            .replace(/dynamicMezzo/g,"m").replace(/<[^>]*>/g, "").replace(/\s/g, "").toLowerCase();
        return /^(pppppp|ppppp|pppp|ppp|pp|p|mp|mf|f|ff|fff|ffff|fffff|ffffff)$/.test(text) ? text : "";
    }

    function enumerateAssignments(notes, profile, adjacentOnly) {
        var found = [];
        function visit(index, used, strings, offsets) {
            if (index === notes.length) {
                var lo = Math.min.apply(null, strings), hi = Math.max.apply(null, strings);
                if (!adjacentOnly || hi - lo === notes.length - 1)
                    found.push({strings: strings.slice(), offsets: offsets.slice()});
                return;
            }
            for (var s = 0; s < 4; ++s) {
                var offset = notes[index].pitch - profile.open[s];
                if ((used & (1 << s)) || offset < 0 || offset > profile.maxFret) continue;
                if (typeof notes[index].stringIndex === "number" && notes[index].stringIndex !== s) continue;
                strings.push(s); offsets.push(offset);
                visit(index + 1, used | (1 << s), strings, offsets);
                strings.pop(); offsets.pop();
            }
        }
        visit(0, 0, [], []);
        return found;
    }

    function assessAssignment(notes, assignment, key) {
        var p = instrumentProfile(key), stopped = [], reasons = [], severity = 0;
        var openCount = 0, minFret = 100, maxFret = 0;
        for (var i = 0; i < notes.length; ++i) {
            var f = assignment.offsets[i];
            if (f === 0) ++openCount;
            else { stopped.push(f); minFret = Math.min(minFret, f); maxFret = Math.max(maxFret, f); }
        }
        var spread = stopped.length > 1 ? maxFret - minFret : 0;
        var reach = stopped.length > 1 ? p.length * (Math.pow(2, -minFret / 12) - Math.pow(2, -maxFret / 12)) : 0;
        var pitches = notes.map(function(n) { return n.pitch; });
        var interval = Math.max.apply(null, pitches) - Math.min.apply(null, pitches);
        var thumb = false;
        if (reach > p.maximum) {
            severity = 2;
            reasons.push("Stopped-note reach is about " + Math.round(reach) + " mm, above the " + p.maximum + " mm model limit.");
        }
        if ((key === "violin" || key === "viola") && openCount === 0 && interval > 19) {
            severity = 2;
            reasons.push("Fully fingered outer interval exceeds 19 semitones (the configured orchestral reach limit).");
        }
        if (key === "violin" || key === "viola") {
            if (reach > p.comfortable || spread > 7) {
                severity = Math.max(severity, 1);
                reasons.push("Extended hand frame; " + spread + " semitones between stopped-string offsets.");
            }
            if (notes.length === 2 && openCount === 0 && (interval === 15 || interval === 16)) {
                severity = Math.max(severity, 1);
                reasons.push(key === "viola" ? "Viola tenth: extreme stretch, suitable only for large hands or high positions."
                    : "Violin tenth: virtuosic stretch; shortening of the string makes higher positions easier.");
            }
            if (notes.length === 2 && interval === 0) {
                if (minFret < 5 && stopped.length > 1) severity = Math.max(severity, 1);
                reasons.push("Adjacent-string unison: the lower string is stopped seven semitones farther up; higher positions reduce the reach.");
            }
        } else if (key === "cello" && stopped.length > 1) {
            if (spread === 4) {
                severity = Math.max(severity, 1);
                reasons.push("Extended cello frame: four semitones rather than the normal three.");
            } else if (spread >= 5) {
                thumb = true;
                severity = Math.max(severity, 1);
                if (minFret < 5 || spread > 7) severity = 2;
                reasons.push("Cello " + (interval === 12 ? "octave" : "wide double stop")
                    + " requires thumb position; ordinary neck-position fingers cannot cover this frame."
                    + (minFret < 5 ? " This assignment is too low for the configured thumb-position model." : ""));
            } else if (minFret >= 12) {
                thumb = true; severity = Math.max(severity, 1);
                reasons.push("High cello register: thumb-position technique and careful intonation.");
            }
        } else if (key === "bass" && stopped.length > 1) {
            severity = Math.max(severity, 1);
            if (minFret >= 10) {
                thumb = true;
                if (spread > 5) severity = 2;
                reasons.push("Fingered bass stop in high/thumb position; specialist coordination required.");
            } else {
                if (spread > 2) severity = 2;
                reasons.push(spread > 2 ? "Bass neck-position hand cannot cover this stopped-note frame; use an open string or high thumb position."
                    : "Compact bass neck-position stop; possible, but demanding string spacing and pressure.");
            }
        }
        if (stopped.length && maxFret > (key === "bass" ? 9 : key === "cello" ? 12 : 16)) {
            severity = Math.max(severity, 1);
            reasons.push("High stopped-string register; orchestral reliability and tone need review.");
        }
        var distinct = {};
        for (i = 0; i < stopped.length; ++i) distinct[stopped[i]] = true;
        if (stopped.length >= 3 && Object.keys(distinct).length >= 3) {
            severity = Math.max(severity, 1);
            reasons.push("Three or more independently stopped strings: difficult finger placement and clearance.");
        }
        var stringDescription = Math.max.apply(null,assignment.strings) - Math.min.apply(null,assignment.strings) === notes.length - 1
            ? "Adjacent strings" : "Distinct plucked strings";
        if (!reasons.length) reasons.push(openCount ? stringDescription + " with " + openCount + " open string(s); compatible hand frame."
            : stringDescription + " with a standard compact hand frame (equal offsets may use a barre).");
        assignment.severity = severity;
        assignment.reasons = reasons;
        assignment.reach = reach;
        assignment.thumb = thumb;
        assignment.openCount = openCount;
        assignment.anchorLo = stopped.length ? Math.max(1, maxFret - Math.max(p.frame, spread)) : null;
        assignment.anchorHi = stopped.length ? minFret : null;
        // Prefer low difficulty, low register, and open strings, without hard-coding a single fingering.
        assignment.cost = severity * 1000 + maxFret * 0.3 + reach * 0.01 - openCount;
        return assignment;
    }

    function solveStop(key, notes, context) {
        context = context || {};
        var p = instrumentProfile(key);
        var result = {key:key, notes:notes, context:context, candidates:[], severity:0,
            physicalSeverity:0, acousticSeverity:0, transitionSeverity:0, reasons:[], label:"[OK]", chosen:null};
        if (!p || notes.length < 2 || notes.length > 4) {
            result.physicalSeverity = 2;
            result.reasons.push(!p ? "Unknown instrument." : "A stop must contain two to four sounding notes; one player has four strings.");
            return finishResult(result);
        }
        for (var i = 0; i < notes.length; ++i) {
            if (!isFinite(notes[i].pitch) || Math.floor(notes[i].pitch) !== notes[i].pitch || notes[i].pitch < p.open[0]) {
                result.physicalSeverity = 2;
                result.reasons.push("Pitch is invalid or below the lowest open string " + pitchName(p.open[0]) + ".");
                return finishResult(result);
            }
        }
        var assignments = enumerateAssignments(notes, p, context.technique !== "pizz");
        if (!assignments.length) {
            result.physicalSeverity = 2;
            var any = enumerateAssignments(notes, p, false);
            if (any.length) {
                var occupied = any[0].strings.slice().sort(function(a,b) { return a-b; });
                var middle = [];
                for (var s = occupied[0]; s <= occupied[occupied.length-1]; ++s)
                    if (occupied.indexOf(s) < 0) middle.push(pitchName(p.open[s]));
                result.reasons.push("Only non-adjacent string assignments fit ("
                    + occupied.map(function(index) { return pitchName(p.open[index]); }).join(", ")
                    + "); the intervening " + middle.join(", ") + " string(s) would also sound.");
            }
            else result.reasons.push("No distinct-string assignment within the modeled fingerboard range: notes compete for the same string or exceed its usable register.");
            return finishResult(result);
        }
        var rejected = [];
        for (i = 0; i < assignments.length; ++i) {
            var a = assessAssignment(notes, assignments[i], key);
            if (a.severity < 2) result.candidates.push(a); else rejected.push(a);
        }
        result.candidates.sort(function(a,b) { return a.cost - b.cost; });
        if (!result.candidates.length) {
            rejected.sort(function(a,b) { return a.reach - b.reach; });
            result.physicalSeverity = 2;
            result.reasons = ["Every adjacent distinct-string assignment exceeds the configured hand model."].concat(rejected[0].reasons);
        } else result.chosen = result.candidates[0];
        if (notes.length >= 3 && context.technique !== "pizz") {
            if (notes.length === 4) {
                result.acousticSeverity = 1;
                result.reasons.push("Four strings cannot be sustained by a normal bow at once: break 2+2 (or 3+1), then sustain at most two notes.");
            } else {
                var quiet = /^(p|pp|ppp|pppp|ppppp|pppppp|mp)$/.test(context.dynamic || "");
                if (quiet && context.durationQ > 1 && !context.arpeggio) {
                    result.acousticSeverity = 2;
                    result.reasons.push("Acoustically impossible AS A SUSTAINED TRIPLE: duration exceeds a quarter note at " + context.dynamic
                        + ". Break 2+1 or 1+2, or use divisi.");
                } else {
                    result.acousticSeverity = 1;
                    result.reasons.push("Normally broken 2+1 or 1+2; a normal bow sustains at most two adjacent strings."
                        + (/^ff/.test(context.dynamic || "") && context.durationQ <= 0.25
                           ? " A very brief loud three-string attack may be possible with high pressure near the bridge." : ""));
                }
            }
        } else if (context.technique === "pizz" && notes.length >= 3) {
            result.acousticSeverity = 1;
            result.reasons.push("Pizzicato multiple stop: normally rolled/plucked across the strings; no sustained bowed-chord inference.");
        }
        return finishResult(result);
    }

    function finishResult(result) {
        result.severity = Math.max(result.physicalSeverity, result.acousticSeverity,
            result.transitionSeverity, result.chosen ? result.chosen.severity : 0);
        result.label = result.severity === 2 ? "[IMPOSSIBLE]" : result.severity === 0 ? "[OK]"
            : result.notes.length >= 3 && result.acousticSeverity === 1 && result.context.technique !== "pizz"
                ? (result.notes.length === 3 ? "[BROKEN 2+1]" : "[BROKEN 2+2]") : "[DIFFICULT]";
        return result;
    }

    function transitionAssessment(previous, a, current, b) {
        var answer = {blocked:false, severity:0, cost:0, reasons:[]};
        for (var i = 0; i < previous.notes.length; ++i) {
            for (var j = 0; j < current.notes.length; ++j) {
                if (previous.notes[i].id && previous.notes[i].id === current.notes[j].id && a.strings[i] !== b.strings[j]) {
                    answer.blocked = true;
                    return answer;
                }
            }
        }
        var gap = a.anchorLo === null || b.anchorLo === null ? 0
            : Math.max(0, b.anchorLo - a.anchorHi, a.anchorLo - b.anchorHi);
        var positions = gap / 2; // Approximate diatonic positions; not a named-position fingering prescription.
        answer.cost = gap * 0.5;
        if (positions > 4 && current.context.deltaQ < 1) {
            answer.severity = 1;
            answer.cost += 300;
            answer.reasons.push("Fast transition needs more than four approximate positions (minimum frame shift "
                + gap + " semitones in " + current.context.deltaQ.toFixed(2) + " quarter-note beats).");
        }
        if (previous.notes.length === 2 && current.notes.length === 2
                && a.offsets[0] > 0 && b.offsets[0] > 0
                && a.offsets[0] === a.offsets[1] && b.offsets[0] === b.offsets[1]
                && a.offsets[0] !== b.offsets[0]
                && a.strings[0] === b.strings[0] && a.strings[1] === b.strings[1]
                && current.key !== "bass" && current.context.technique !== "pizz" && previous.context.technique !== "pizz") {
            answer.reasons.push("Parallel fingered fifths on the same string pair: a shared-finger slide risks audible glissando under a slur; separate bows or refinger.");
            if (current.context.slurred) { answer.severity = 1; answer.cost += 50; }
        }
        if (current.context.rapidRun >= 3 && current.context.tempo > 100) {
            answer.severity = Math.max(answer.severity, 1); answer.cost += 50;
            answer.reasons.push("Continuous rapid stops: three or more sixteenth-note attacks above 100 quarter-note BPM.");
        }
        return answer;
    }

    function planSequence(results) {
        // Dynamic programming over ALL viable string assignments; preserve held/tied strings.
        var stages = [], previous = null;
        function commit() {
            if (!stages.length) return;
            var last = stages[stages.length - 1], best = 0;
            for (var k = 1; k < last.costs.length; ++k) if (last.costs[k] < last.costs[best]) best = k;
            for (var s = stages.length - 1; s >= 0; --s) {
                var stage = stages[s], r = stage.result;
                r.chosen = r.candidates[best];
                var tr = stage.transitions[best];
                if (tr) { r.transitionSeverity = Math.max(r.transitionSeverity, tr.severity); r.reasons = r.reasons.concat(tr.reasons); }
                finishResult(r);
                best = stage.parents[best];
            }
            stages = []; previous = null;
        }
        for (var i = 0; i < results.length; ++i) {
            var r = results[i];
            if (!r.candidates.length) { commit(); continue; }
            // A rest, intervening single note, or instrument change starts a fresh transition chain.
            if (previous && (r.context.chain !== previous.result.context.chain || r.key !== previous.result.key)) commit();
            var costs = [], parents = [], transitions = [], possible = false;
            for (var b = 0; b < r.candidates.length; ++b) {
                var cost = Infinity, parent = -1, transition = null;
                if (!previous) cost = r.candidates[b].cost;
                else for (var a = 0; a < previous.result.candidates.length; ++a) {
                    var tr = transitionAssessment(previous.result, previous.result.candidates[a], r, r.candidates[b]);
                    if (tr.blocked) continue;
                    var value = previous.costs[a] + r.candidates[b].cost + tr.cost;
                    if (value < cost) { cost = value; parent = a; transition = tr; }
                }
                costs.push(cost); parents.push(parent); transitions.push(transition);
                if (isFinite(cost)) possible = true;
            }
            if (!possible) {
                commit();
                r.transitionSeverity = 2;
                r.reasons.push("No assignment preserves the physical strings of all held/tied notes through this transition. Release or rewrite a voice.");
                costs = r.candidates.map(function(c) { return c.cost; });
                parents = r.candidates.map(function() { return -1; });
                transitions = r.candidates.map(function() { return null; });
            }
            previous = {result:r, costs:costs, parents:parents, transitions:transitions};
            stages.push(previous);
        }
        commit();
        return results;
    }

    function assignmentDescription(result) {
        if (!result.chosen) return "";
        var p = instrumentProfile(result.key), a = result.chosen, descriptions = [];
        for (var i = 0; i < result.notes.length; ++i)
            descriptions.push(pitchName(result.notes[i].pitch, result.notes[i].tpc) + " on "
                + pitchName(p.open[a.strings[i]]) + " string " + (a.offsets[i] ? "(+" + a.offsets[i] + " semitones)" : "(open)"));
        return descriptions.join("; ") + ".";
    }

    function groupSoundingEvents(events, begin, end, acceptTick) {
        // Onset sweep includes sounding notes from earlier voices and from before a range selection.
        events.sort(function(a,b) { return a.tick - b.tick || a.track - b.track; });
        var active = [], groups = [], at = 0, chain = 0, lastEnd = -1;
        while (at < events.length) {
            var tick = events[at].tick, batch = [];
            active = active.filter(function(n) { return n.end > tick; });
            while (at < events.length && events[at].tick === tick) batch.push(events[at++]);
            for (var i = 0; i < batch.length; ++i) {
                for (var n = 0; n < batch[i].notes.length; ++n) {
                    var incoming = batch[i].notes[n];
                    // A tied continuation updates the written note reference, retaining its physical identity.
                    active = active.filter(function(held) { return !incoming.id || held.id !== incoming.id; });
                    active.push(incoming);
                }
            }
            if (tick >= end) break;
            if (active.length < 2) { ++chain; lastEnd = -1; continue; }
            if (tick > lastEnd) ++chain;
            var groupEnd = Math.min.apply(null, active.map(function(n) { return n.end; }));
            if (tick >= begin && (!acceptTick || acceptTick(tick))) {
                var notes = active.slice().sort(function(a,b) { return a.pitch - b.pitch || String(a.id).localeCompare(String(b.id)); });
                groups.push({tick:tick, end:groupEnd, notes:notes, event:batch[0], chain:chain});
            } else if (tick >= begin) ++chain;
            lastEnd = groupEnd;
        }
        return groups;
    }
    // END SOLVER

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        instrumentProfile: instrumentProfile,
        pitchName: pitchName,
        detectInstrument: detectInstrument,
        solveStop: solveStop,
        planSequence: planSequence,
        assignmentDescription: assignmentDescription
    };
}
