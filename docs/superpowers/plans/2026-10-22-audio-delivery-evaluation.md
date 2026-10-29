# Audio Delivery Evaluation Implementation Plan

> **For agentic workers:** Use this plan task-by-task in the current session. Preserve the user's existing uncommitted interview records. Do not push audio or records to any remote repository.

**Goal:** Separate answer-content scoring from audio-delivery scoring and produce candidate-only speaker, pace, pause, loudness, and prosody evidence for future interview sessions.

**Architecture:** Keep faster-whisper for Korean transcription and timestamps. Add an optional pyannote diarization layer that labels audio turns, with an explicit candidate-speaker selection when transcript labels cannot map diarized IDs safely. Add a focused local signal-analysis module for candidate-only speech rate, pauses, RMS/clipping, and F0 summaries; retain these as evidence with limitations, not automatic grades. Update the rubric and evaluation templates so text scores assess answer content, voice scores assess delivery, and the total remains a 50:50 rounded mean.

**Tech Stack:** Python 3 in `.audio-venv`, faster-whisper/PyAV, optional `pyannote.audio` with a Hugging Face token/model agreement for model download, NumPy/PyAV signal access, optional `librosa` for F0 extraction, Markdown rubric/skill/templates.

**Spec:** `docs/superpowers/specs/2026-10-22-audio-delivery-evaluation-design.md`

## Global Constraints

- Audio processing remains local by default; the original recording is preserved.
- Store Hugging Face credentials only in environment variables; never write tokens into code, JSON, logs, or records.
- Do not infer personality, emotion, health, confidence, accent origin, or hiring suitability from voice signals.
- Text scores evaluate answer content only; voice scores evaluate delivery only.
- Final total is the rounded arithmetic mean of valid text and voice scores; if candidate audio cannot be isolated, withhold voice and total scores.
- Keep existing interview-session scores unchanged; apply the new rubric only to future sessions unless separately requested.
- Treat ASR, diarization, pitch, loudness, and pause measurements as evidence, not universal quality thresholds.

## Review Focus

1. Short candidate turns and overlapping interviewer speech can be assigned to the wrong diarized speaker; require explicit candidate-speaker mapping and disclose ambiguity.
2. A transcript speaker label may not align exactly with audio diarization boundaries; align by interval overlap and avoid silently forcing low-confidence assignments.
3. M4A decoding may produce stereo or unusual sample rates; compute measurements on correctly decoded PCM and document channel handling.
4. Silence, breath noise, clipped samples, and background noise can distort pace/RMS/F0; mark a metric unavailable when the signal cannot support it.
5. First-time diarization setup may lack an accepted model agreement, network, token, or compatible optional dependency; provide actionable errors and a documented ASR-only fallback without candidate scoring.

## Files to Change

- `scripts/analyze_voice.py` — retain transcription behavior; add optional diarization invocation and candidate-speaker selection, then write versioned evidence JSON.
- `scripts/voice_metrics.py` — create interval mapping, PCM decode helpers, candidate-only pace/pause/RMS/clipping/F0 measurements, confidence and unavailable states.
- `requirements-audio-diarization.txt` — create optional pyannote/librosa dependencies separate from base transcription requirements.
- `references/voice-scoring-rubric.md` — replace v1.0 categories with v2.0: intelligibility 30, pace/stability 20, breathing/pauses/disfluency 20, loudness/signal stability 15, prosody 15.
- `references/audio-analysis-workflow.md` — update local setup, model agreement/token environment, first diarization output, candidate ID selection, fallbacks, and limitations.
- `references/output-format.md` — make text-only and audio-only score boundaries explicit and use v2.0 audio labels.
- `records/templates/evaluation-template.md` — match the new score dimensions and review section.
- `SKILL.md` — define the separated scoring policy and when final score must be withheld.
- `README.md` — document both installation paths and the revised audio assessment limitations.

## Task 1: Candidate Speaker Mapping and Diarization Evidence

**Files:**
- Modify: `scripts/analyze_voice.py`
- Create: `requirements-audio-diarization.txt`
- Modify: `references/audio-analysis-workflow.md`

**Interfaces:**
- `analyze_voice.py` accepts `--diarize`, optional `--num-speakers`, and `--candidate-speaker SPEAKER_XX`; model access reads `HF_TOKEN` from the environment.
- Evidence JSON schema version becomes `2`; it contains `speaker_turns`, `candidate_speaker`, `speaker_mapping_method`, and `speaker_mapping_confidence`.
- If diarization runs but candidate speaker is omitted, write per-speaker turns and set candidate-specific analysis status to `candidate_speaker_unconfirmed`; never guess from label order.
- If diarization dependency/token/model access is unavailable, preserve ASR output, record the failure and an install/configuration hint, and omit candidate-specific scoring fields.

- [x] Add optional dependency installation and secure token/acceptance instructions to the audio workflow.
- [x] Add diarization CLI arguments and call pyannote only when `--diarize` is requested.
- [x] Align diarized speaker turns to ASR segments using maximum temporal overlap; record unmapped/ambiguous segments explicitly.
- [x] Add a candidate selection option that filters the aligned turns by an explicit diarized speaker ID.
- [x] Extend evidence JSON with schema version, diarization model/version, speaker turn intervals, selected candidate, mapping method, and errors/limitations.
- [x] Preserve the existing ASR-only invocation and output when `--diarize` is not requested.
- [x] Document the two-stage flow: run diarization, inspect speaker IDs/turns, rerun/select candidate speaker.

## Task 2: Candidate-Only Acoustic Metrics

**Files:**
- Create: `scripts/voice_metrics.py`
- Modify: `scripts/analyze_voice.py`
- Modify: `requirements-audio-diarization.txt`
- Modify: `references/audio-analysis-workflow.md`

**Interfaces:**
- `analyze_candidate_audio(audio_path: Path, segments: list[dict], candidate_speaker: str, sample_rate: int | None = None) -> dict` returns `status`, `candidate_speech_seconds`, `candidate_character_count`, `speech_rate_chars_per_minute`, `pause_summary`, `loudness`, `clipping`, `prosody`, and `limitations`.
- `segments` use `{start: float, end: float, speaker: str, text: str}` with times in seconds.
- Metrics are `unavailable` when candidate segments are absent or signal quality makes them unreliable; do not substitute whole-call mixed-speaker values.
- Pause thresholds for reporting are 0.75, 1.5, and 3.0 seconds; these are descriptive bins, not automatic grade cutoffs.

- [x] Define deterministic interval merge/clip helpers for candidate turns and gaps.
- [x] Decode PCM from the existing PyAV dependency, downmix consistently for measurements, and retain original channel count/sample rate in metadata.
- [x] Calculate candidate-only character pace from ASR text and candidate speech duration; include segment-level pace distribution where enough speech exists.
- [x] Calculate pauses only between candidate turns within a response; do not count interviewer turn gaps as candidate hesitation.
- [x] Calculate relative RMS/dBFS distribution and clipping ratio; label absolute volume and noise interpretation as recording-dependent.
- [x] Calculate voiced F0 summary with `librosa.pyin` and store limited distribution statistics, not emotion/voice-quality labels.
- [x] Store metric availability and the exact segments used in evidence JSON.
- [x] Keep breath-sound detection out of the first implementation; report pause/breath spacing and require listening review for actual breath events.

## Task 3: Independent Rubrics and Evaluation Output

**Files:**
- Modify: `references/voice-scoring-rubric.md`
- Modify: `references/output-format.md`
- Modify: `records/templates/evaluation-template.md`
- Modify: `SKILL.md`

**Interfaces:**
- Text rubric stays question-based v2.0 and excludes delivery features.
- Voice rubric becomes v2.0 with category caps 30/20/20/15/15 (total 100).
- Required voice review fields: method/confidence, observed delivery strengths, actionable delivery changes, timestamped evidence, listening/automation limitations.
- `voice_score` and `total_score` are withheld when candidate identity or evidence coverage is insufficient.

- [x] Replace old voice categories in the scoring rubric with the agreed v2.0 categories and evidence requirements.
- [x] State that transcript content correctness is excluded from voice scores and delivery traits are excluded from text scores.
- [x] Update the output format and evaluation template to show separate scores/reviews and a 50:50 mean.
- [x] Update the evaluation procedure to distinguish directly heard evidence from ASR/acoustic-feature-only provisional evidence.
- [x] Preserve fair-scoring limits: accent/voice timbre/gender/age/personality/emotion are not scored; gear/noise defects are not candidate faults.

## Task 4: User Setup and Future Session Workflow

**Files:**
- Modify: `README.md`
- Modify: `references/audio-analysis-workflow.md`
- Modify: `SKILL.md`

**Interfaces:**
- Base ASR install remains `requirements-audio.txt`.
- Optional diarization/prosody install uses `requirements-audio-diarization.txt`.
- Document `HF_TOKEN` as an environment variable and never accept it as a command-line literal or store it in a session record.
- Voice review lists measured metrics and listening judgments separately; candidate-only rate/pause fields must state candidate mapping confidence.

- [x] Add exact Windows PowerShell setup commands for optional packages and process-scoped `HF_TOKEN` configuration.
- [x] Add example commands to produce speaker turns and select the candidate diarization ID.
- [x] Explain model terms, first download, local inference, ASR-only fallback, and candidate score withholding.
- [x] Update README overview and limitations to reflect audio-delivery v2.0.
- [x] Review documentation paths, schema terms, and score caps for consistency.

## Execution Notes

- Work in the existing checkout because it contains interview records added during the conversation that are not yet committed and therefore would not appear in a new worktree. Do not stage or commit those session files as part of feature implementation.
- The initial model package may be too large or unavailable in the sandbox. Implement dependency checks and actionable fallback behavior without embedding credentials or model weights in the repository.
- After the user reviews this plan, implement tasks in order in the current session. Do not push to GitHub unless the user asks.
