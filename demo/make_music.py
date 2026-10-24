"""A royalty-free placeholder music bed and sound effects for the rough cut, synthesized here (no samples, no licences).

The bed is a slow, soft pad (four chords, 8 s each, looped) with a light pulse; the effects are short tones.
Swap demo/edit/music/bed.wav for a licensed track in the final edit: make_rough_cut.py ducks whatever is there
under the narration.

Also: a typhoon (wind, rain, gusts) for the opening, a two-tone cabin chime for texts and email arriving, and the
jet for the ending, cut from a real recording when demo/edit/music/src/inspectorj_boeing_flyby.wav is there:
"Airplane, Boeing, Flyby, Right to Left, A.wav" by InspectorJ (www.jshaw.co.uk) of Freesound.org, CC BY 4.0
(https://commons.wikimedia.org/wiki/File:428086_inspectorj_airplane-boeing-flyby-right-to-left-a.wav) —
the video's description must credit it.

Run: python3 demo/make_music.py [seconds]   (system python with numpy; ffmpeg for the jet)
Out: demo/edit/music/{bed,ding,chime,click,thud,whoosh,storm,jet}.wav
"""
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent / "edit" / "music"


def save(name, x):
    x = np.clip(x, -1, 1)
    st = np.stack([x, x], 1) if x.ndim == 1 else x
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes((st * 32767).astype(np.int16).tobytes())


def hz(note):  # MIDI note number -> Hz
    return 440.0 * 2 ** ((note - 69) / 12)


def lowpass(x, cutoff):
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):  # one-pole; fine for a few minutes of audio
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def pad(seconds):
    chords = [[48, 55, 59, 64, 67], [45, 52, 55, 60, 64], [41, 48, 52, 57, 60], [43, 50, 55, 59, 62]]  # Cmaj7 Am7 Fmaj7 G6
    bar = 8.0
    t = np.arange(int(seconds * SR)) / SR
    out = np.zeros_like(t)
    for k in range(int(np.ceil(seconds / bar)) + 1):
        notes = chords[k % len(chords)]
        s0, s1 = int(k * bar * SR), int(min((k + 1) * bar + 1.5, seconds) * SR)  # chords overlap 1.5 s
        if s0 >= len(t):
            break
        tt = t[s0:s1] - k * bar
        env = np.minimum(1, tt / 1.8) * np.minimum(1, np.maximum(0, (bar + 1.5 - tt) / 1.5))
        for n in notes:
            f = hz(n)
            out[s0:s1] += env * (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * 2 * f * tt + 0.7)) / len(notes)
    pulse = 0.5 + 0.5 * np.sin(2 * np.pi * (96 / 60 / 2) * t) ** 8  # a gentle swell twice a bar
    bass = np.zeros_like(t)
    for k in range(int(np.ceil(seconds / bar))):
        root = chords[k % len(chords)][0] - 12
        s0, s1 = int(k * bar * SR), int(min((k + 1) * bar, seconds) * SR)
        tt = t[s0:s1] - k * bar
        bass[s0:s1] = np.sin(2 * np.pi * hz(root) * tt) * np.minimum(1, tt / 0.5) * np.minimum(1, (bar - tt) / 0.5)
    x = lowpass(out * (0.75 + 0.25 * pulse) + 0.35 * bass, 1800)
    fade = np.minimum(1, t / 2.5) * np.minimum(1, (seconds - t) / 4.0)
    return 0.5 * x / np.max(np.abs(x)) * fade


def biquad_bp(x, f0, q):
    """RBJ band-pass (constant peak gain); f0 may be an array (a sweeping centre)."""
    f0 = np.broadcast_to(np.asarray(f0, float), x.shape)
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(len(x)):
        w = 2 * np.pi * f0[i] / SR
        al = np.sin(w) / (2 * q)
        a0 = 1 + al
        b0, a1, a2 = al / a0, -2 * np.cos(w) / a0, (1 - al) / a0
        yi = b0 * x[i] - b0 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, x[i], y1, yi
        y[i] = yi
    return y


def storm(seconds=5.0, seed=7):
    """Typhoon wind: a low roar, gusts that swell and fall, a whistle riding the gusts, rain hiss; wide stereo."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    slow = lowpass(rng.normal(size=n), 0.6)
    gust = 0.55 + 0.45 * np.tanh(3 * (slow / (np.std(slow) + 1e-9)) * 0.5 + np.sin(2 * np.pi * 0.23 * t))
    out = []
    for ch in range(2):
        white = rng.normal(size=n)
        brown = np.cumsum(white)
        brown = brown - lowpass(brown, 8)                       # drift removed
        roar = lowpass(brown, 380) - lowpass(brown, 60)          # the body of the wind
        rumble = lowpass(brown, 70)
        whistle = biquad_bp(white, 650 + 520 * gust + 40 * ch, 9.0)
        rain = white - lowpass(white, 2500)
        x = (roar / np.std(roar) * 0.55 * gust + rumble / np.std(rumble) * 0.35
             + whistle / np.std(whistle) * 0.18 * gust ** 2 + rain / np.std(rain) * 0.06)
        out.append(x)
    x = np.stack(out, 1)
    # full for the first second, then down to a bed under the narration (about -12 dB by 2 s), gone by the end
    env = np.minimum(1, t / 0.35) * np.where(t < 1.0, 1.0, (0.07 + 0.93 * np.exp(-(t - 1.0) / 0.25)) * np.clip((seconds - t) / (seconds - 2.0), 0, 1))
    x *= env[:, None]
    return 0.8 * x / np.max(np.abs(x))


def chime(level=0.55):
    """The two-tone cabin chime (high, then low): soft bell partials and a short room tail."""
    def bell(f, dur):
        t = np.arange(int(dur * SR)) / SR
        x = sum(a * np.sin(2 * np.pi * f * m * t) * np.exp(-t / d) for m, a, d in ((1, 1, 0.45), (2.0, 0.25, 0.2), (3.01, 0.08, 0.12)))
        return x * np.minimum(1, t / 0.006)
    hi, lo = bell(1318.5, 1.0), bell(1046.5, 1.3)  # a short tail: it must not sit on the next sentence
    x = np.zeros(int(2.0 * SR))
    x[:len(hi)] += hi
    o = int(0.62 * SR)
    x[o:o + len(lo)] += lo[:len(x) - o]
    wet = np.zeros_like(x)
    for dly, g in ((0.029, 0.35), (0.043, 0.28), (0.067, 0.2), (0.11, 0.12)):  # a small room
        k = int(dly * SR)
        wet[k:] += g * x[:-k]
    x = x + lowpass(wet, 3000)
    return level * x / np.max(np.abs(x))


def jet(src):
    """The flyby, cut around its loudest seconds (it peaks about 14-16 s in), faded in and out."""
    import subprocess
    out = OUT / "jet.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "6", "-t", "16", "-i", str(src),
                    "-af", "afade=t=in:d=4,afade=t=out:st=12:d=4,loudnorm=I=-20:TP=-3,aresample=44100", "-ac", "2", str(out)], check=True)


def tone(freqs, dur, decay, level=0.6):
    t = np.arange(int(dur * SR)) / SR
    x = sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs)
    return level * x * np.exp(-t / decay) * np.minimum(1, t / 0.004)


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 180
    OUT.mkdir(parents=True, exist_ok=True)
    save("bed", pad(seconds))
    save("ding", np.concatenate([tone([1318.5, 2637], 0.18, 0.08), tone([1760, 3520], 0.5, 0.18)]))  # a text arrives
    save("click", tone([2200, 3300], 0.05, 0.012, 0.5))                                                # a tap
    save("thud", tone([70, 140], 0.6, 0.18, 0.9) + np.pad(tone([1200], 0.08, 0.02, 0.2), (0, int(0.52 * SR))))  # blocked
    t = np.arange(int(0.45 * SR)) / SR
    save("whoosh", 0.25 * lowpass(np.random.default_rng(1).normal(size=t.size), 900) * np.sin(np.pi * t / 0.45) ** 2 * 6)  # a page opens
    save("storm", storm())
    save("chime", chime())
    src = OUT / "src" / "inspectorj_boeing_flyby.wav"
    if src.exists():
        jet(src)
    print("music ->", OUT)


if __name__ == "__main__":
    main()
