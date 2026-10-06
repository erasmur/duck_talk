/**
 * Ears without Gemini: a local whisper.cpp server (`whisper-server`, already running for
 * dictate on 127.0.0.1:8178) behind a small energy-based voice activity detector.
 * Same contract as ears.ts, selected with DUCK_BACKEND=local.
 *
 * Whisper does not stream, so the two signals are made here:
 *   onPartial  once BARGE_MS of speech have been heard ("…"), which is all a barge-in needs
 *              (earlier, a cough or breath over the reply cut Claude off); with
 *              DUCK_PARTIALS=1 also the utterance so far, about once a second. Off by
 *              default: whisper-server takes one request at a time (~2.5 s each on an
 *              M-series Mac), so partials queue up in front of the final
 *   onFinal    after SILENCE_MS of quiet, the whole utterance transcribed once more
 *
 * The detector tracks the noise floor and calls a frame speech when it stands clearly
 * above it, so a hissing wireless mic does not count as talking. Whisper invents text
 * for noise ("Untertitel im Auftrag des ZDF", "Thank you."), so very short utterances
 * and those known phrases are dropped.
 *
 *   node ears-local.ts --file turn.wav      feed a 16 kHz mono recording, print events
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { wav } from './clips.ts';
import type { Ears, EarsCallbacks } from './ears.ts';

const URL_ = process.env['WHISPER_URL'] ?? 'http://127.0.0.1:8178/inference';
const LANGUAGE = process.env['DUCK_LANG'] ?? 'auto';
const SILENCE_MS = Number(process.env['SILENCE_MS'] ?? 1000); // quiet speech dips between words; 800 cut sentences
const JOIN_MS = Number(process.env['JOIN_MS'] ?? 1500); // a final this soon after the last one continues it
const PARTIALS = process.env['DUCK_PARTIALS'] === '1';
const PARTIAL_EVERY_MS = 1200;
const MIN_SPEECH_MS = 350;
const BARGE_MS = Number(process.env['BARGE_MS'] ?? 500);
const FRAME = 320; // 20 ms at 16 kHz
const PREROLL_FRAMES = 15; // 300 ms before the detector fired belong to the utterance
const START_FRAMES = 3;
const HALLUCINATIONS = [
  /untertitel/i, /^thank you\.?$/i, /^thanks for watching/i, /^vielen dank\.?$/i, /^\[.*\]$/, /^\(.*\)$/,
  /^\.+$/, /^you$/i, /copyright/i,
];

async function transcribe(pcm: Buffer): Promise<string> {
  const form = new FormData();
  form.append('file', new Blob([wav(pcm)], { type: 'audio/wav' }), 'utterance.wav');
  form.append('response_format', 'json');
  form.append('temperature', '0');
  form.append('language', LANGUAGE);
  const res = await fetch(URL_, { method: 'POST', body: form });
  if (!res.ok) throw new Error(`whisper-server answered ${res.status}`);
  const body = (await res.json()) as { text?: string };
  const text = (body.text ?? '').replace(/\s+/g, ' ').trim();
  return HALLUCINATIONS.some((h) => h.test(text)) ? '' : text;
}

function rms(frame: Buffer): number {
  let sum = 0;
  for (let i = 0; i + 1 < frame.length; i += 2) {
    const s = frame.readInt16LE(i);
    sum += s * s;
  }
  return Math.sqrt(sum / (frame.length / 2));
}

export async function openEarsLocal(cb: EarsCallbacks): Promise<Ears> {
  const log = cb.log ?? (() => {});
  let pending = Buffer.alloc(0); // bytes not yet a whole frame
  const preroll: Buffer[] = [];
  let utterance: Buffer[] = [];
  let speaking = false;
  let loudRun = 0;
  let quietMs = 0;
  let speechMs = 0;
  let noise = 200; // running noise floor, in RMS
  let lastFinalAt = 0;
  let lastFinalText = '';
  let continuing = false;
  let partialBusy = false;
  let lastPartialAt = 0;
  let announced = false; // this utterance has sent its first partial
  let closed = false;
  let chain: Promise<void> = Promise.resolve(); // finals in order

  const stitch = (text: string) => (continuing && lastFinalText ? `${lastFinalText} ${text}` : text);

  function startUtterance(): void {
    speaking = true;
    quietMs = 0;
    speechMs = 0;
    utterance = preroll.splice(0);
    continuing = Date.now() - lastFinalAt < JOIN_MS;
    lastPartialAt = Date.now();
    announced = false;
  }

  function partial(): void {
    if (partialBusy || closed) return;
    partialBusy = true;
    lastPartialAt = Date.now();
    const audio = Buffer.concat(utterance);
    transcribe(audio)
      .then((text) => { if (text && speaking && !closed) cb.onPartial(stitch(text), continuing); })
      .catch((e) => log(`ears-local: partial failed: ${e}`))
      .finally(() => { partialBusy = false; });
  }

  function endUtterance(): void {
    speaking = false;
    const audio = Buffer.concat(utterance);
    const long = speechMs >= MIN_SPEECH_MS;
    // a short utterance never announced itself; announce it now so the final has its partial
    if (long && !announced) cb.onPartial(stitch('…'), continuing);
    const joined = continuing;
    utterance = [];
    if (!long) return;
    chain = chain.then(async () => {
      try {
        const text = await transcribe(audio);
        if (!text || closed) return;
        const full = joined && lastFinalText ? `${lastFinalText} ${text}` : text;
        lastFinalText = full;
        lastFinalAt = Date.now();
        cb.onFinal(full, audio);
      } catch (e) {
        log(`ears-local: transcription failed: ${e}`);
      }
    });
  }

  function frame(f: Buffer): void {
    const level = rms(f);
    // the DJI stream is quiet: speech peaks around 300-600 RMS over a floor of 1-6
    const loud = level > Math.max(noise * 5, 40);
    // the floor follows quiet frames quickly and loud ones barely, so speech does not raise it
    noise = loud ? noise * 0.999 + level * 0.001 : noise * 0.95 + level * 0.05;
    if (!speaking) {
      preroll.push(f);
      if (preroll.length > PREROLL_FRAMES) preroll.shift();
      loudRun = loud ? loudRun + 1 : 0;
      if (loudRun >= START_FRAMES) startUtterance();
      return;
    }
    utterance.push(f);
    if (loud) {
      quietMs = 0;
      speechMs += 20;
      if (!announced && speechMs >= BARGE_MS) {
        announced = true;
        cb.onPartial(stitch('…'), continuing);
      }
    } else {
      quietMs += 20;
    }
    if (quietMs >= SILENCE_MS) return endUtterance();
    if (PARTIALS && Date.now() - lastPartialAt >= PARTIAL_EVERY_MS) partial();
  }

  log(`ears-local: whisper at ${URL_}, language ${LANGUAGE}, silence ${SILENCE_MS} ms`);
  return {
    send(pcm: Buffer) {
      if (closed) return;
      pending = Buffer.concat([pending, pcm]);
      const bytes = FRAME * 2;
      while (pending.length >= bytes) {
        frame(pending.subarray(0, bytes));
        pending = pending.subarray(bytes);
      }
    },
    close() {
      closed = true;
    },
    cut() {
      lastFinalText = '';
      lastFinalAt = 0;
    },
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const at = process.argv.indexOf('--file');
  if (at < 0) {
    console.error('usage: node ears-local.ts --file turn.wav');
    process.exit(2);
  }
  const pcm = readFileSync(process.argv[at + 1]!).subarray(44);
  const t0 = Date.now();
  const ears = await openEarsLocal({
    log: console.error,
    onPartial: (t) => console.log(`${Date.now() - t0}ms partial: ${t}`),
    onFinal: (t) => console.log(`${Date.now() - t0}ms FINAL: ${t}`),
  });
  // real time, as a microphone would deliver it
  for (let i = 0; i < pcm.length; i += 640) {
    ears.send(pcm.subarray(i, i + 640));
    await new Promise((r) => setTimeout(r, 20));
  }
  ears.send(Buffer.alloc(32000 * 2)); // two seconds of silence so the last utterance ends
  await new Promise((r) => setTimeout(r, 6000));
  ears.close();
}
