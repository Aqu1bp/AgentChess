// Chess move sounds
const audioCache: Record<string, HTMLAudioElement> = {};

function getAudio(name: string): HTMLAudioElement {
  if (!audioCache[name]) {
    audioCache[name] = new Audio(`/sounds/${name}.mp3`);
    audioCache[name].volume = 0.5;
  }
  return audioCache[name];
}

export function playMoveSound(): void {
  try {
    const audio = getAudio('move');
    audio.currentTime = 0;
    audio.play().catch(() => {});
  } catch {}
}

export function playCaptureSound(): void {
  try {
    const audio = getAudio('capture');
    audio.currentTime = 0;
    audio.play().catch(() => {});
  } catch {}
}

export function playCheckSound(): void {
  try {
    const audio = getAudio('check');
    audio.currentTime = 0;
    audio.play().catch(() => {});
  } catch {}
}

export function playGameEndSound(): void {
  try {
    const audio = getAudio('game-end');
    audio.currentTime = 0;
    audio.play().catch(() => {});
  } catch {}
}

// Training mode sounds
export function playCorrectSound(): void {
  try {
    // Use game-end as a pleasant success sound, or create a dedicated one
    const audio = getAudio('correct');
    audio.currentTime = 0;
    audio.volume = 0.4;
    audio.play().catch(() => {
      // Fallback to move sound
      playMoveSound();
    });
  } catch {
    playMoveSound();
  }
}

export function playWrongSound(): void {
  try {
    const audio = getAudio('wrong');
    audio.currentTime = 0;
    audio.volume = 0.3;
    audio.play().catch(() => {
      // No fallback for wrong sound - silence is fine
    });
  } catch {}
}

export function playHintSound(): void {
  try {
    const audio = getAudio('hint');
    audio.currentTime = 0;
    audio.volume = 0.25;
    audio.play().catch(() => {
      // No fallback needed
    });
  } catch {}
}

export function playSessionCompleteSound(): void {
  try {
    const audio = getAudio('session-complete');
    audio.currentTime = 0;
    audio.volume = 0.5;
    audio.play().catch(() => {
      // Fallback to game-end sound
      playGameEndSound();
    });
  } catch {
    playGameEndSound();
  }
}
