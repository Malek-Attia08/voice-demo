// The sentence the speaker reads aloud before their voice is cloned, and the check that they did.
// The check runs on the device only.

// A recogniser misses or garbles a word now and then: this share of the sentence's words must be heard.
const REQUIRED_SHARE = 0.8;

function wordsOf(text) {
  return text
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);
}

/** Whether `heard` is the `prompt` read aloud: case, punctuation and accents do not matter, a missed word or two does not either. */
export function matchesPrompt(prompt, heard) {
  const wanted = wordsOf(prompt);
  const available = new Map();
  for (const word of wordsOf(heard)) available.set(word, (available.get(word) ?? 0) + 1);
  let found = 0;
  for (const word of wanted) {
    const left = available.get(word) ?? 0;
    if (left > 0) {
      found += 1;
      available.set(word, left - 1);
    }
  }
  return wanted.length > 0 && found / wanted.length >= REQUIRED_SHARE;
}

/** The sentence the speaker reads aloud before their voice is cloned. */
export const readingPrompt = (speaker) =>
  `Hello, my name is ${(speaker || "").trim() || "…"}. This is my own voice, and I agree that this demo may store it and speak with it.`;

/**
 * Listens for the speaker on the device itself and reports everything heard so far. Returns how to stop, or null
 * when this browser cannot promise that the audio stays on the device: it is then never asked to recognise speech,
 * because most browsers would send the voice to a server to do it.
 */
export async function listenOnDevice(lang, onHeard) {
  const Recogniser = window.SpeechRecognition ?? window.webkitSpeechRecognition;
  if (!Recogniser || typeof Recogniser.available !== "function") return null;
  try {
    if ((await Recogniser.available({ langs: [lang], processLocally: true })) !== "available") return null;
  } catch {
    return null;
  }
  const recognition = new Recogniser();
  recognition.lang = lang;
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.processLocally = true;
  recognition.onresult = (event) => onHeard(Array.from(event.results, (result) => result[0]?.transcript ?? "").join(" "));
  recognition.onerror = () => {};
  recognition.start();
  return () => recognition.stop();
}
