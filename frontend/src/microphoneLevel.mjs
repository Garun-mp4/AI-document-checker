const RMS_FLOOR = 0.012
const RMS_CEILING = 0.2

export function rmsFromTimeDomainData(samples) {
  if (!samples?.length) return 0

  let sumSquares = 0
  for (let index = 0; index < samples.length; index += 1) {
    const centered = (Number(samples[index]) - 128) / 128
    sumSquares += centered * centered
  }

  return Math.sqrt(sumSquares / samples.length)
}

export function normalizeRms(rms) {
  if (!Number.isFinite(rms) || rms <= RMS_FLOOR) return 0
  const normalized = (rms - RMS_FLOOR) / (RMS_CEILING - RMS_FLOOR)
  return Math.min(1, Math.max(0, normalized) ** 0.7)
}

export async function openLocalMicrophoneAnalyzer(environment = {}) {
  const mediaDevices = environment.mediaDevices ?? globalThis.navigator?.mediaDevices
  const AudioContextConstructor = environment.AudioContext
    ?? globalThis.AudioContext
    ?? globalThis.webkitAudioContext

  if (typeof mediaDevices?.getUserMedia !== 'function') {
    throw new Error('Local microphone capture is unavailable.')
  }
  if (typeof AudioContextConstructor !== 'function') {
    throw new Error('Local audio analysis is unavailable.')
  }

  let stream = null
  let audioContext = null
  let source = null
  let analyser = null
  let disposed = false

  const dispose = async () => {
    if (disposed) return
    disposed = true

    for (const track of stream?.getTracks?.() ?? []) {
      try {
        track.stop()
      } catch {
        // Continue releasing the remaining local audio resources.
      }
    }
    try {
      source?.disconnect()
    } catch {
      // The source may already be disconnected by the browser.
    }
    try {
      analyser?.disconnect()
    } catch {
      // The analyser may already be disconnected by the browser.
    }
    if (audioContext && audioContext.state !== 'closed') {
      try {
        await audioContext.close()
      } catch {
        // The browser may have closed the context during page teardown.
      }
    }
  }

  try {
    stream = await mediaDevices.getUserMedia({ audio: true, video: false })
    audioContext = new AudioContextConstructor()
    source = audioContext.createMediaStreamSource(stream)
    analyser = audioContext.createAnalyser()
    analyser.fftSize = 512
    analyser.smoothingTimeConstant = 0.65
    source.connect(analyser)

    if (audioContext.state === 'suspended') await audioContext.resume()

    return { analyser, dispose }
  } catch (error) {
    await dispose()
    throw error
  }
}
