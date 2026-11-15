// The browser records compressed audio; the sample is sent as a plain WAV, which every voice cloner reads

export async function toWav(recording) {
  const context = new AudioContext();
  try {
    const buffer = await context.decodeAudioData(await recording.arrayBuffer());
    return encodeWav(buffer.getChannelData(0), buffer.sampleRate);
  } finally {
    void context.close();
  }
}

/** Mono 16-bit PCM WAV from float samples (-1..1). */
export function encodeWav(samples, sampleRate) {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVEfmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s)) * 0x7fff, true));
  return new Blob([view], { type: "audio/wav" });
}
